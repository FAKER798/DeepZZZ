"""Pinyin-based keyword matching for ASR transcripts.

SenseVoice often writes a homophone or near-homophone ("宝宝未奶粉" for
"宝宝喂奶粉", "尿不时" for "尿不湿"). Keywords are therefore matched on
pronunciation instead of characters:

* 2-character keywords: every changed character must be an exact homophone
  *including the tone* (未 wèi = 喂 wèi, but 违 wéi != 喂 wèi), at most one
  character may differ, and a few everyday homophone words are excluded
  (亲为/评为/省了/尿不).
* 3+ character keywords: one character may differ; it may be a homophone in
  any tone or a fuzzy-pinyin neighbour (zh/z, ch/c, sh/s, n/l, l/r, f/h,
  an/ang, en/eng, in/ing, ...).

The character -> pinyin table comes from fcitx5's pinyin helper data
(py_table.mb, installed on the board by fcitx5-module-pinyinhelper; a copy is
kept in models/asr/). Binary layout, repeated until EOF:

    u8 len | utf-8 char (len bytes) | u8 count | count x (u8 initial, u8 final, u8 tone)
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Iterable

from utils.paths import ASR_MODEL_DIR

TABLE_CANDIDATES = (
    ASR_MODEL_DIR / "py_table.mb",
    Path("/usr/share/fcitx5/pinyinhelper/py_table.mb"),
)

INITIALS = (
    "", "b", "c", "ch", "d", "f", "g", "h", "j", "k", "l", "m", "n", "ng", "p", "q",
    "r", "s", "sh", "t", "w", "x", "y", "z", "zh",
)
FINALS = (
    "", "a", "ai", "an", "ang", "ao", "e", "ei", "en", "eng", "er", "i", "ia", "ian", "iang",
    "iao", "ie", "in", "ing", "iong", "iu", "m", "ng", "n", "o", "ong", "ou", "u", "ua", "uai",
    "uan", "uang", "ue", "ueng", "ui", "un", "uo", "v", "ve", "ve",
)
# Common Mandarin accent / ASR confusions ("fuzzy pinyin").
FUZZY_INITIALS = {frozenset(pair) for pair in (("zh", "z"), ("ch", "c"), ("sh", "s"), ("n", "l"), ("l", "r"), ("f", "h"))}
FUZZY_FINALS = {
    frozenset(pair)
    for pair in (("an", "ang"), ("en", "eng"), ("in", "ing"), ("ian", "iang"), ("uan", "uang"), ("eng", "ong"), ("o", "uo"), ("ao", "uo"))
}
PART_FUZZY = 0.75
HOMOPHONE = 0.95  # same syllable and tone (未/喂 wèi)
TONELESS_HOMOPHONE = 0.9  # same syllable, different tone (违 wéi / 喂 wèi)

# Everyday words that are exact (tonal) homophones of a 2-character keyword.
COMMON_HOMOPHONES = frozenset({
    "亲为",  # 亲力亲为 ~ 亲喂 (为 also reads wèi)
    "评为",  # 被评为 ~ 瓶喂
    "省了", "省啦",  # 省 also reads xǐng ~ 醒了
    "尿不",  # 尿不出来 ~ 尿布 (尿不湿 is its own keyword)
})

ToneReading = tuple[str, str, int]  # (initial, final, tone 0-4)


def load_table(path: Path) -> dict[str, frozenset[ToneReading]]:
    data = Path(path).read_bytes()
    table: dict[str, frozenset[ToneReading]] = {}
    offset = 0
    while offset < len(data):
        size = data[offset]
        offset += 1
        char = data[offset : offset + size].decode("utf-8", "replace")
        offset += size
        count = data[offset]
        offset += 1
        readings = set()
        for index in range(count):
            initial, final, tone = data[offset + 3 * index : offset + 3 * index + 3]
            if initial < len(INITIALS) and final < len(FINALS):
                readings.add((INITIALS[initial], FINALS[final], int(tone)))
        offset += 3 * count
        if len(char) == 1 and readings:
            table[char] = frozenset(readings)
    return table


@lru_cache(maxsize=4)
def default_table(path: str = "") -> dict[str, frozenset[ToneReading]]:
    candidates = [Path(path)] if path else list(TABLE_CANDIDATES)
    for candidate in candidates:
        if candidate.is_file():
            return load_table(candidate)
    return {}


def _part_similarity(a: str, b: str, fuzzy: set[frozenset[str]]) -> float:
    if a == b:
        return 1.0
    return PART_FUZZY if frozenset((a, b)) in fuzzy else 0.0


class PinyinMatcher:
    def __init__(self, table: dict[str, frozenset[ToneReading]], threshold: float = 0.9) -> None:
        self.table = table
        self.threshold = float(threshold)
        self.blocked = COMMON_HOMOPHONES

    @property
    def available(self) -> bool:
        return bool(self.table)

    def pinyin(self, text: str) -> list[str]:
        """Readable pinyin per character (first reading), e.g. 喂奶 -> ['wei4', 'nai3']."""
        result = []
        for char in text:
            readings = self.table.get(char)
            if not readings:
                result.append(char)
                continue
            initial, final, tone = sorted(readings, key=lambda r: (r[2] == 0, r))[0]
            result.append(f"{initial}{final}{tone or ''}")
        return result

    def char_similarity(self, a: str, b: str) -> float:
        """1.0 same char, 0.95 tonal homophone, 0.9 toneless homophone, 0.75/0.56 fuzzy pinyin, else 0."""
        if a == b:
            return 1.0
        ra, rb = self.table.get(a), self.table.get(b)
        if not ra or not rb:
            return 0.0
        if ra & rb:
            return HOMOPHONE
        best = 0.0
        for ia, fa, _ta in ra:
            for ib, fb, _tb in rb:
                if ia == ib and fa == fb:
                    return TONELESS_HOMOPHONE
                score = _part_similarity(ia, ib, FUZZY_INITIALS) * _part_similarity(fa, fb, FUZZY_FINALS)
                best = max(best, score)
        return best

    def _window_score(self, window: str, keyword: str) -> float | None:
        if window in self.blocked:
            return None
        scores = [self.char_similarity(a, b) for a, b in zip(window, keyword)]
        changed = [s for s in scores if s < 1.0]
        if len(changed) > 1:  # at most one character may differ
            return None
        if len(keyword) <= 2:
            if changed and changed[0] < HOMOPHONE:  # short words: tonal homophones only
                return None
        elif min(scores) < 0.5:
            return None
        score = sum(scores) / len(keyword)
        return score if score >= self.threshold else None

    def find(self, text: str, keywords: Iterable[str]) -> tuple[str, str, float] | None:
        """Best pronunciation hit as (keyword, heard_substring, score), or None."""
        if not self.table or not text:
            return None
        best: tuple[str, str, float] | None = None
        for keyword in keywords:
            size = len(keyword)
            if size < 2 or size > len(text):
                continue
            for start in range(len(text) - size + 1):
                window = text[start : start + size]
                score = self._window_score(window, keyword)
                if score is not None and (best is None or score > best[2]):
                    best = (keyword, window, round(score, 3))
        return best

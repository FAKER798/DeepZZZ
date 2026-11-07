from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import numpy as np

from ai.asr import apply_lfr, kaldi_fbank, split_tags
from services.care_log import CareStore
from services.voice_trigger import (
    CareIntentMatcher,
    SpeechSegmenter,
    VoiceTriggerSettings,
    VoiceTriggerWorker,
    chinese_number,
    load_settings,
    normalize_text,
    read_voice_trigger_status,
)


class Clock:
    def __init__(self) -> None:
        self.now = 1_790_000_000.0

    def __call__(self) -> float:
        return self.now


class StoreClient:
    """EdgeClient stand-in that talks to a real CareStore (what main.py serves)."""

    def __init__(self, store: CareStore) -> None:
        self.store = store

    def list_care_records(self, record_type, since=None, limit=5):
        return self.store.list_records(since=since, record_type=record_type, limit=limit)

    def create_care_record(self, payload):
        return self.store.create_record(payload)

    def update_care_record(self, record_id, payload):
        return self.store.update_record(record_id, payload)


def make_worker(clock: Clock, **overrides) -> tuple[VoiceTriggerWorker, CareStore]:
    tmp = Path(tempfile.mkdtemp())
    store = CareStore(tmp / "records.json", tmp / "profile.json", clock=clock)
    settings = VoiceTriggerSettings(status_path=str(tmp / "status.json"), command_path=str(tmp / "command.json"), **overrides)
    worker = VoiceTriggerWorker(settings, client=StoreClient(store), recognizer=object(), vad=lambda _: 0.0, clock=clock)
    return worker, store


def events(text: str) -> list[tuple[str, str, dict]]:
    matcher = CareIntentMatcher(VoiceTriggerSettings())
    return [(e.type, e.action, e.fields) for e in matcher.match(text)]


class MatcherTests(unittest.TestCase):
    def test_diaper_phrases(self) -> None:
        self.assertEqual(events("宝宝乖，妈妈给你换尿布了。"), [("diaper", "log", {})])
        self.assertEqual(events("換尿片啦"), [("diaper", "log", {})])
        self.assertEqual(events("换尿不湿，拉了好多便便")[0][2], {"kind": "dirty"})
        self.assertEqual(events("尿布湿了，尿了好多")[0][2], {"kind": "wet"})
        self.assertEqual(events("换纸尿裤，又是尿尿又是便便")[0][2], {"kind": "mixed"})

    def test_feeding_phrases(self) -> None:
        self.assertEqual(events("宝宝该喂奶了"), [("feeding", "log", {})])
        self.assertEqual(events("冲了120毫升奶粉喂奶")[0][2], {"kind": "bottle", "milk": "formula", "amount_ml": 120})
        self.assertEqual(events("瓶喂了一百五十毫升母乳")[0][2], {"kind": "bottle", "milk": "breast_milk", "amount_ml": 150})
        self.assertEqual(events("左边亲喂了15分钟")[0][2], {"kind": "breast", "duration_min": 15, "side": "left"})
        self.assertEqual(events("给宝宝喂辅食，吃了米粉")[0][2], {"kind": "solid", "food": "辅食"})

    def test_sleep_phrases(self) -> None:
        self.assertEqual(events("宝宝睡着了"), [("sleep", "sleep_start", {})])
        self.assertEqual(events("宝宝睡醒了"), [("sleep", "sleep_end", {})])
        self.assertEqual(events("小声点，别吵醒宝宝"), [])
        self.assertEqual(events("还没睡着呢"), [])

    def test_negations_and_chatter(self) -> None:
        self.assertEqual(events("不用换尿布，还是干的"), [])
        self.assertEqual(events("等会再喂奶"), [])
        self.assertEqual(events("今天天气不错，我们晚上吃面条吧。"), [])
        self.assertEqual(events("."), [])
        self.assertEqual(events(""), [])

    def test_two_events_in_one_sentence(self) -> None:
        kinds = [item[0] for item in events("换完尿布再喂奶")]
        self.assertEqual(kinds, ["diaper", "feeding"])

    def test_disabled_type(self) -> None:
        matcher = CareIntentMatcher(VoiceTriggerSettings(care_types=("diaper",)))
        self.assertEqual(matcher.match("宝宝睡着了，喂奶"), [])

    def test_new_sleep_phrases(self) -> None:
        self.assertEqual(events("宝宝睡觉了。"), [("sleep", "sleep_start", {})])
        self.assertEqual(events("开始睡觉"), [("sleep", "sleep_start", {})])
        self.assertEqual(events("宝宝醒来啦"), [("sleep", "sleep_end", {})])
        self.assertEqual(events("该睡觉了"), [])
        self.assertEqual(events("宝宝要睡觉了"), [])

    def test_pinyin_fuzzy_matching(self) -> None:
        from services.pinyin_match import default_table

        if not default_table():
            self.skipTest("pinyin table not available")
        matcher = CareIntentMatcher(VoiceTriggerSettings())
        negatives = (
            "好了行了", "这样违反规定", "他亲力亲为", "被评为优秀", "我们去吃饭吧", "这个月省了很多钱",
            "他尿不出来", "未来会更好", "味道不错", "为了你好", "喂，你好",
        )
        texts = ("换个尿不时", "宝宝睡找了", "给宝宝为母乳", "宝宝未奶粉呢？", "宝宝位奶了") + negatives
        hits = {text: [(e.type, e.action, e.keyword) for e in matcher.match(text)] for text in texts}
        self.assertEqual(hits["换个尿不时"], [("diaper", "log", "尿不湿≈尿不时")])
        self.assertEqual(hits["宝宝睡找了"], [("sleep", "sleep_start", "睡着了≈睡找了")])
        self.assertEqual(hits["给宝宝为母乳"], [("feeding", "log", "喂母乳≈为母乳")])
        self.assertEqual(hits["宝宝未奶粉呢？"], [("feeding", "log", "喂奶≈未奶")])
        self.assertEqual(matcher.match("宝宝未奶粉呢？")[0].fields, {"kind": "bottle", "milk": "formula"})
        self.assertEqual(hits["宝宝位奶了"], [("feeding", "log", "喂奶≈位奶")])
        for text in negatives:
            self.assertEqual(hits[text], [], text)
        off = CareIntentMatcher(VoiceTriggerSettings(fuzzy_pinyin=False))
        self.assertEqual(off.match("换个尿不时"), [])

    def test_pinyin_table_decoding(self) -> None:
        from services.pinyin_match import PinyinMatcher, default_table

        table = default_table()
        if not table:
            self.skipTest("pinyin table not available")
        self.assertEqual(table["换"], frozenset({("h", "uan", 4)}))
        self.assertIn(("zh", "uo", 2), table["着"])
        self.assertEqual(table["略"], frozenset({("l", "ve", 4)}))
        matcher = PinyinMatcher(table)
        self.assertEqual(matcher.pinyin("喂奶"), ["wei4", "nai3"])
        self.assertEqual(matcher.char_similarity("未", "喂"), 0.95)  # same tone
        self.assertEqual(matcher.char_similarity("违", "喂"), 0.9)  # different tone
        self.assertEqual(matcher.char_similarity("看", "换"), 0.0)
        self.assertEqual(matcher.char_similarity("森", "僧"), 0.75)  # sen/seng: fuzzy final
        self.assertEqual(matcher.char_similarity("森", "生"), 0.5625)  # sen/sheng: both parts fuzzy

    def test_chinese_numbers(self) -> None:
        for text, value in (("120", 120), ("一百二十", 120), ("一百五", 150), ("九十", 90), ("两百", 200), ("三十五", 35), ("十", 10), ("一百零五", 105)):
            self.assertEqual(chinese_number(text), value, text)

    def test_normalize(self) -> None:
        self.assertEqual(normalize_text("Hi, 換 尿布！"), "hi换尿布")


class WorkerTests(unittest.TestCase):
    def test_voice_creates_and_merges_diaper_record(self) -> None:
        clock = Clock()
        worker, store = make_worker(clock)
        entry = worker.handle_transcript({"text": "宝宝乖，换尿布了"}, captured_at=clock.now)
        self.assertEqual(entry["events"][0]["result"], "logged")
        clock.now += 60
        entry = worker.handle_transcript({"text": "哇，拉了好多便便"}, captured_at=clock.now)
        self.assertEqual(entry["events"], [])  # no diaper keyword -> nothing
        entry = worker.handle_transcript({"text": "尿布里都是便便"}, captured_at=clock.now)
        self.assertEqual(entry["events"][0]["result"], "merged")
        records = store.list_records(record_type="diaper")
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["kind"], "dirty")
        self.assertEqual(records[0]["source"], "voice")
        self.assertIn("换尿布了", records[0]["note"])
        self.assertIn("尿布里都是便便", records[0]["note"])

        clock.now += 15 * 60  # outside the 10 min window -> a new diaper change
        worker.handle_transcript({"text": "再换个尿不湿"}, captured_at=clock.now)
        self.assertEqual(len(store.list_records(record_type="diaper")), 2)

    def test_breast_then_formula_are_two_feedings(self) -> None:
        # The sequence heard on the board at 17:57-18:06 on 2026-11-05.
        clock = Clock()
        worker, store = make_worker(clock)
        script = [
            (0, "宝宝喂母乳的。", "logged"),
            (480, "喂奶粉。", "logged"),  # formula is a different feeding -> new record
            (488, "宝宝喂奶粉Q。", "merged"),  # same formula feeding
            (509, "宝宝喂母乳的。", "skipped"),  # same words as the breast-feeding record -> duplicate
            (524, "哎，是着了之后再说喂东西，宝宝喂母乳了。", "merged"),  # joins the breast record, not formula
            (544, "宝宝喂母乳吗？", None),  # a question, not a report
        ]
        for offset, text, expected in script:
            clock.now = 1_790_000_000.0 + offset
            entry = worker.handle_transcript({"text": text}, captured_at=clock.now)
            got = entry["events"][0]["result"] if entry["events"] else None
            self.assertEqual(got, expected, text)
        feedings = store.list_records(record_type="feeding")
        self.assertEqual(sorted((r.get("kind"), r.get("milk")) for r in feedings), [("bottle", "formula"), ("breast", None)])

    def test_questions_are_ignored(self) -> None:
        for text in ("宝宝喂母乳吗？", "换尿布了吗", "宝宝睡着了吗", "喂奶了没", "要不要换尿布"):
            self.assertEqual(events(text), [], text)
        # Modal particles are statements, even when the ASR adds a question mark.
        for text in ("宝宝未奶粉呢？", "宝宝喂奶呢", "在喂奶了没呢", "换尿布吧", "换尿布啊", "宝宝喂奶呀",
                     "换尿布啦", "喂奶哦", "喂奶嘛", "宝宝睡着了呢"):
            self.assertEqual(len(events(text)), 1, text)

    def test_app_record_is_not_duplicated(self) -> None:
        clock = Clock()
        worker, store = make_worker(clock)
        store.create_record({"type": "feeding", "kind": "bottle", "amount_ml": 90, "start_at": clock.now - 120})
        entry = worker.handle_transcript({"text": "喂奶喂完了"}, captured_at=clock.now)
        self.assertEqual((entry["events"][0]["result"], entry["events"][0]["reason"]), ("skipped", "already_logged"))
        self.assertEqual(len(store.list_records(record_type="feeding")), 1)

    def test_voice_sleep_start_and_end(self) -> None:
        clock = Clock()
        worker, store = make_worker(clock)
        self.assertEqual(worker.handle_transcript({"text": "宝宝睡着了"}, clock.now)["events"][0]["result"], "logged")
        self.assertEqual(worker.handle_transcript({"text": "睡着了睡着了"}, clock.now)["events"][0]["reason"], "already_sleeping")
        clock.now += 45 * 60
        self.assertEqual(worker.handle_transcript({"text": "宝宝醒了"}, clock.now)["events"][0]["result"], "merged")
        sleep = store.list_records(record_type="sleep")[0]
        self.assertAlmostEqual(sleep["end_at"] - sleep["start_at"], 45 * 60, delta=1)
        self.assertEqual(worker.handle_transcript({"text": "醒了"}, clock.now)["events"][0]["reason"], "no_sleep_in_progress")

    def test_status_reports_records(self) -> None:
        clock = Clock()
        worker, _ = make_worker(clock)
        worker.handle_transcript({"text": "喂奶，120毫升"}, clock.now)
        status = worker.status()
        self.assertEqual(status["mode"], "care_log")
        self.assertEqual(status["counters"]["records_logged"], 1)
        self.assertEqual(status["recent_records"][0]["record"]["amount_ml"], 120)
        worker._write_status()
        self.assertTrue(read_voice_trigger_status(worker.status_path, now=clock.now)["online"])

    def test_command_file_toggles_enabled(self) -> None:
        worker, _ = make_worker(Clock())
        worker.command_path.write_text(json.dumps({"enabled": False}), encoding="utf-8")
        worker.apply_command()
        self.assertFalse(worker.enabled)

    def test_missing_status_file_reports_offline(self) -> None:
        status = read_voice_trigger_status(Path(tempfile.mkdtemp()) / "missing.json")
        self.assertEqual((status["online"], status["state"]), (False, "offline"))


class SegmenterTests(unittest.TestCase):
    def test_emits_one_segment_per_utterance(self) -> None:
        def vad(window: np.ndarray) -> float:
            return 0.9 if float(np.abs(window).max()) > 0.1 else 0.0

        segmenter = SpeechSegmenter(vad, min_speech_s=0.2, min_silence_s=0.3, pre_roll_s=0.1)
        silence = np.zeros(16000, dtype=np.float32)
        speech = np.full(16000, 0.5, dtype=np.float32)
        segments = []
        for block in (silence, speech, silence, speech[:8000], silence):
            for start in range(0, block.size, 1000):
                segments.extend(segmenter.feed(block[start : start + 1000]))
        self.assertEqual(len(segments), 2)
        self.assertGreater(segments[0].size, 16000)
        self.assertLess(segments[0].size, 16000 + 16000 * 0.5)

    def test_short_blips_are_dropped(self) -> None:
        segmenter = SpeechSegmenter(lambda w: 0.9 if w.max() > 0.1 else 0.0, min_speech_s=0.3, min_silence_s=0.2)
        blip = np.concatenate([np.zeros(8000), np.full(1024, 0.5), np.zeros(16000)]).astype(np.float32)
        self.assertEqual(segmenter.feed(blip), [])

    def test_long_speech_is_split(self) -> None:
        segmenter = SpeechSegmenter(lambda w: 0.9, max_speech_s=2.0)
        self.assertEqual(len(segmenter.feed(np.full(16000 * 5, 0.5, dtype=np.float32))), 2)


class SettingsTests(unittest.TestCase):
    def test_config_file_and_cli_override_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "config.json"
            config.write_text(
                json.dumps({"voice_trigger": {"diaper_keywords": "换尿布, 换裤裤", "care_types": ["diaper", "feeding"], "enabled": "0"}}),
                encoding="utf-8",
            )
            settings, _args = load_settings(["--threads", "2"], config_path=config)
        self.assertEqual(settings.diaper_keywords, ("换尿布", "换裤裤"))
        self.assertEqual(settings.care_types, ("diaper", "feeding"))
        self.assertFalse(settings.enabled)
        self.assertEqual(settings.threads, 2)


class AsrFeatureTests(unittest.TestCase):
    def test_fbank_and_lfr_shapes(self) -> None:
        samples = np.random.RandomState(0).randn(16000).astype(np.float32) * 1000
        feats = kaldi_fbank(samples)
        self.assertEqual(feats.shape, (100, 80))
        self.assertTrue(np.isfinite(feats).all())
        self.assertEqual(apply_lfr(feats).shape, ((100 - 7) // 6 + 1, 560))
        self.assertEqual(kaldi_fbank(np.zeros(0)).shape, (0, 80))

    def test_split_tags(self) -> None:
        text, tags = split_tags("<|zh|><|NEUTRAL|><|Speech|><|withitn|>换尿布了。")
        self.assertEqual(text, "换尿布了。")
        self.assertEqual(tags, {"language": "zh", "emotion": "NEUTRAL", "event": "Speech", "itn": "withitn"})


if __name__ == "__main__":
    unittest.main()

package com.naviga.app

import java.time.Instant
import java.time.LocalDate
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import java.util.UUID
import org.json.JSONObject

// Feeding / diaper / sleep / growth records and the nap prediction. The board owns
// the data (GET/POST /api/care/...), so every phone shows the same log. The board API
// speaks Unix seconds; the app keeps epoch milliseconds.

enum class CareRecordType(val apiValue: String, val label: String) {
    Feeding("feeding", "喂养"),
    Diaper("diaper", "尿布"),
    Sleep("sleep", "睡眠"),
    Growth("growth", "生长"),
}

enum class FeedingKind(val apiValue: String, val label: String) {
    Breast("breast", "亲喂"),
    Bottle("bottle", "瓶喂"),
    Solid("solid", "辅食"),
}

enum class BreastSide(val apiValue: String, val label: String) {
    Left("left", "左侧"),
    Right("right", "右侧"),
    Both("both", "两侧"),
}

enum class MilkType(val apiValue: String, val label: String) {
    BreastMilk("breast_milk", "母乳"),
    Formula("formula", "配方奶"),
}

enum class DiaperKind(val apiValue: String, val label: String) {
    Wet("wet", "嘘嘘"),
    Dirty("dirty", "便便"),
    Mixed("mixed", "嘘嘘+便便"),
    Dry("dry", "干爽"),
}

enum class BabySex(val apiValue: String, val label: String) {
    Female("female", "女孩"),
    Male("male", "男孩"),
}

data class CareRecord(
    val id: String = newCareRecordId(),
    val type: CareRecordType,
    val startAtMillis: Long,
    val endAtMillis: Long? = null,
    val feedingKind: FeedingKind? = null,
    val side: BreastSide? = null,
    val durationMin: Int? = null,
    val amountMl: Int? = null,
    val milk: MilkType? = null,
    val food: String? = null,
    val diaperKind: DiaperKind? = null,
    val weightKg: Double? = null,
    val heightCm: Double? = null,
    val headCm: Double? = null,
    val note: String? = null,
) {
    val ongoing: Boolean
        get() = type == CareRecordType.Sleep && endAtMillis == null

    fun toJson(includeId: Boolean = true): JSONObject {
        val json = JSONObject()
        if (includeId) json.put("id", id)
        json.put("type", type.apiValue)
        json.put("start_at", startAtMillis.toEpochSeconds())
        json.put("note", note?.takeIf { it.isNotBlank() } ?: JSONObject.NULL)
        when (type) {
            CareRecordType.Feeding -> {
                json.put("kind", feedingKind?.apiValue ?: JSONObject.NULL)
                json.put("side", side?.apiValue ?: JSONObject.NULL)
                json.put("duration_min", durationMin ?: JSONObject.NULL)
                json.put("amount_ml", amountMl ?: JSONObject.NULL)
                json.put("milk", milk?.apiValue ?: JSONObject.NULL)
                json.put("food", food?.takeIf { it.isNotBlank() } ?: JSONObject.NULL)
            }
            CareRecordType.Diaper -> json.put("kind", diaperKind?.apiValue ?: JSONObject.NULL)
            CareRecordType.Sleep -> json.put("end_at", endAtMillis?.toEpochSeconds() ?: JSONObject.NULL)
            CareRecordType.Growth -> {
                json.put("weight_kg", weightKg ?: JSONObject.NULL)
                json.put("height_cm", heightCm ?: JSONObject.NULL)
                json.put("head_cm", headCm ?: JSONObject.NULL)
            }
        }
        return json
    }
}

data class CareProfile(
    val name: String? = null,
    val birthDate: LocalDate? = null,
    val sex: BabySex? = null,
) {
    fun toJson(): JSONObject = JSONObject()
        .put("name", name?.takeIf { it.isNotBlank() } ?: JSONObject.NULL)
        .put("birth_date", birthDate?.toString() ?: JSONObject.NULL)
        .put("sex", sex?.apiValue ?: JSONObject.NULL)
}

data class CareAge(val days: Int, val months: Int, val monthDays: Int) {
    val label: String
        get() = when {
            months == 0 -> "出生第 ${days + 1} 天"
            monthDays == 0 -> "$months 个月"
            else -> "$months 个月 $monthDays 天"
        }
}

data class CareTodaySummary(
    val feedingCount: Int = 0,
    val bottleMl: Int = 0,
    val breastMin: Int = 0,
    val solidCount: Int = 0,
    val diaperCount: Int = 0,
    val wetCount: Int = 0,
    val dirtyCount: Int = 0,
    val sleepCount: Int = 0,
    val sleepTotalMin: Double = 0.0,
    val sleepLongestMin: Double = 0.0,
)

enum class NapState { NeedProfile, NeedSleep, Sleeping, Awake, Unknown }

enum class NapTiming { Early, Due, Overdue }

enum class NapBasis { Age, Blend, History }

data class NapPrediction(
    val state: NapState,
    val ageRangeMin: Pair<Int, Int>? = null,
    val night: Boolean = false,
    val sleepId: String? = null,
    val sleepStartedAtMillis: Long? = null,
    val lastWakeAtMillis: Long? = null,
    val timing: NapTiming? = null,
    val basis: NapBasis? = null,
    val samples: Int = 0,
    val earliestAtMillis: Long? = null,
    val idealAtMillis: Long? = null,
    val latestAtMillis: Long? = null,
)

data class CareOverview(
    val boardNowMillis: Long,
    val profile: CareProfile,
    val age: CareAge?,
    val dayStartMillis: Long,
    val today: CareTodaySummary,
    val lastFeeding: CareRecord?,
    val lastDiaper: CareRecord?,
    val latestGrowth: CareRecord?,
    val nap: NapPrediction,
)

/** Result of a write call: the board's answer, or a message to show the parent. */
data class CareCallResult<T>(
    val value: T?,
    val errorMessage: String? = null,
) {
    val ok: Boolean
        get() = errorMessage == null
}

fun newCareRecordId(): String = UUID.randomUUID().toString().replace("-", "")

// ── JSON parsing ─────────────────────────────────────────────

internal fun JSONObject.toCareRecord(): CareRecord? {
    val type = CareRecordType.entries.firstOrNull { it.apiValue == optStringOrNull("type") } ?: return null
    val id = optStringOrNull("id") ?: return null
    val start = optDoubleOrNull("start_at") ?: return null
    val kind = optStringOrNull("kind")
    return CareRecord(
        id = id,
        type = type,
        startAtMillis = start.secondsToMillis(),
        endAtMillis = optDoubleOrNull("end_at")?.secondsToMillis(),
        feedingKind = FeedingKind.entries.firstOrNull { it.apiValue == kind }.takeIf { type == CareRecordType.Feeding },
        side = BreastSide.entries.firstOrNull { it.apiValue == optStringOrNull("side") },
        durationMin = optDoubleOrNull("duration_min")?.toInt(),
        amountMl = optDoubleOrNull("amount_ml")?.toInt(),
        milk = MilkType.entries.firstOrNull { it.apiValue == optStringOrNull("milk") },
        food = optStringOrNull("food"),
        diaperKind = DiaperKind.entries.firstOrNull { it.apiValue == kind }.takeIf { type == CareRecordType.Diaper },
        weightKg = optDoubleOrNull("weight_kg"),
        heightCm = optDoubleOrNull("height_cm"),
        headCm = optDoubleOrNull("head_cm"),
        note = optStringOrNull("note"),
    )
}

internal fun JSONObject.toCareProfile(): CareProfile {
    return CareProfile(
        name = optStringOrNull("name"),
        birthDate = optStringOrNull("birth_date")?.let { runCatching { LocalDate.parse(it) }.getOrNull() },
        sex = BabySex.entries.firstOrNull { it.apiValue == optStringOrNull("sex") },
    )
}

internal fun JSONObject.toCareOverview(): CareOverview {
    val today = optJSONObject("today")
    val feeding = today?.optJSONObject("feeding")
    val diaper = today?.optJSONObject("diaper")
    val sleep = today?.optJSONObject("sleep")
    val age = optJSONObject("age")
    return CareOverview(
        boardNowMillis = (optDoubleOrNull("now") ?: (System.currentTimeMillis() / 1_000.0)).secondsToMillis(),
        profile = optJSONObject("profile")?.toCareProfile() ?: CareProfile(),
        age = age?.let {
            CareAge(
                days = it.optInt("days"),
                months = it.optInt("months"),
                monthDays = it.optInt("month_days"),
            )
        },
        dayStartMillis = optJSONObject("day")?.optDoubleOrNull("start_at")?.secondsToMillis() ?: 0L,
        today = CareTodaySummary(
            feedingCount = feeding?.optInt("count") ?: 0,
            bottleMl = feeding?.optInt("bottle_ml") ?: 0,
            breastMin = feeding?.optInt("breast_min") ?: 0,
            solidCount = feeding?.optInt("solid_count") ?: 0,
            diaperCount = diaper?.optInt("count") ?: 0,
            wetCount = diaper?.optInt("wet") ?: 0,
            dirtyCount = diaper?.optInt("dirty") ?: 0,
            sleepCount = sleep?.optInt("count") ?: 0,
            sleepTotalMin = sleep?.optDoubleOrNull("total_min") ?: 0.0,
            sleepLongestMin = sleep?.optDoubleOrNull("longest_min") ?: 0.0,
        ),
        lastFeeding = optJSONObject("last_feeding")?.toCareRecord(),
        lastDiaper = optJSONObject("last_diaper")?.toCareRecord(),
        latestGrowth = optJSONObject("latest_growth")?.toCareRecord(),
        nap = optJSONObject("nap")?.toNapPrediction() ?: NapPrediction(NapState.Unknown),
    )
}

private fun JSONObject.toNapPrediction(): NapPrediction {
    val range = optJSONArray("age_range_min")
    return NapPrediction(
        state = when (optStringOrNull("state")) {
            "need_profile" -> NapState.NeedProfile
            "need_sleep" -> NapState.NeedSleep
            "sleeping" -> NapState.Sleeping
            "awake" -> NapState.Awake
            else -> NapState.Unknown
        },
        ageRangeMin = if (range != null && range.length() == 2) range.optInt(0) to range.optInt(1) else null,
        night = optBoolean("night", false),
        sleepId = optStringOrNull("sleep_id"),
        sleepStartedAtMillis = optDoubleOrNull("started_at")?.secondsToMillis(),
        lastWakeAtMillis = optDoubleOrNull("last_wake_at")?.secondsToMillis(),
        timing = when (optStringOrNull("timing")) {
            "early" -> NapTiming.Early
            "due" -> NapTiming.Due
            "overdue" -> NapTiming.Overdue
            else -> null
        },
        basis = when (optStringOrNull("basis")) {
            "age" -> NapBasis.Age
            "blend" -> NapBasis.Blend
            "history" -> NapBasis.History
            else -> null
        },
        samples = optInt("samples", 0),
        earliestAtMillis = optDoubleOrNull("earliest_at")?.secondsToMillis(),
        idealAtMillis = optDoubleOrNull("ideal_at")?.secondsToMillis(),
        latestAtMillis = optDoubleOrNull("latest_at")?.secondsToMillis(),
    )
}

internal fun JSONObject.optStringOrNull(key: String): String? {
    if (!has(key) || isNull(key)) return null
    return optString(key).takeIf { it.isNotBlank() }
}

internal fun JSONObject.optDoubleOrNull(key: String): Double? {
    if (!has(key) || isNull(key)) return null
    return optDouble(key).takeUnless { it.isNaN() }
}

private fun Double.secondsToMillis(): Long = (this * 1_000).toLong()

private fun Long.toEpochSeconds(): Double = this / 1_000.0

// ── formatting ───────────────────────────────────────────────

private val careZone: ZoneId
    get() = ZoneId.systemDefault()

private val clockFormatter = DateTimeFormatter.ofPattern("HH:mm")
private val dayFormatter = DateTimeFormatter.ofPattern("M月d日")

fun formatClock(millis: Long): String = Instant.ofEpochMilli(millis).atZone(careZone).format(clockFormatter)

fun formatDay(millis: Long): String = Instant.ofEpochMilli(millis).atZone(careZone).format(dayFormatter)

fun formatDayAndClock(millis: Long, nowMillis: Long): String {
    val day = Instant.ofEpochMilli(millis).atZone(careZone).toLocalDate()
    val today = Instant.ofEpochMilli(nowMillis).atZone(careZone).toLocalDate()
    val prefix = when (day) {
        today -> "今天"
        today.minusDays(1) -> "昨天"
        else -> formatDay(millis)
    }
    return "$prefix ${formatClock(millis)}"
}

fun formatDurationMinutes(minutes: Double): String {
    val total = minutes.coerceAtLeast(0.0).toInt()
    val hours = total / 60
    val rest = total % 60
    return when {
        hours == 0 -> "$rest 分钟"
        rest == 0 -> "$hours 小时"
        else -> "$hours 小时 $rest 分"
    }
}

fun formatNumber(value: Double): String {
    return if (value % 1.0 == 0.0) value.toLong().toString() else "%.1f".format(value).trimEnd('0').trimEnd('.')
}

fun CareRecord.title(): String = when (type) {
    CareRecordType.Feeding -> feedingKind?.label ?: "喂养"
    CareRecordType.Diaper -> "尿布 · ${diaperKind?.label ?: ""}".trimEnd(' ', '·')
    CareRecordType.Sleep -> if (ongoing) "睡眠中" else "睡眠"
    CareRecordType.Growth -> "生长测量"
}

fun CareRecord.detail(nowMillis: Long): String {
    val parts: List<String> = when (type) {
        CareRecordType.Feeding -> when (feedingKind) {
            FeedingKind.Breast -> listOfNotNull(side?.label, durationMin?.let { "$it 分钟" })
            FeedingKind.Bottle -> listOfNotNull(amountMl?.let { "$it ml" }, milk?.label)
            FeedingKind.Solid -> listOfNotNull(food)
            null -> emptyList()
        }
        CareRecordType.Diaper -> emptyList()
        CareRecordType.Sleep -> {
            val end = endAtMillis ?: nowMillis
            listOf(
                "${formatClock(startAtMillis)}–${endAtMillis?.let(::formatClock) ?: "现在"}",
                formatDurationMinutes((end - startAtMillis) / 60_000.0),
            )
        }
        CareRecordType.Growth -> listOfNotNull(
            weightKg?.let { "${formatNumber(it)} kg" },
            heightCm?.let { "身长 ${formatNumber(it)} cm" },
            headCm?.let { "头围 ${formatNumber(it)} cm" },
        )
    }
    return (parts + listOfNotNull(note)).joinToString(" · ")
}

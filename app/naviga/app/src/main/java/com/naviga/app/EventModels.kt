package com.naviga.app

import org.json.JSONObject

/** Vision / sound episode types logged by the board (services/event_log.py). */
enum class BoardEventType(val apiValue: String, val label: String) {
    Cry("cry", "哭闹"),
    FaceCover("face_cover_rollover", "遮脸/翻身"),
    Boundary("boundary", "越出安全区域"),
    NoPerson("no_person", "离开画面");

    companion object {
        fun fromApi(value: String?): BoardEventType? = entries.firstOrNull { it.apiValue == value }
    }
}

/** What the camera sees right now, as the board's event log last observed it. */
enum class VisionState(val apiValue: String, val label: String?) {
    Visible("visible", "面部可见"),
    Covered("covered", "疑似遮脸/翻身"),
    NoPerson("no_person", "画面无人"),
    Paused("paused", null),
    Stale("stale", null),
    Unknown("unknown", null);

    companion object {
        fun fromApi(value: String?): VisionState = entries.firstOrNull { it.apiValue == value } ?: Unknown
    }
}

data class BoardEvent(
    val id: String,
    val type: BoardEventType,
    val startMillis: Long,
    val endMillis: Long?,
    val durationSeconds: Double,
    val ongoing: Boolean,
    val peakScore: Double?,
)

data class BoardEventTypeSummary(
    val count: Int,
    val totalSeconds: Double,
    val latest: BoardEvent?,
)

data class BoardEventSummary(
    val windowSeconds: Double,
    val visionState: VisionState,
    val active: List<BoardEventType>,
    val types: Map<BoardEventType, BoardEventTypeSummary>,
) {
    fun count(type: BoardEventType): Int = types[type]?.count ?: 0

    val totalCount: Int get() = types.values.sumOf { it.count }
}

private fun JSONObject.optSecondsAsMillis(name: String): Long? {
    if (isNull(name) || !has(name)) return null
    val seconds = optDouble(name, Double.NaN)
    return if (seconds.isNaN()) null else (seconds * 1000).toLong()
}

internal fun JSONObject.toBoardEvent(): BoardEvent? {
    val type = BoardEventType.fromApi(optString("type")) ?: return null
    val start = optSecondsAsMillis("start_at") ?: return null
    return BoardEvent(
        id = optString("id"),
        type = type,
        startMillis = start,
        endMillis = optSecondsAsMillis("end_at"),
        durationSeconds = optDouble("duration_s", 0.0).takeUnless { it.isNaN() } ?: 0.0,
        ongoing = optBoolean("ongoing", false),
        peakScore = if (has("peak_score") && !isNull("peak_score")) optDouble("peak_score") else null,
    )
}

internal fun JSONObject.toBoardEventSummary(): BoardEventSummary {
    val current = optJSONObject("current")
    val activeJson = current?.optJSONArray("active")
    val active = buildList {
        for (index in 0 until (activeJson?.length() ?: 0)) {
            BoardEventType.fromApi(activeJson?.optString(index))?.let(::add)
        }
    }
    val typesJson = optJSONObject("types")
    val types = BoardEventType.entries.associateWith { type ->
        val item = typesJson?.optJSONObject(type.apiValue)
        BoardEventTypeSummary(
            count = item?.optInt("count", 0) ?: 0,
            totalSeconds = item?.optDouble("total_s", 0.0)?.takeUnless { it.isNaN() } ?: 0.0,
            latest = item?.optJSONObject("latest")?.toBoardEvent(),
        )
    }
    return BoardEventSummary(
        windowSeconds = optDouble("window_s", 7200.0),
        visionState = VisionState.fromApi(current?.optString("vision_state")),
        active = active,
        types = types,
    )
}

/** "3 分钟" / "45 秒" / "1 小时 5 分钟" */
fun eventDurationText(seconds: Double): String {
    val total = seconds.coerceAtLeast(0.0)
    if (total < 60) return "${total.toInt()} 秒"
    val minutes = Math.round(total / 60).toInt()
    val hours = minutes / 60
    val rest = minutes % 60
    return when {
        hours > 0 && rest > 0 -> "$hours 小时 $rest 分钟"
        hours > 0 -> "$hours 小时"
        else -> "$minutes 分钟"
    }
}

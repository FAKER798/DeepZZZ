package com.naviga.app

enum class SleepEventType {
    Cry,
    Roll,
    Sleep,
    OffBed,
}

data class SleepSegment(val startHour: Float, val endHour: Float, val type: SleepEventType)

data class SleepSummary(
    val totalSleep: String,
    val sleepCount: String,
    val longestSleep: String,
    val cryCount: String,
)

data class SleepUiState(
    val summary: SleepSummary,
    val segments: List<SleepSegment>,
)

val TimelineLanes = listOf(
    SleepEventType.Cry,
    SleepEventType.Roll,
    SleepEventType.Sleep,
)

val TimelineLabels = mapOf(
    SleepEventType.Cry to "哭闹",
    SleepEventType.Roll to "翻身",
    SleepEventType.Sleep to "睡眠",
)

val SegmentLaneIndex = mapOf(
    SleepEventType.Cry to 0,
    SleepEventType.Roll to 1,
    SleepEventType.Sleep to 2,
    SleepEventType.OffBed to 2,
)

fun sampleSleepUiState(): SleepUiState {
    val rawSegments = sampleSleepSegments()
    val segments = rawSegments + deriveSleepGaps(rawSegments)
    return SleepUiState(
        summary = summarizeSleep(segments),
        segments = segments,
    )
}

private fun summarizeSleep(segments: List<SleepSegment>): SleepSummary {
    val sleepSegments = segments.filter { it.type == SleepEventType.Sleep }
    val totalMinutes = kotlin.math.round(sleepSegments.sumOf { segment ->
        ((segment.endHour - segment.startHour) * 60f).toDouble()
    }).toInt()
    val longestMinutes = sleepSegments.maxOfOrNull { segment ->
        kotlin.math.round((segment.endHour - segment.startHour) * 60f).toInt()
    } ?: 0
    return SleepSummary(
        totalSleep = formatSleepDuration(totalMinutes),
        sleepCount = "${sleepSegments.size} 次",
        longestSleep = formatSleepDuration(longestMinutes),
        cryCount = "${segments.count { it.type == SleepEventType.Cry }} 次",
    )
}

private fun formatSleepDuration(totalMinutes: Int): String {
    val hours = totalMinutes / 60
    val minutes = totalMinutes % 60
    return "$hours 小时 $minutes 分"
}

private fun sampleSleepSegments() = listOf(
    SleepSegment(14.6f, 15.0f, SleepEventType.Cry),
    SleepSegment(23.1f, 23.5f, SleepEventType.Cry),
    SleepSegment(1.6f, 2.0f, SleepEventType.Cry),
    SleepSegment(5.1f, 5.5f, SleepEventType.Cry),
    SleepSegment(22.0f, 23.0f, SleepEventType.Roll),
    SleepSegment(0.5f, 1.5f, SleepEventType.Roll),
    SleepSegment(2.5f, 3.5f, SleepEventType.Roll),
    SleepSegment(4.0f, 4.8f, SleepEventType.Roll),
    SleepSegment(6.0f, 7.0f, SleepEventType.OffBed),
    SleepSegment(16.0f, 18.0f, SleepEventType.OffBed),
)

private fun deriveSleepGaps(nonSleep: List<SleepSegment>): List<SleepSegment> {
    val occupied = nonSleep.filter { it.type != SleepEventType.Sleep }.sortedBy { it.startHour }
    if (occupied.isEmpty()) return listOf(SleepSegment(0f, 24f, SleepEventType.Sleep))

    val gaps = mutableListOf<SleepSegment>()
    if (occupied.first().startHour > 0f) {
        gaps.add(SleepSegment(0f, occupied.first().startHour, SleepEventType.Sleep))
    }

    for (i in 0 until occupied.size - 1) {
        val end = occupied[i].endHour
        val next = occupied[i + 1].startHour
        if (next - end > 0.05f) {
            gaps.add(SleepSegment(end, next, SleepEventType.Sleep))
        }
    }

    if (occupied.last().endHour < 24f) {
        gaps.add(SleepSegment(occupied.last().endHour, 24f, SleepEventType.Sleep))
    }

    return gaps
}

package com.naviga.app

import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.ElevatedCard
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.geometry.CornerRadius
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.drawscope.DrawScope
import androidx.compose.ui.text.TextMeasurer
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.drawText
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.rememberTextMeasurer
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp

@Composable
private fun rememberEventColors(): Map<SleepEventType, Color> {
    val scheme = MaterialTheme.colorScheme
    return remember(scheme) {
        mapOf(
            SleepEventType.Cry to Color(0xFFE53935),
            SleepEventType.Roll to Color(0xFFFF8F00),
            SleepEventType.Sleep to scheme.primary,
            SleepEventType.OffBed to scheme.outline,
        )
    }
}


// ── Page ──────────────────────────────────────────────────────

@OptIn(ExperimentalLayoutApi::class)
@Composable
fun SleepPage(
    uiState: SleepUiState = remember { sampleSleepUiState() },
) {
    val eventColors = rememberEventColors()

    val onSurface = MaterialTheme.colorScheme.onSurface
    val onSurfaceVar = MaterialTheme.colorScheme.onSurfaceVariant
    val outline = MaterialTheme.colorScheme.outline

    Column(
        modifier = Modifier.fillMaxSize().padding(horizontal = 18.dp, vertical = 18.dp),
    ) {
        PageHeader(title = "睡眠")
        Spacer(Modifier.height(16.dp))
        Text(
            text = "昨晚睡眠",
            style = MaterialTheme.typography.titleMedium,
            color = onSurface,
        )
        Spacer(Modifier.height(10.dp))
        SleepSummaryGrid(
            summary = uiState.summary,
            onSurface = onSurface,
            onSurfaceVar = onSurfaceVar,
            modifier = Modifier.fillMaxWidth().weight(0.9f),
        )
        Spacer(Modifier.height(16.dp))
        SleepTimelineCard(
            segments = uiState.segments,
            eventColors = eventColors,
            onSurfaceVar = onSurfaceVar,
            outline = outline,
            modifier = Modifier.fillMaxWidth().weight(1.1f),
        )
    }
}

// ── Summary ───────────────────────────────────────────────────

@Composable
private fun SleepSummaryGrid(
    summary: SleepSummary,
    onSurface: Color,
    onSurfaceVar: Color,
    modifier: Modifier = Modifier,
) {
    Column(
        modifier = modifier,
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        Row(
            modifier = Modifier.fillMaxWidth().weight(1f),
            horizontalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            SleepSummaryBlock(
                label = "总睡眠",
                value = summary.totalSleep,
                onSurface = onSurface,
                onSurfaceVar = onSurfaceVar,
                modifier = Modifier.fillMaxHeight().weight(1f),
            )
            SleepSummaryBlock(
                label = "睡眠次数",
                value = summary.sleepCount,
                onSurface = onSurface,
                onSurfaceVar = onSurfaceVar,
                modifier = Modifier.fillMaxHeight().weight(1f),
            )
        }
        Row(
            modifier = Modifier.fillMaxWidth().weight(1f),
            horizontalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            SleepSummaryBlock(
                label = "最长睡眠",
                value = summary.longestSleep,
                onSurface = onSurface,
                onSurfaceVar = onSurfaceVar,
                modifier = Modifier.fillMaxHeight().weight(1f),
            )
            SleepSummaryBlock(
                label = "哭闹次数",
                value = summary.cryCount,
                onSurface = onSurface,
                onSurfaceVar = onSurfaceVar,
                modifier = Modifier.fillMaxHeight().weight(1f),
            )
        }
    }
}

@Composable
private fun SleepSummaryBlock(
    label: String,
    value: String,
    onSurface: Color,
    onSurfaceVar: Color,
    modifier: Modifier = Modifier,
) {
    ElevatedCard(
        modifier = modifier,
        shape = CardShape,
        colors = CardDefaults.elevatedCardColors(containerColor = MaterialTheme.colorScheme.surface),
    ) {
        Column(
            modifier = Modifier.fillMaxSize().padding(horizontal = 16.dp, vertical = 14.dp),
            verticalArrangement = Arrangement.Center,
        ) {
            Text(text = label, style = MaterialTheme.typography.labelLarge, color = onSurfaceVar)
            Spacer(Modifier.height(8.dp))
            Text(
                text = value,
                style = MaterialTheme.typography.titleLarge,
                fontWeight = FontWeight.Bold,
                color = onSurface,
                maxLines = 2,
            )
        }
    }
}

// ── Timeline ──────────────────────────────────────────────────

@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun SleepTimelineCard(
    segments: List<SleepSegment>,
    eventColors: Map<SleepEventType, Color>,
    onSurfaceVar: Color,
    outline: Color,
    modifier: Modifier = Modifier,
) {
    val textMeasurer = rememberTextMeasurer()

    ElevatedCard(
        modifier = modifier,
        shape = CardShape,
        colors = CardDefaults.elevatedCardColors(containerColor = MaterialTheme.colorScheme.surface),
    ) {
        Column(modifier = Modifier.fillMaxSize().padding(horizontal = 16.dp, vertical = 14.dp)) {
            FlowRow(
                horizontalArrangement = Arrangement.spacedBy(12.dp),
                verticalArrangement = Arrangement.spacedBy(4.dp),
            ) {
                TimelineLanes.forEach { type ->
                    Row(
                        verticalAlignment = Alignment.CenterVertically,
                        horizontalArrangement = Arrangement.spacedBy(5.dp),
                    ) {
                        Box(
                            modifier = Modifier
                                .size(8.dp)
                                .clip(CircleShape)
                                .background(eventColors.getValue(type)),
                        )
                        Text(
                            text = TimelineLabels.getValue(type),
                            style = MaterialTheme.typography.labelSmall,
                            color = onSurfaceVar,
                        )
                    }
                }
            }

            Spacer(Modifier.height(12.dp))

            TimelineCanvas(
                segments = segments,
                eventColors = eventColors,
                onSurfaceVar = onSurfaceVar,
                outline = outline,
                textMeasurer = textMeasurer,
                modifier = Modifier.fillMaxWidth().weight(1f),
            )
        }
    }
}

@Composable
private fun TimelineCanvas(
    segments: List<SleepSegment>,
    eventColors: Map<SleepEventType, Color>,
    onSurfaceVar: Color,
    outline: Color,
    textMeasurer: TextMeasurer,
    modifier: Modifier = Modifier,
) {
    Canvas(
        modifier = modifier,
    ) {
        drawTimeline(
            segments = segments,
            eventColors = eventColors,
            onSurfaceVar = onSurfaceVar,
            outline = outline,
            textMeasurer = textMeasurer,
        )
    }
}

private const val TIMELINE_LP = 36f
private const val TIMELINE_RP = 8f

private fun DrawScope.drawTimeline(
    segments: List<SleepSegment>,
    eventColors: Map<SleepEventType, Color>,
    onSurfaceVar: Color,
    outline: Color,
    textMeasurer: TextMeasurer,
) {
    val w = size.width
    val h = size.height
    val lp = TIMELINE_LP.dp.toPx()
    val rp = TIMELINE_RP.dp.toPx()
    val chartW = w - lp - rp
    val laneCount = TimelineLanes.size
    val laneH = (h - 24.dp.toPx()) / laneCount
    val hourToX = { hour: Float -> lp + (hour / 24f) * chartW }

    val gridColor = outline.copy(alpha = 0.08f)

    // Grid
    for (i in 0..laneCount) {
        val y = i * laneH
        drawLine(gridColor, Offset(lp, y), Offset(w - rp, y), strokeWidth = 0.5f)
    }
    for (hr in 0..24 step 6) {
        val x = hourToX(hr.toFloat())
        drawLine(gridColor, Offset(x, 0f), Offset(x, laneCount * laneH), strokeWidth = 0.5f)
    }

    // Segments
    segments.forEach { seg ->
        val lane = SegmentLaneIndex[seg.type] ?: return@forEach
        val x1 = hourToX(seg.startHour)
        val x2 = hourToX(seg.endHour)
        val y = lane * laneH + 3.dp.toPx()
        val barH = laneH - 6.dp.toPx()
        val alpha = if (seg.type == SleepEventType.OffBed) 0.18f else 0.32f
        drawRoundRect(
            color = eventColors.getValue(seg.type).copy(alpha = alpha),
            topLeft = Offset(x1, y),
            size = Size(x2 - x1, barH),
            cornerRadius = CornerRadius(4.dp.toPx()),
        )
    }

    // X-axis
    val axisStyle = TextStyle(fontSize = 9.sp, color = onSurfaceVar.copy(alpha = 0.6f))
    for (hr in 0..24 step 6) {
        val x = hourToX(hr.toFloat())
        val result = textMeasurer.measure("%02d".format(hr), axisStyle)
        drawText(
            textLayoutResult = result,
            topLeft = Offset(x - result.size.width / 2f, laneCount * laneH + 6.dp.toPx()),
        )
    }
}


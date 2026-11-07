package com.naviga.app

import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.listSaver
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue

class CameraUiState {
    var live by mutableStateOf(true)
    var listenEnabled by mutableStateOf(false)
    var voiceRecording by mutableStateOf(false)
    var fullscreen by mutableStateOf(false)
    var safetyZone by mutableStateOf<SafetyZone?>(null)
    var activityZoneMode by mutableStateOf(ActivityZoneMode.Safe)
}

class ControlsUiState {
    var lullabyLevel by mutableFloatStateOf(0.6f)
    var lullabyEnabled by mutableStateOf(true)
    var autoPlayOnCry by mutableStateOf(false)
}

class AlertSettingsState {
    var faceAlert by mutableStateOf(true)
    var noPersonAlert by mutableStateOf(true)
    var cryAlert by mutableStateOf(true)
    var boundaryAlert by mutableStateOf(true)
}

fun AlertSettingsState.snapshot(
    safetyZone: SafetyZone? = null,
    activityZoneMode: ActivityZoneMode = ActivityZoneMode.Safe,
): AlertSettingsSnapshot {
    return AlertSettingsSnapshot(
        faceAlert = faceAlert,
        noPersonAlert = noPersonAlert,
        cryAlert = cryAlert,
        boundaryAlert = boundaryAlert,
        safetyZone = safetyZone?.normalized(),
        activityZoneMode = activityZoneMode,
    )
}

enum class ActivityZoneMode(val label: String, val apiValue: String) {
    Safe("安全区域", "safe"),
    Danger("危险区域", "danger"),
}

data class SafetyZone(
    val points: List<ZonePoint>,
) {
    constructor(left: Float, top: Float, right: Float, bottom: Float) : this(
        listOf(
            ZonePoint(left, top),
            ZonePoint(right, top),
            ZonePoint(right, bottom),
            ZonePoint(left, bottom),
        ),
    )

    val left: Float get() = points.minOfOrNull { it.x } ?: 0f
    val top: Float get() = points.minOfOrNull { it.y } ?: 0f
    val right: Float get() = points.maxOfOrNull { it.x } ?: 0f
    val bottom: Float get() = points.maxOfOrNull { it.y } ?: 0f

    fun normalized(): SafetyZone {
        return SafetyZone(points.map { it.normalized() })
    }

    fun contains(x: Float, y: Float): Boolean {
        return pointInPolygon(ZonePoint(x, y), normalized().points, includeBoundary = true)
    }

    fun isValid(): Boolean {
        val normalizedPoints = normalized().points
        return normalizedPoints.distinct().size >= 3 &&
            polygonArea(normalizedPoints) >= MIN_ZONE_AREA &&
            !polygonSelfIntersects(normalizedPoints)
    }

    fun fullyContainsRect(left: Float, top: Float, right: Float, bottom: Float): Boolean {
        val polygon = normalized().points
        if (!isValid()) return false
        val corners = rectCorners(left, top, right, bottom)
        if (!corners.all { pointInPolygon(it, polygon, includeBoundary = false) }) return false
        return !polygonEdges(polygon).any { edge ->
            rectEdges(corners).any { rectEdge -> segmentsIntersect(edge.first, edge.second, rectEdge.first, rectEdge.second) }
        }
    }

    fun intersectsRect(left: Float, top: Float, right: Float, bottom: Float): Boolean {
        val polygon = normalized().points
        if (!isValid()) return false
        val corners = rectCorners(left, top, right, bottom)
        if (corners.any { pointInPolygon(it, polygon, includeBoundary = true) }) return true
        if (polygon.any { it.x in left..right && it.y in top..bottom }) return true
        return polygonEdges(polygon).any { edge ->
            rectEdges(corners).any { rectEdge -> segmentsIntersect(edge.first, edge.second, rectEdge.first, rectEdge.second) }
        }
    }

    companion object {
        fun default() = SafetyZone(0.12f, 0.14f, 0.88f, 0.88f)
    }
}

data class ZonePoint(val x: Float, val y: Float) {
    fun normalized() = ZonePoint(x.coerceIn(0f, 1f), y.coerceIn(0f, 1f))
}

private const val MIN_ZONE_AREA = 0.001f
private const val GEOMETRY_EPSILON = 0.00001f

private fun polygonArea(points: List<ZonePoint>): Float {
    if (points.size < 3) return 0f
    return kotlin.math.abs(points.indices.sumOf { index ->
        val next = points[(index + 1) % points.size]
        (points[index].x * next.y - next.x * points[index].y).toDouble()
    }.toFloat()) / 2f
}

private fun pointInPolygon(point: ZonePoint, polygon: List<ZonePoint>, includeBoundary: Boolean): Boolean {
    if (polygon.size < 3) return false
    var inside = false
    for (index in polygon.indices) {
        val a = polygon[index]
        val b = polygon[(index + 1) % polygon.size]
        if (pointOnSegment(point, a, b)) return includeBoundary
        val crosses = (a.y > point.y) != (b.y > point.y) &&
            point.x < (b.x - a.x) * (point.y - a.y) / (b.y - a.y) + a.x
        if (crosses) inside = !inside
    }
    return inside
}

private fun pointOnSegment(point: ZonePoint, a: ZonePoint, b: ZonePoint): Boolean {
    val cross = (point.y - a.y) * (b.x - a.x) - (point.x - a.x) * (b.y - a.y)
    if (kotlin.math.abs(cross) > GEOMETRY_EPSILON) return false
    return point.x >= minOf(a.x, b.x) - GEOMETRY_EPSILON &&
        point.x <= maxOf(a.x, b.x) + GEOMETRY_EPSILON &&
        point.y >= minOf(a.y, b.y) - GEOMETRY_EPSILON &&
        point.y <= maxOf(a.y, b.y) + GEOMETRY_EPSILON
}

private fun segmentsIntersect(a: ZonePoint, b: ZonePoint, c: ZonePoint, d: ZonePoint): Boolean {
    fun orientation(p: ZonePoint, q: ZonePoint, r: ZonePoint): Float =
        (q.y - p.y) * (r.x - q.x) - (q.x - p.x) * (r.y - q.y)
    val o1 = orientation(a, b, c)
    val o2 = orientation(a, b, d)
    val o3 = orientation(c, d, a)
    val o4 = orientation(c, d, b)
    if ((o1 > GEOMETRY_EPSILON && o2 < -GEOMETRY_EPSILON || o1 < -GEOMETRY_EPSILON && o2 > GEOMETRY_EPSILON) &&
        (o3 > GEOMETRY_EPSILON && o4 < -GEOMETRY_EPSILON || o3 < -GEOMETRY_EPSILON && o4 > GEOMETRY_EPSILON)
    ) return true
    return kotlin.math.abs(o1) <= GEOMETRY_EPSILON && pointOnSegment(c, a, b) ||
        kotlin.math.abs(o2) <= GEOMETRY_EPSILON && pointOnSegment(d, a, b) ||
        kotlin.math.abs(o3) <= GEOMETRY_EPSILON && pointOnSegment(a, c, d) ||
        kotlin.math.abs(o4) <= GEOMETRY_EPSILON && pointOnSegment(b, c, d)
}

private fun rectCorners(left: Float, top: Float, right: Float, bottom: Float) = listOf(
    ZonePoint(left, top), ZonePoint(right, top), ZonePoint(right, bottom), ZonePoint(left, bottom),
)

private fun polygonEdges(points: List<ZonePoint>) = points.indices.map { index ->
    points[index] to points[(index + 1) % points.size]
}

private fun polygonSelfIntersects(points: List<ZonePoint>): Boolean {
    val edges = polygonEdges(points)
    return edges.indices.any { firstIndex ->
        ((firstIndex + 1) until edges.size).any { secondIndex ->
            val adjacent = secondIndex == firstIndex + 1 || firstIndex == 0 && secondIndex == edges.lastIndex
            !adjacent && segmentsIntersect(
                edges[firstIndex].first,
                edges[firstIndex].second,
                edges[secondIndex].first,
                edges[secondIndex].second,
            )
        }
    }
}

private fun rectEdges(corners: List<ZonePoint>) = polygonEdges(corners)

class FamilySettingsState {
    var inviteCount by mutableIntStateOf(0)
}

@Composable
fun rememberCameraUiState(): CameraUiState {
    return rememberSaveable(saver = CameraUiStateSaver) { CameraUiState() }
}

@Composable
fun rememberControlsUiState(): ControlsUiState {
    return rememberSaveable(saver = ControlsUiStateSaver) { ControlsUiState() }
}

@Composable
fun rememberAlertSettingsState(): AlertSettingsState {
    return rememberSaveable(saver = AlertSettingsStateSaver) { AlertSettingsState() }
}

@Composable
fun rememberFamilySettingsState(): FamilySettingsState {
    return rememberSaveable(saver = FamilySettingsStateSaver) { FamilySettingsState() }
}

private val CameraUiStateSaver = listSaver<CameraUiState, Any>(
    save = {
        buildList {
            add(it.live)
            add(it.listenEnabled)
            add(it.fullscreen)
            add(it.safetyZone != null)
            add(it.activityZoneMode.name)
            add(it.safetyZone?.points?.size ?: 0)
            it.safetyZone?.points?.forEach { point ->
                add(point.x)
                add(point.y)
            }
        }
    },
    restore = {
        CameraUiState().apply {
            live = it[0] as Boolean
            listenEnabled = it[1] as Boolean
            fullscreen = it[2] as Boolean
            val hasZone = it.getOrNull(3) as? Boolean ?: false
            val usesPolygonFormat = it.getOrNull(4) is String
            safetyZone = if (hasZone && usesPolygonFormat) {
                val pointCount = it.getOrNull(5) as? Int ?: 0
                SafetyZone(
                    List(pointCount) { index ->
                        ZonePoint(
                            x = it.getOrNull(6 + index * 2) as? Float ?: 0f,
                            y = it.getOrNull(7 + index * 2) as? Float ?: 0f,
                        )
                    },
                ).normalized().takeIf { zone -> zone.isValid() }
            } else if (hasZone) {
                SafetyZone(
                    left = it.getOrNull(4) as? Float ?: 0f,
                    top = it.getOrNull(5) as? Float ?: 0f,
                    right = it.getOrNull(6) as? Float ?: 0f,
                    bottom = it.getOrNull(7) as? Float ?: 0f,
                ).normalized()
            } else {
                null
            }
            val modeIndex = if (usesPolygonFormat) 4 else 8
            activityZoneMode = (it.getOrNull(modeIndex) as? String)
                ?.let { name -> runCatching { ActivityZoneMode.valueOf(name) }.getOrNull() }
                ?: ActivityZoneMode.Safe
        }
    },
)

private val ControlsUiStateSaver = listSaver<ControlsUiState, Any>(
    save = { listOf(it.lullabyLevel, it.lullabyEnabled, it.autoPlayOnCry) },
    restore = {
        ControlsUiState().apply {
            lullabyLevel = it.getOrNull(0) as? Float ?: 0.6f
            lullabyEnabled = it.getOrNull(1) as? Boolean ?: true
            autoPlayOnCry = it.getOrNull(2) as? Boolean ?: false
        }
    },
)

private val AlertSettingsStateSaver = listSaver<AlertSettingsState, Any>(
    save = {
        listOf(
            it.faceAlert,
            it.noPersonAlert,
            it.cryAlert,
            it.boundaryAlert,
        )
    },
    restore = {
        AlertSettingsState().apply {
            faceAlert = it.getOrNull(0) as? Boolean ?: true
            noPersonAlert = if (it.size >= 3) it.getOrNull(1) as? Boolean ?: true else true
            cryAlert = if (it.size >= 3) {
                it.getOrNull(2) as? Boolean ?: true
            } else {
                it.getOrNull(1) as? Boolean ?: true
            }
            boundaryAlert = it.getOrNull(3) as? Boolean ?: true
        }
    },
)

private val FamilySettingsStateSaver = listSaver<FamilySettingsState, Any>(
    save = { listOf(it.inviteCount) },
    restore = {
        FamilySettingsState().apply {
            inviteCount = it[0] as Int
        }
    },
)

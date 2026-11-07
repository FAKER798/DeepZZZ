package com.naviga.app

import android.app.Activity
import android.content.pm.ActivityInfo
import android.graphics.BitmapFactory
import androidx.activity.compose.BackHandler
import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.core.tween
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.gestures.detectDragGestures
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.rounded.ArrowBack
import androidx.compose.material.icons.automirrored.rounded.VolumeUp
import androidx.compose.material.icons.rounded.CheckCircle
import androidx.compose.material.icons.rounded.Fullscreen
import androidx.compose.material.icons.rounded.FullscreenExit
import androidx.compose.material.icons.rounded.Mic
import androidx.compose.material.icons.rounded.MicOff
import androidx.compose.material.icons.rounded.Pause
import androidx.compose.material.icons.rounded.PhotoCamera
import androidx.compose.material.icons.rounded.PlayArrow
import androidx.compose.material3.ElevatedCard
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.layout.onSizeChanged
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalView
import androidx.compose.ui.unit.IntSize
import androidx.compose.ui.unit.dp
import androidx.core.view.WindowCompat
import androidx.core.view.WindowInsetsCompat
import androidx.core.view.WindowInsetsControllerCompat
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.withContext
import java.net.HttpURLConnection
import java.net.URL

@Composable
fun OverviewPage(
    context: android.content.Context,
    uiState: OverviewUiState = remember { sampleOverviewUiState() },
    cameraFrame: androidx.compose.ui.graphics.ImageBitmap?,
    cameraLive: Boolean,
    onCameraLiveChange: (Boolean) -> Unit,
    listenEnabled: Boolean,
    onListenToggle: () -> Unit,
    voiceRecording: Boolean,
    onVoicePressStart: () -> Unit,
    onVoicePressEnd: () -> Unit,
    onSnapshot: () -> Unit,
    onToggleFullscreen: () -> Unit,
    onAcknowledge: (String) -> Unit,
) {
    LaunchedEffect(uiState.statusMetrics) {
        NotificationHelper.showStatusNotification(context, uiState.statusMetrics)
    }

    PageColumn {
        item {
            PageHeader(title = "总览")
        }
        item {
            LiveCameraCard(
                cameraLive = cameraLive,
                cameraFrame = cameraFrame,
                onCameraLiveChange = onCameraLiveChange,
                listenEnabled = listenEnabled,
                onListenToggle = onListenToggle,
                voiceRecording = voiceRecording,
                onVoicePressStart = onVoicePressStart,
                onVoicePressEnd = onVoicePressEnd,
                onSnapshot = onSnapshot,
                onToggleFullscreen = onToggleFullscreen,
            )
        }
        item {
            MetricBars(metrics = uiState.metrics)
        }
        if (uiState.events.isNotEmpty()) {
            item {
                SectionHeader(title = "最近警告")
            }
            items(uiState.events.size) { index ->
                val event = uiState.events[index]
                AlertEventCard(
                    event = event,
                    onAcknowledge = { onAcknowledge(event.id) },
                )
            }
        }
    }
}

@Composable
fun MetricBars(metrics: List<Metric>) {
    Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
        metrics.forEach { metric ->
            MetricBar(metric = metric)
        }
    }
}

@Composable
private fun MetricBar(metric: Metric) {
    PressedCard(contentPadding = PaddingValues(horizontal = 14.dp, vertical = 12.dp)) {
        Row(
            modifier = Modifier.fillMaxWidth(),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            IconBadge(icon = metric.icon)
            Text(
                text = metric.label,
                style = MaterialTheme.typography.titleMedium,
                color = MaterialTheme.colorScheme.onSurface,
                modifier = Modifier.weight(1f),
            )
            Row(verticalAlignment = Alignment.Bottom) {
                Text(
                    text = metric.value,
                    style = MaterialTheme.typography.titleLarge,
                    color = MaterialTheme.colorScheme.onSurface,
                )
                Spacer(Modifier.width(4.dp))
                Text(
                    text = metric.unit,
                    style = MaterialTheme.typography.labelMedium,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier.padding(bottom = 2.dp),
                )
            }
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun FullscreenCameraView(
    cameraFrame: androidx.compose.ui.graphics.ImageBitmap?,
    cameraLive: Boolean,
    onCameraLiveChange: (Boolean) -> Unit,
    onExitFullscreen: () -> Unit,
) {
    val context = LocalView.current.context as Activity
    val view = LocalView.current
    val window = context.window
    val insetsController = WindowCompat.getInsetsController(window, view)

    DisposableEffect(Unit) {
        val original = context.requestedOrientation
        context.requestedOrientation = ActivityInfo.SCREEN_ORIENTATION_LANDSCAPE
        insetsController.hide(WindowInsetsCompat.Type.systemBars())
        insetsController.systemBarsBehavior =
            WindowInsetsControllerCompat.BEHAVIOR_SHOW_TRANSIENT_BARS_BY_SWIPE
        onDispose {
            context.requestedOrientation = original
            insetsController.show(WindowInsetsCompat.Type.systemBars())
        }
    }

    BackHandler { onExitFullscreen() }

    var controlsVisible by remember { mutableStateOf(true) }
    val controlsAlpha by animateFloatAsState(
        targetValue = if (controlsVisible) 1f else 0f,
        animationSpec = tween(durationMillis = 250),
        label = "fullscreenControlsAlpha",
    )

    LaunchedEffect(controlsVisible) {
        if (controlsVisible) {
            delay(3500)
            controlsVisible = false
        }
    }

    LaunchedEffect(cameraLive) {
        controlsVisible = true
    }

    Box(
        modifier = Modifier
            .fillMaxSize()
            .background(Color.Black)
            .clickable(
                interactionSource = remember { MutableInteractionSource() },
                indication = null,
            ) { controlsVisible = !controlsVisible },
    ) {
        CameraStreamView(
            frame = cameraFrame,
            cameraLive = cameraLive,
            modifier = Modifier.fillMaxSize(),
        )

        Row(
            modifier = Modifier
                .fillMaxWidth()
                .graphicsLayer { alpha = controlsAlpha }
                .background(
                    Brush.verticalGradient(
                        listOf(Color.Black.copy(alpha = 0.55f), Color.Transparent),
                    ),
                )
                .padding(horizontal = 12.dp, vertical = 10.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            PlayerButton(
                icon = Icons.AutoMirrored.Rounded.ArrowBack,
                onClick = onExitFullscreen,
            )
            Spacer(Modifier.width(12.dp))
            Text(
                text = "实时看护",
                color = Color.White,
                style = MaterialTheme.typography.titleMedium,
            )
            Spacer(Modifier.width(8.dp))
            if (cameraLive) {
                Box(
                    modifier = Modifier
                        .size(8.dp)
                        .clip(CircleShape)
                        .background(MaterialTheme.colorScheme.primary),
                )
                Spacer(Modifier.width(6.dp))
                Text(
                    text = "直播中",
                    color = Color.White.copy(alpha = 0.8f),
                    style = MaterialTheme.typography.bodySmall,
                )
            }
        }

        if (!cameraLive) {
            Surface(
                onClick = { onCameraLiveChange(true) },
                shape = CircleShape,
                color = Color.Black.copy(alpha = 0.45f),
                modifier = Modifier
                    .align(Alignment.Center)
                    .size(64.dp),
            ) {
                Icon(
                    imageVector = Icons.Rounded.PlayArrow,
                    contentDescription = "播放",
                    tint = Color.White,
                    modifier = Modifier.padding(16.dp),
                )
            }
        }

        Row(
            modifier = Modifier
                .fillMaxWidth()
                .align(Alignment.BottomStart)
                .graphicsLayer { alpha = controlsAlpha }
                .background(
                    Brush.verticalGradient(
                        listOf(Color.Transparent, Color.Black.copy(alpha = 0.55f)),
                    ),
                )
                .padding(horizontal = 12.dp, vertical = 10.dp),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically,
        ) {
            PlayerButton(
                icon = if (cameraLive) Icons.Rounded.Pause else Icons.Rounded.PlayArrow,
                onClick = { onCameraLiveChange(!cameraLive) },
            )
            PlayerButton(
                icon = Icons.Rounded.FullscreenExit,
                onClick = onExitFullscreen,
            )
        }
    }
}

@Composable
fun LiveCameraCard(
    cameraLive: Boolean,
    cameraFrame: androidx.compose.ui.graphics.ImageBitmap?,
    onCameraLiveChange: (Boolean) -> Unit,
    listenEnabled: Boolean,
    onListenToggle: () -> Unit,
    voiceRecording: Boolean,
    onVoicePressStart: () -> Unit,
    onVoicePressEnd: () -> Unit,
    onSnapshot: () -> Unit,
    onToggleFullscreen: () -> Unit,
) {
    ElevatedCard(
        modifier = Modifier.fillMaxWidth(),
        shape = CardShape,
        colors = CardDefaults.elevatedCardColors(containerColor = MaterialTheme.colorScheme.surface),
    ) {
        Column(
            modifier = Modifier.padding(16.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            Box(
                modifier = Modifier
                    .fillMaxWidth()
                    .aspectRatio(16f / 9f)
                    .clip(RoundedCornerShape(24.dp))
                    .background(Color.Black),
            ) {
                CameraStreamView(
                    frame = cameraFrame,
                    cameraLive = cameraLive,
                    modifier = Modifier.fillMaxSize(),
                )

                Row(
                    modifier = Modifier
                        .align(Alignment.BottomCenter)
                        .fillMaxWidth()
                        .background(
                            Brush.verticalGradient(
                                listOf(Color.Transparent, Color.Black.copy(alpha = 0.45f)),
                            ),
                        )
                        .padding(horizontal = 16.dp, vertical = 10.dp),
                    horizontalArrangement = Arrangement.SpaceBetween,
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    PlayerButton(
                        icon = if (cameraLive) Icons.Rounded.Pause else Icons.Rounded.PlayArrow,
                        onClick = { onCameraLiveChange(!cameraLive) },
                    )
                    PlayerButton(
                        icon = Icons.Rounded.Fullscreen,
                        onClick = onToggleFullscreen,
                    )
                }
            }

            Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                MiniActionButton(
                    label = "倾听",
                    icon = Icons.AutoMirrored.Rounded.VolumeUp,
                    selected = listenEnabled,
                    onClick = onListenToggle,
                    enabled = cameraLive,
                    modifier = Modifier.weight(1f),
                )
                HoldActionButton(
                    label = if (voiceRecording) "松开发送" else "语音",
                    icon = if (voiceRecording) Icons.Rounded.MicOff else Icons.Rounded.Mic,
                    selected = voiceRecording,
                    onPressStart = onVoicePressStart,
                    onPressEnd = onVoicePressEnd,
                    enabled = cameraLive,
                    modifier = Modifier.weight(1f),
                )
                MiniActionButton(
                    label = "抓拍",
                    icon = Icons.Rounded.PhotoCamera,
                    selected = false,
                    onClick = onSnapshot,
                    enabled = cameraLive,
                    modifier = Modifier.weight(1f),
                )
            }
        }
    }
}

@Composable
fun CameraStreamView(
    frame: androidx.compose.ui.graphics.ImageBitmap?,
    cameraLive: Boolean,
    modifier: Modifier = Modifier,
) {
    Box(modifier = modifier.background(Color.Black), contentAlignment = Alignment.Center) {
        frame?.let {
            Image(
                bitmap = it,
                contentDescription = null,
                contentScale = ContentScale.Crop,
                modifier = Modifier.fillMaxSize(),
            )
        }
    }
}

internal suspend fun loadLatestCameraFrame(snapshotUrl: String) = withContext(Dispatchers.IO) {
    runCatching {
        val separator = if (snapshotUrl.contains("?")) "&" else "?"
        val connection = URL("$snapshotUrl${separator}t=${System.currentTimeMillis()}")
            .openConnection() as HttpURLConnection
        try {
            connection.connectTimeout = CAMERA_REQUEST_TIMEOUT_MS
            connection.readTimeout = CAMERA_REQUEST_TIMEOUT_MS
            connection.useCaches = false
            connection.setRequestProperty("Cache-Control", "no-cache, no-store")
            connection.inputStream.use(BitmapFactory::decodeStream)?.asImageBitmap()
        } finally {
            connection.disconnect()
        }
    }.getOrNull()
}

@Composable
fun SafetyZoneOverlay(
    safetyZone: SafetyZone?,
    editing: Boolean,
    zoneColor: Color = MaterialTheme.colorScheme.primary,
    onSafetyZoneChange: (SafetyZone) -> Unit,
    modifier: Modifier = Modifier,
    drawing: Boolean = false,
    draftPoints: List<ZonePoint> = emptyList(),
    onDraftPointsChange: (List<ZonePoint>) -> Unit = {},
) {
    var canvasSize by remember { mutableStateOf(IntSize.Zero) }
    var dragIndex by remember { mutableStateOf<Int?>(null) }
    var dragPreview by remember { mutableStateOf<List<ZonePoint>?>(null) }
    val zone = safetyZone?.normalized()
    val basePoints = if (drawing) draftPoints else zone?.points.orEmpty()
    val displayPoints = dragPreview ?: basePoints

    Canvas(
        modifier = modifier
            .onSizeChanged { canvasSize = it }
            .pointerInput(editing, drawing, canvasSize, basePoints) {
                if (!editing || canvasSize.width <= 0 || canvasSize.height <= 0) return@pointerInput
                detectTapGestures { offset ->
                    if (!drawing || basePoints.size >= MAX_ZONE_POINTS) return@detectTapGestures
                    val width = canvasSize.width.toFloat().coerceAtLeast(1f)
                    val height = canvasSize.height.toFloat().coerceAtLeast(1f)
                    onDraftPointsChange(
                        (basePoints + ZonePoint(offset.x / width, offset.y / height).normalized())
                            .distinct(),
                    )
                }
            }
            .pointerInput(editing, drawing, canvasSize, basePoints) {
                if (!editing || canvasSize.width <= 0 || canvasSize.height <= 0) return@pointerInput
                detectDragGestures(
                    onDragStart = { offset ->
                        val width = canvasSize.width.toFloat().coerceAtLeast(1f)
                        val height = canvasSize.height.toFloat().coerceAtLeast(1f)
                        val nearest = basePoints.withIndex()
                            .minByOrNull { (_, point) ->
                                val dx = point.x * width - offset.x
                                val dy = point.y * height - offset.y
                                dx * dx + dy * dy
                            }
                        val hitRadius = 32.dp.toPx()
                        if (nearest != null) {
                            val dx = nearest.value.x * width - offset.x
                            val dy = nearest.value.y * height - offset.y
                            if (dx * dx + dy * dy <= hitRadius * hitRadius) {
                                dragIndex = nearest.index
                                dragPreview = basePoints
                            }
                        }
                    },
                    onDrag = { change, _ ->
                        val index = dragIndex ?: return@detectDragGestures
                        val width = canvasSize.width.toFloat().coerceAtLeast(1f)
                        val height = canvasSize.height.toFloat().coerceAtLeast(1f)
                        val next = (dragPreview ?: basePoints).toMutableList()
                        next[index] = ZonePoint(
                            change.position.x / width,
                            change.position.y / height,
                        ).normalized()
                        dragPreview = next
                    },
                    onDragCancel = {
                        dragIndex = null
                        dragPreview = null
                    },
                    onDragEnd = {
                        val next = dragPreview
                        if (next != null && next.size >= 3) {
                            if (drawing) onDraftPointsChange(next)
                            else onSafetyZoneChange(SafetyZone(next).normalized())
                        }
                        dragIndex = null
                        dragPreview = null
                    },
                )
            },
    ) {
        if (displayPoints.isEmpty()) return@Canvas
        val path = Path().apply {
            displayPoints.forEachIndexed { index, point ->
                val offset = Offset(point.x * size.width, point.y * size.height)
                if (index == 0) moveTo(offset.x, offset.y) else lineTo(offset.x, offset.y)
            }
            if (!drawing && displayPoints.size >= 3) close()
        }
        if (!drawing && displayPoints.size >= 3) {
            drawPath(path, color = zoneColor.copy(alpha = 0.16f))
        }
        drawPath(path, color = zoneColor, style = Stroke(width = 3.dp.toPx()))
        displayPoints.forEach { point ->
            drawCircle(
                color = zoneColor,
                radius = 8.dp.toPx(),
                center = Offset(point.x * size.width, point.y * size.height),
            )
            drawCircle(
                color = Color.White,
                radius = 3.dp.toPx(),
                center = Offset(point.x * size.width, point.y * size.height),
            )
        }
    }
}

private const val MAX_ZONE_POINTS = 12

private const val CAMERA_REQUEST_TIMEOUT_MS = 1_200
internal const val CAMERA_FRAME_INTERVAL_MS = 33L

@Composable
fun AlertEventCard(
    event: AlertEvent,
    onAcknowledge: () -> Unit,
) {
    val thumbnail = remember(event.id, event.thumbnailJpeg) {
        event.thumbnailJpeg
            ?.takeIf { it.isNotEmpty() }
            ?.let { BitmapFactory.decodeByteArray(it, 0, it.size) }
            ?.asImageBitmap()
    }
    PressedCard(
        contentPadding = PaddingValues(14.dp),
    ) {
        Row(
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            IconBadge(icon = event.icon, tint = MaterialTheme.colorScheme.error)
            Column(modifier = Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(3.dp)) {
                Text(text = event.title, style = MaterialTheme.typography.titleMedium)
                Text(
                    text = event.timestamp,
                    style = MaterialTheme.typography.labelLarge,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
            if (thumbnail != null) {
                Image(
                    bitmap = thumbnail,
                    contentDescription = "${event.title}判定画面",
                    contentScale = ContentScale.Crop,
                    modifier = Modifier
                        .size(width = 72.dp, height = 48.dp)
                        .clip(RoundedCornerShape(6.dp)),
                )
            }
            IconButton(
                onClick = onAcknowledge,
            ) {
                Icon(
                    imageVector = Icons.Rounded.CheckCircle,
                    contentDescription = "标记已处理",
                    tint = MaterialTheme.colorScheme.primary,
                )
            }
        }
    }
}

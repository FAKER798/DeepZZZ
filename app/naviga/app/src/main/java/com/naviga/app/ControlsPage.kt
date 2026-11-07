package com.naviga.app

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.rounded.QueueMusic
import androidx.compose.material.icons.automirrored.rounded.VolumeUp
import androidx.compose.material.icons.rounded.Check
import androidx.compose.material.icons.rounded.LibraryMusic
import androidx.compose.material.icons.rounded.Pause
import androidx.compose.material.icons.rounded.PlayArrow
import androidx.compose.material.icons.rounded.Repeat
import androidx.compose.material.icons.rounded.RepeatOne
import androidx.compose.material.icons.rounded.SkipNext
import androidx.compose.material.icons.rounded.SkipPrevious
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.FilledIconButton
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FilledTonalButton
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.ListItem
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.Slider
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.layout.Layout
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import java.util.Locale

@Composable
fun ControlsPage(
    musicVolume: Float,
    onMusicVolumeChange: (Float) -> Unit,
    selectedTrack: MusicTrack?,
    tracks: List<MusicTrack>,
    playerPlaying: Boolean,
    repeatOne: Boolean,
    positionMillis: Long,
    onSelectTrack: (MusicTrack) -> Unit,
    onPlayPause: () -> Unit,
    onRepeatModeChange: (Boolean) -> Unit,
    onSeek: (Long) -> Unit,
    onPrevious: () -> Unit,
    onNext: () -> Unit,
) {
    var showLibrary by remember { mutableStateOf(false) }
    Column(
        modifier = Modifier.fillMaxSize().padding(horizontal = 18.dp, vertical = 18.dp),
    ) {
        PageHeader(title = "摇篮曲")
        Spacer(Modifier.height(16.dp))
        PlayerCard(
            modifier = Modifier.weight(1f),
            musicVolume = musicVolume,
            onMusicVolumeChange = onMusicVolumeChange,
            selectedTrack = selectedTrack,
            trackCount = tracks.size,
            playerPlaying = playerPlaying,
            repeatOne = repeatOne,
            positionMillis = positionMillis,
            onOpenLibrary = { showLibrary = true },
            onPlayPause = onPlayPause,
            onRepeatModeChange = onRepeatModeChange,
            onSeek = onSeek,
            onPrevious = onPrevious,
            onNext = onNext,
        )
    }

    if (showLibrary) {
        TrackLibrarySheet(
            tracks = tracks,
            selectedTrack = selectedTrack,
            onDismiss = { showLibrary = false },
            onSelect = {
                onSelectTrack(it)
                showLibrary = false
            },
        )
    }
}

@Composable
private fun PlayerCard(
    modifier: Modifier = Modifier,
    musicVolume: Float,
    onMusicVolumeChange: (Float) -> Unit,
    selectedTrack: MusicTrack?,
    trackCount: Int,
    playerPlaying: Boolean,
    repeatOne: Boolean,
    positionMillis: Long,
    onOpenLibrary: () -> Unit,
    onPlayPause: () -> Unit,
    onRepeatModeChange: (Boolean) -> Unit,
    onSeek: (Long) -> Unit,
    onPrevious: () -> Unit,
    onNext: () -> Unit,
) {
    PressedCard(
        modifier = modifier,
        contentPadding = PaddingValues(horizontal = 18.dp, vertical = 20.dp),
        contentModifier = Modifier.fillMaxSize(),
    ) {
        PlayerContentLayout(
            title = selectedTrack?.title ?: "暂无摇篮曲",
            trackCount = trackCount,
            durationMillis = selectedTrack?.durationMillis ?: 0L,
            positionMillis = positionMillis,
            controlsEnabled = selectedTrack != null,
            playing = playerPlaying,
            repeatOne = repeatOne,
            volume = musicVolume,
            onOpenLibrary = onOpenLibrary,
            onSeek = onSeek,
            onPrevious = onPrevious,
            onPlayPause = onPlayPause,
            onNext = onNext,
            onRepeatModeChange = onRepeatModeChange,
            onVolumeChange = onMusicVolumeChange,
        )
    }
}

@Composable
private fun PlayerContentLayout(
    title: String,
    trackCount: Int,
    durationMillis: Long,
    positionMillis: Long,
    controlsEnabled: Boolean,
    playing: Boolean,
    repeatOne: Boolean,
    volume: Float,
    onOpenLibrary: () -> Unit,
    onSeek: (Long) -> Unit,
    onPrevious: () -> Unit,
    onPlayPause: () -> Unit,
    onNext: () -> Unit,
    onRepeatModeChange: (Boolean) -> Unit,
    onVolumeChange: (Float) -> Unit,
) {
    Layout(
        modifier = Modifier.fillMaxSize(),
        content = {
            Text(
                text = title,
                modifier = Modifier.fillMaxWidth(),
                style = MaterialTheme.typography.headlineSmall,
                fontWeight = FontWeight.SemiBold,
                textAlign = TextAlign.Center,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
            )
            PlaybackTimeline(
                positionMillis = positionMillis,
                durationMillis = durationMillis,
                enabled = controlsEnabled,
                onSeek = onSeek,
            )
            FilledTonalButton(
                onClick = onOpenLibrary,
                modifier = Modifier.fillMaxWidth(),
                enabled = trackCount > 0,
                shape = ControlShape,
            ) {
                Icon(Icons.AutoMirrored.Rounded.QueueMusic, contentDescription = null)
                Text("曲库 · $trackCount 首", modifier = Modifier.padding(start = 8.dp))
            }
            PlaybackControls(
                enabled = controlsEnabled,
                playing = playing,
                repeatOne = repeatOne,
                volume = volume,
                onPrevious = onPrevious,
                onPlayPause = onPlayPause,
                onNext = onNext,
                onRepeatModeChange = onRepeatModeChange,
                onVolumeChange = onVolumeChange,
            )
            Icon(
                imageVector = Icons.Rounded.LibraryMusic,
                contentDescription = null,
                tint = MaterialTheme.colorScheme.primary,
                modifier = Modifier.size(112.dp),
            )
        },
    ) { measurables, constraints ->
        val childConstraints = constraints.copy(minHeight = 0)
        val titlePlaceable = measurables[0].measure(childConstraints)
        val timelinePlaceable = measurables[1].measure(childConstraints)
        val libraryPlaceable = measurables[2].measure(childConstraints)
        val controlsPlaceable = measurables[3].measure(childConstraints)
        val musicIconPlaceable = measurables[4].measure(childConstraints.copy(minWidth = 0))
        val spacing = 6.dp.roundToPx()
        val fixedHeight = titlePlaceable.height + timelinePlaceable.height +
            libraryPlaceable.height + controlsPlaceable.height + spacing * 6
        val flexibleHeight = (constraints.maxHeight - fixedHeight).coerceAtLeast(0)
        val topSpace = (flexibleHeight * 0.8f / 2.5f).toInt()
        val middleSpace = (flexibleHeight * 1f / 2.5f).toInt()
        val bottomSpace = flexibleHeight - topSpace - middleSpace

        val timelineY = titlePlaceable.height + spacing + topSpace + spacing +
            libraryPlaceable.height + spacing + middleSpace + spacing
        val controlsY = timelineY + timelinePlaceable.height + spacing + bottomSpace + spacing
        val libraryGapTop = timelineY + timelinePlaceable.height
        val libraryY = libraryGapTop +
            ((controlsY - libraryGapTop - libraryPlaceable.height) / 2).coerceAtLeast(0)
        val iconGapTop = titlePlaceable.height
        val musicIconY = iconGapTop +
            ((timelineY - iconGapTop - musicIconPlaceable.height) / 2).coerceAtLeast(0)
        val musicIconX = (constraints.maxWidth - musicIconPlaceable.width) / 2

        layout(constraints.maxWidth, constraints.maxHeight) {
            titlePlaceable.placeRelative(0, 0)
            timelinePlaceable.placeRelative(0, timelineY)
            libraryPlaceable.placeRelative(0, libraryY)
            controlsPlaceable.placeRelative(0, controlsY)
            musicIconPlaceable.placeRelative(musicIconX, musicIconY)
        }
    }
}

@Composable
private fun PlaybackTimeline(
    positionMillis: Long,
    durationMillis: Long,
    enabled: Boolean,
    onSeek: (Long) -> Unit,
) {
    val duration = durationMillis.coerceAtLeast(1L)
    var dragging by remember { mutableStateOf(false) }
    var sliderPosition by remember { mutableFloatStateOf(positionMillis.toFloat()) }
    LaunchedEffect(positionMillis, dragging) {
        if (!dragging) sliderPosition = positionMillis.coerceIn(0L, duration).toFloat()
    }
    Column(modifier = Modifier.padding(top = 18.dp)) {
        Slider(
            value = sliderPosition.coerceIn(0f, duration.toFloat()),
            onValueChange = {
                dragging = true
                sliderPosition = it
            },
            onValueChangeFinished = {
                dragging = false
                onSeek(sliderPosition.toLong())
            },
            valueRange = 0f..duration.toFloat(),
            enabled = enabled && durationMillis > 0L,
        )
        Row(modifier = Modifier.fillMaxWidth()) {
            Text(formatDuration(sliderPosition.toLong()), style = MaterialTheme.typography.labelMedium)
            Text(
                formatDuration(durationMillis),
                modifier = Modifier.weight(1f),
                style = MaterialTheme.typography.labelMedium,
                textAlign = TextAlign.End,
            )
        }
    }
}

@Composable
private fun PlaybackControls(
    enabled: Boolean,
    playing: Boolean,
    repeatOne: Boolean,
    volume: Float,
    onPrevious: () -> Unit,
    onPlayPause: () -> Unit,
    onNext: () -> Unit,
    onRepeatModeChange: (Boolean) -> Unit,
    onVolumeChange: (Float) -> Unit,
) {
    var showVolume by remember { mutableStateOf(false) }
    Row(
        modifier = Modifier.fillMaxWidth().height(74.dp).padding(vertical = 8.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Box(modifier = Modifier.weight(1f), contentAlignment = Alignment.Center) {
            IconButton(onClick = { onRepeatModeChange(!repeatOne) }, enabled = enabled) {
                Icon(
                    if (repeatOne) Icons.Rounded.RepeatOne else Icons.Rounded.Repeat,
                    contentDescription = if (repeatOne) "单曲循环" else "列表循环",
                    modifier = Modifier.size(28.dp),
                )
            }
        }
        Box(modifier = Modifier.weight(1f), contentAlignment = Alignment.Center) {
            IconButton(onClick = onPrevious, enabled = enabled) {
                Icon(Icons.Rounded.SkipPrevious, contentDescription = "上一首", modifier = Modifier.size(28.dp))
            }
        }
        Box(modifier = Modifier.weight(1f), contentAlignment = Alignment.Center) {
            FilledIconButton(onClick = onPlayPause, enabled = enabled, modifier = Modifier.size(58.dp)) {
                Icon(
                    if (playing) Icons.Rounded.Pause else Icons.Rounded.PlayArrow,
                    contentDescription = if (playing) "暂停" else "播放",
                    modifier = Modifier.size(32.dp),
                )
            }
        }
        Box(modifier = Modifier.weight(1f), contentAlignment = Alignment.Center) {
            IconButton(onClick = onNext, enabled = enabled) {
                Icon(Icons.Rounded.SkipNext, contentDescription = "下一首", modifier = Modifier.size(28.dp))
            }
        }
        Box(modifier = Modifier.weight(1f), contentAlignment = Alignment.Center) {
            IconButton(onClick = { showVolume = true }, enabled = enabled) {
                Icon(Icons.AutoMirrored.Rounded.VolumeUp, contentDescription = "音量", modifier = Modifier.size(28.dp))
            }
            DropdownMenu(
                expanded = showVolume,
                onDismissRequest = { showVolume = false },
                modifier = Modifier.width(260.dp),
            ) {
                Column(modifier = Modifier.padding(horizontal = 16.dp, vertical = 8.dp)) {
                    Text(
                        "${(volume * 100).toInt()}%",
                        modifier = Modifier.fillMaxWidth(),
                        style = MaterialTheme.typography.labelLarge,
                        textAlign = TextAlign.Center,
                    )
                    Slider(value = volume, onValueChange = onVolumeChange)
                }
            }
        }
    }
}

@Composable
@OptIn(ExperimentalMaterial3Api::class)
private fun TrackLibrarySheet(
    tracks: List<MusicTrack>,
    selectedTrack: MusicTrack?,
    onDismiss: () -> Unit,
    onSelect: (MusicTrack) -> Unit,
) {
    ModalBottomSheet(onDismissRequest = onDismiss) {
        Text(
            "摇篮曲曲库",
            style = MaterialTheme.typography.titleLarge,
            fontWeight = FontWeight.SemiBold,
            modifier = Modifier.padding(horizontal = 24.dp, vertical = 8.dp),
        )
        tracks.forEach { track ->
            ListItem(
                headlineContent = { Text(track.title, maxLines = 1, overflow = TextOverflow.Ellipsis) },
                supportingContent = { Text(formatDuration(track.durationMillis ?: 0L)) },
                leadingContent = { Icon(Icons.Rounded.LibraryMusic, contentDescription = null) },
                trailingContent = {
                    if (track.boardTrackId == selectedTrack?.boardTrackId) {
                        Icon(Icons.Rounded.Check, contentDescription = "当前曲目", tint = MaterialTheme.colorScheme.primary)
                    }
                },
                modifier = Modifier.fillMaxWidth().clickable { onSelect(track) },
            )
            androidx.compose.material3.HorizontalDivider()
        }
        Spacer(Modifier.height(24.dp))
    }
}

private fun formatDuration(durationMillis: Long): String {
    val totalSeconds = (durationMillis / 1_000L).coerceAtLeast(0L)
    return String.format(Locale.US, "%d:%02d", totalSeconds / 60L, totalSeconds % 60L)
}

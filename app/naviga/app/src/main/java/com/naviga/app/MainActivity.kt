package com.naviga.app

import android.Manifest
import android.app.Activity
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Bundle
import android.os.Build
import android.os.SystemClock
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.compose.foundation.background
import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.consumeWindowInsets
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Scaffold
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.runtime.toMutableStateList
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.ImageBitmap
import androidx.compose.ui.platform.LocalView
import androidx.core.content.ContextCompat
import androidx.core.view.WindowCompat
import androidx.core.view.WindowInsetsCompat
import androidx.core.view.WindowInsetsControllerCompat
import com.naviga.app.ui.theme.NavigaTheme
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

class MainActivity : ComponentActivity() {
    private lateinit var performanceModeController: PerformanceModeController

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        NotificationHelper.initChannels(this)
        NotificationHelper.requestPermission(this)
        ContextCompat.startForegroundService(this, Intent(this, CareForegroundService::class.java))
        performanceModeController = PerformanceModeController(this)
        lifecycle.addObserver(performanceModeController)
        setContent {
            NavigaRoot()
    }
}

@Composable
private fun NavigaRoot() {
    val systemDark = isSystemInDarkTheme()
    var themeMode by rememberSaveable { mutableStateOf(ThemeMode.System.name) }
    val mode = ThemeMode.valueOf(themeMode)
    val darkTheme = when (mode) {
        ThemeMode.Light -> false
        ThemeMode.Dark -> true
        ThemeMode.System -> systemDark
    }

    NavigaTheme(darkTheme = darkTheme) {
        NavigaApp(
            themeMode = mode,
            onThemeModeChange = { themeMode = it.name },
        )
    }
}

@Composable
private fun NavigaApp(
    themeMode: ThemeMode,
    onThemeModeChange: (ThemeMode) -> Unit,
) {
    var selectedTab by rememberSaveable { mutableStateOf(AppTab.Overview) }
    val view = LocalView.current
    val boardConnection = remember { BoardConnectionSession() }
    val boardApi = boardConnection.api
    val cameraSnapshotUrl = remember(boardApi) { boardApi.snapshotUrl() }
    val environmentDataSource = remember(boardApi) { BoardEnvironmentDataSource(boardApi) }
    val alertDataSource = remember(boardApi) { BoardAlertDataSourceAdapter(boardApi) }
    var environmentReading by remember { mutableStateOf(StubEnvironmentDataSource().currentReading()) }
    var retainedAlerts by remember { mutableStateOf(emptyMap<BoardAlertType, BoardAlert>()) }
    var activeBoardAlerts by remember { mutableStateOf(emptyList<BoardAlert>()) }
    val albumStore = remember(view.context) { GrowthAlbumStore(view.context) }
    val albumImageLoader = remember(view.context) { GrowthAlbumImageLoader(view.context) }
    val albumSyncClient = remember(boardApi) { BoardGrowthAlbumSyncClient(boardApi) }
    val snackbarHostState = remember { SnackbarHostState() }
    val scope = rememberCoroutineScope()
    val talkAudioStreamer = remember(view.context, scope) {
        TalkAudioStreamer(view.context.applicationContext, scope)
    }
    val growthMoments = remember(albumStore) { albumStore.loadMoments().toMutableStateList() }
    var albumFolderLabel by remember(albumStore) { mutableStateOf(albumStore.albumFolderLabel()) }
    val cameraState = rememberCameraUiState()
    var cameraFrame by remember { mutableStateOf<ImageBitmap?>(null) }
    val controlsState = rememberControlsUiState()
    val alertSettingsState = rememberAlertSettingsState()
    val familySettingsState = rememberFamilySettingsState()
    var acknowledgedAlertIds by rememberSaveable { mutableStateOf(emptyList<String>()) }
    val musicFilePlayer = remember(boardApi) {
        MusicFilePlayer { boardApi }
    }
    var musicTracks by remember { mutableStateOf(emptyList<MusicTrack>()) }
    var selectedMusicTrack by remember { mutableStateOf<MusicTrack?>(null) }
    var musicPlaying by remember { mutableStateOf(false) }
    var musicPaused by remember { mutableStateOf(false) }
    var musicPositionMillis by remember { mutableStateOf(0L) }
    var musicRepeatOne by rememberSaveable { mutableStateOf(true) }
    val musicTasks = remember { LullabyTaskState() }
    val cryPlaybackTrigger = remember { CryPlaybackTriggerState() }
    // Held here so the conversation survives tab switches and an answer keeps streaming.
    val assistantState = remember(boardApi) { AssistantChatState(boardApi, scope) }

    LaunchedEffect(cameraSnapshotUrl, cameraState.live) {
        while (cameraState.live && !cameraSnapshotUrl.isNullOrBlank()) {
            val startedAt = SystemClock.elapsedRealtime()
            loadLatestCameraFrame(cameraSnapshotUrl)?.let { cameraFrame = it }
            val elapsed = SystemClock.elapsedRealtime() - startedAt
            if (elapsed < CAMERA_FRAME_INTERVAL_MS) {
                delay(CAMERA_FRAME_INTERVAL_MS - elapsed)
            }
        }
    }

    val window = (view.context as Activity).window
    val insetsController = WindowCompat.getInsetsController(window, view)

    fun toggleFullscreen() {
        cameraState.fullscreen = !cameraState.fullscreen
        if (cameraState.fullscreen) {
            insetsController.hide(WindowInsetsCompat.Type.systemBars())
            insetsController.systemBarsBehavior =
                WindowInsetsControllerCompat.BEHAVIOR_SHOW_TRANSIENT_BARS_BY_SWIPE
        } else {
            insetsController.show(WindowInsetsCompat.Type.systemBars())
        }
    }

    val showMessage: (String) -> Unit = { message ->
        scope.launch { snackbarHostState.showSnackbar(message) }
    }
    val refreshAlbumMoments = {
        growthMoments.clear()
        growthMoments.addAll(albumStore.loadMoments())
    }
    val recordPermissionLauncher = rememberLauncherForActivityResult(
        contract = ActivityResultContracts.RequestPermission(),
    ) { granted ->
        if (granted && talkAudioStreamer.startVoiceMessage(
                boardApiProvider = { boardApi },
                onSent = { sent, durationMillis ->
                    cameraState.voiceRecording = false
                    showMessage(
                        when {
                            durationMillis < 350L -> "语音太短"
                            sent -> "语音已发送"
                            else -> "语音发送失败"
                        },
                    )
                },
            )
        ) {
            cameraState.voiceRecording = true
            showMessage("按住说话")
        } else {
            showMessage("需要麦克风权限")
        }
    }
    fun launchMusicCommand(
        command: suspend () -> MusicPlaybackResult,
        onResult: (MusicPlaybackResult) -> Unit,
    ) {
        if (musicTasks.commandRunning) return
        musicTasks.syncJob?.cancel()
        musicTasks.commandRunning = true
        scope.launch {
            try {
                val result = withContext(Dispatchers.IO) { command() }
                onResult(result)
            } finally {
                musicTasks.commandRunning = false
            }
        }
    }

    fun playSelectedMusic(positionMillis: Long? = null) {
        val track = selectedMusicTrack
        if (track == null) {
            showMessage("板端没有可用摇篮曲")
            return
        }
        val volume = controlsState.lullabyLevel
        val repeatOne = musicRepeatOne
        launchMusicCommand(
            command = {
                musicFilePlayer.play(
                    track = track,
                    volume = volume,
                    positionMillis = positionMillis,
                    repeatOne = repeatOne,
                )
            },
            onResult = { result ->
                musicPlaying = result.ok
                musicPaused = false
                if (result.ok && positionMillis != null) musicPositionMillis = positionMillis
                showMessage(result.message)
            },
        )
    }

    fun pauseSelectedMusic() {
        if (!musicPlaying) return
        launchMusicCommand(
            command = { musicFilePlayer.pause() },
            onResult = { result ->
                if (result.ok) {
                    musicPlaying = false
                    musicPaused = true
                }
            },
        )
    }

    fun seekSelectedMusic(positionMillis: Long) {
        val duration = selectedMusicTrack?.durationMillis ?: return
        val target = positionMillis.coerceIn(0L, duration)
        musicPositionMillis = target
        launchMusicCommand(
            command = { musicFilePlayer.seek(target) },
            onResult = {},
        )
    }

    fun syncBoardLullaby(showError: Boolean = false) {
        if (musicTasks.commandRunning) return
        musicTasks.syncJob?.cancel()
        musicTasks.syncJob = scope.launch {
            val status = withContext(Dispatchers.IO) {
                boardApi.lullabyStatus()
            }
            if (status == null) {
                if (showError) showMessage("读取板端摇篮曲状态失败")
                return@launch
            }
            musicTracks = status.tracks.map { it.toMusicTrack() }
            val selectedId = selectedMusicTrack?.boardTrackId
            val boardTrack = status.currentTrack
                ?: status.tracks.firstOrNull { it.id == selectedId }
                ?: status.tracks.firstOrNull()
            if (boardTrack != null) {
                selectedMusicTrack = boardTrack.toMusicTrack()
                musicPlaying = status.playing
                musicPaused = status.paused
                musicPositionMillis = status.positionMillis
                musicRepeatOne = status.repeatOne
                status.volume?.let { controlsState.lullabyLevel = it.coerceIn(0f, 1f) }
            } else if (selectedMusicTrack == null) {
                musicPlaying = false
                musicPaused = false
            }
        }
    }

    fun toggleListen() {
        if (cameraState.listenEnabled) {
            talkAudioStreamer.stopListening()
            cameraState.listenEnabled = false
            showMessage("倾听已关闭")
            return
        }
        if (talkAudioStreamer.startListening { boardApi }) {
            cameraState.listenEnabled = true
            showMessage("倾听已开启")
        } else {
            showMessage("倾听启动失败")
        }
    }

    fun startVoiceMessage() {
        if (cameraState.voiceRecording) return
        val granted = ContextCompat.checkSelfPermission(
            view.context,
            Manifest.permission.RECORD_AUDIO,
        ) == PackageManager.PERMISSION_GRANTED
        if (!granted) {
            recordPermissionLauncher.launch(Manifest.permission.RECORD_AUDIO)
            return
        }
        val started = talkAudioStreamer.startVoiceMessage(
            boardApiProvider = { boardApi },
            onSent = { sent, durationMillis ->
                cameraState.voiceRecording = false
                showMessage(
                    when {
                        durationMillis < 350L -> "语音太短"
                        sent -> "语音已发送"
                        else -> "语音发送失败"
                    },
                )
            },
        )
        if (started) {
            cameraState.voiceRecording = true
        } else {
            showMessage("语音启动失败")
        }
    }

    fun finishVoiceMessage() {
        if (!cameraState.voiceRecording) return
        talkAudioStreamer.finishVoiceMessage()
    }

    fun applyBoardActivityZone(activityZone: BoardActivityZone) {
        cameraState.safetyZone = activityZone.zone?.normalized()
        cameraState.activityZoneMode = activityZone.mode
    }

    fun syncBoardActivityZone() {
        scope.launch {
            val activityZone = withContext(Dispatchers.IO) {
                boardApi.activityZone()
            }
            if (activityZone != null) {
                applyBoardActivityZone(activityZone)
            }
        }
    }

    fun saveBoardActivityZone(zone: SafetyZone, mode: ActivityZoneMode) {
        scope.launch {
            withContext(Dispatchers.IO) {
                boardApi.setActivityZone(zone.normalized(), mode)
            }
        }
    }

    fun clearBoardActivityZone() {
        scope.launch {
            withContext(Dispatchers.IO) {
                boardApi.clearActivityZone()
            }
        }
    }

    LaunchedEffect(
        selectedTab,
        environmentDataSource,
        alertDataSource,
        alertSettingsState.faceAlert,
        alertSettingsState.noPersonAlert,
        alertSettingsState.cryAlert,
        alertSettingsState.boundaryAlert,
        controlsState.autoPlayOnCry,
        cameraState.safetyZone,
        cameraState.activityZoneMode,
    ) {
        while (true) {
            if (selectedTab == AppTab.Overview) {
                val boardActivityZone = withContext(Dispatchers.IO) {
                    boardApi.activityZone()
                }
                if (boardActivityZone != null) {
                    applyBoardActivityZone(boardActivityZone)
                }
            }
            val settings = alertSettingsState.snapshot(
                safetyZone = cameraState.safetyZone,
                activityZoneMode = cameraState.activityZoneMode,
            )
            if (selectedTab == AppTab.Overview) {
                environmentReading = withContext(Dispatchers.IO) {
                    environmentDataSource.currentReading()
                }
            }
            val alerts = withContext(Dispatchers.IO) {
                alertDataSource.activeAlerts(
                    settings.copy(cryAlert = settings.cryAlert || controlsState.autoPlayOnCry),
                )
            }
            val crying = alerts.any { it.type == BoardAlertType.Crying }
            if (!controlsState.autoPlayOnCry) {
                cryPlaybackTrigger.active = false
                cryPlaybackTrigger.clearPolls = 0
            } else if (!crying) {
                cryPlaybackTrigger.clearPolls += 1
                if (cryPlaybackTrigger.clearPolls >= CRY_CLEAR_CONFIRM_POLLS) {
                    cryPlaybackTrigger.active = false
                }
            } else if (!cryPlaybackTrigger.active) {
                cryPlaybackTrigger.clearPolls = 0
                when {
                    musicPlaying -> cryPlaybackTrigger.active = true
                    selectedMusicTrack != null && !musicTasks.commandRunning -> {
                        cryPlaybackTrigger.active = true
                        playSelectedMusic(0L)
                    }
                }
            } else {
                cryPlaybackTrigger.clearPolls = 0
            }
            val notificationAlerts = alerts.filter {
                it.type != BoardAlertType.Crying || alertSettingsState.cryAlert
            }
            activeBoardAlerts = notificationAlerts
            retainedAlerts = retainedAlerts.withLatestAlerts(notificationAlerts)
            delay(OVERVIEW_REFRESH_INTERVAL_MS)
        }
    }

    LaunchedEffect(boardApi) {
        boardConnection.checkHealth()
        syncBoardActivityZone()
        syncBoardLullaby()
    }

    LaunchedEffect(controlsState.lullabyLevel, musicPlaying) {
        if (!musicPlaying) return@LaunchedEffect
        delay(MUSIC_VOLUME_SYNC_DEBOUNCE_MS)
        withContext(Dispatchers.IO) {
            musicFilePlayer.setVolume(controlsState.lullabyLevel)
        }
    }

    LaunchedEffect(
        musicPlaying,
        musicPaused,
        musicPositionMillis,
        musicRepeatOne,
        selectedMusicTrack,
    ) {
        val track = selectedMusicTrack ?: return@LaunchedEffect
        val duration = track.durationMillis ?: return@LaunchedEffect
        if (
            !musicRepeatOne &&
            !musicPlaying &&
            !musicPaused &&
            !musicTasks.commandRunning &&
            musicPositionMillis >= duration - MUSIC_END_TOLERANCE_MS
        ) {
            val index = musicTracks.indexOfFirst { it.boardTrackId == track.boardTrackId }
            if (index >= 0 && musicTracks.isNotEmpty()) {
                selectedMusicTrack = musicTracks[(index + 1) % musicTracks.size]
                playSelectedMusic(0L)
            }
        }
    }

    val visibleAlerts = retainedAlerts
        .values
        .toList()
        .withoutAcknowledged(acknowledgedAlertIds)
        .sortedByDescending { it.timestampMillis }
    BoardAlertNotifications(
        alerts = activeBoardAlerts.withoutAcknowledged(acknowledgedAlertIds),
    )

    Scaffold(
        bottomBar = {
            if (!cameraState.fullscreen) {
                AppNavigation(
                    selectedTab = selectedTab,
                    onSelect = { selectedTab = it },
                )
            }
        },
        snackbarHost = { SnackbarHost(hostState = snackbarHostState) },
        containerColor = MaterialTheme.colorScheme.background,
    ) { paddingValues ->
        if (cameraState.fullscreen) {
            FullscreenCameraView(
                cameraFrame = cameraFrame,
                cameraLive = cameraState.live,
                onCameraLiveChange = { cameraState.live = it },
                onExitFullscreen = { toggleFullscreen() },
            )
        } else {
            val backgroundBrush = AppBackgroundBrush()
            Box(
                modifier = Modifier
                    .fillMaxSize()
                    .background(backgroundBrush)
                    .padding(paddingValues)
                    // Lets imePadding() inside a page add only the part of the keyboard below the nav bar.
                    .consumeWindowInsets(paddingValues),
            ) {
                when (selectedTab) {
                        AppTab.Overview -> OverviewPage(
                            context = view.context,
                            uiState = overviewUiState(
                                environmentReading = environmentReading,
                                alerts = visibleAlerts,
                            ),
                            cameraFrame = cameraFrame,
                            cameraLive = cameraState.live,
                            onCameraLiveChange = { cameraState.live = it },
                            listenEnabled = cameraState.listenEnabled,
                            onListenToggle = { toggleListen() },
                            voiceRecording = cameraState.voiceRecording,
                            onVoicePressStart = { startVoiceMessage() },
                            onVoicePressEnd = { finishVoiceMessage() },
                            onSnapshot = {
                                scope.launch {
                                    val imported = withContext(Dispatchers.IO) {
                                        boardApi.snapshotStream()?.let { stream ->
                                            albumStore.importMoment(
                                                capturedAtMillis = System.currentTimeMillis(),
                                                source = stream,
                                            )
                                        }
                                    }
                                    if (imported != null) {
                                        refreshAlbumMoments()
                                        showMessage("已保存到成长相册")
                                    } else {
                                        showMessage("抓拍保存失败，请检查相册文件夹和看护网络")
                                    }
                                }
                            },
                            onToggleFullscreen = { toggleFullscreen() },
                            onAcknowledge = { alertId ->
                                acknowledgedAlertIds = acknowledgedAlertIds + alertId
                                NotificationHelper.cancelBoardAlert(view.context, alertId)
                                showMessage("已处理警告")
                            },
                        )

                        AppTab.Care -> CarePage(
                            boardApi = boardApi,
                            showMessage = showMessage,
                        )

                        AppTab.Assistant -> AssistantPage(state = assistantState)

                        AppTab.Family -> FamilyPage(
                            moments = growthMoments,
                            imageLoader = albumImageLoader,
                            inviteCount = familySettingsState.inviteCount,
                            onInvite = {
                                familySettingsState.inviteCount += 1
                                showMessage("已生成第 ${familySettingsState.inviteCount} 个家人邀请链接")
                            },
                        )

                        AppTab.Settings -> SettingsPage(
                            themeMode = themeMode,
                            onThemeModeChange = onThemeModeChange,
                            faceAlert = alertSettingsState.faceAlert,
                            onFaceAlertChange = { alertSettingsState.faceAlert = it },
                            noPersonAlert = alertSettingsState.noPersonAlert,
                            onNoPersonAlertChange = { alertSettingsState.noPersonAlert = it },
                            cryAlert = alertSettingsState.cryAlert,
                            onCryAlertChange = { alertSettingsState.cryAlert = it },
                            boundaryAlert = alertSettingsState.boundaryAlert,
                            onBoundaryAlertChange = { alertSettingsState.boundaryAlert = it },
                            autoPlayOnCry = controlsState.autoPlayOnCry,
                            onAutoPlayOnCryChange = { controlsState.autoPlayOnCry = it },
                            cameraFrame = cameraFrame,
                            cameraLive = cameraState.live,
                            safetyZone = cameraState.safetyZone,
                            activityZoneMode = cameraState.activityZoneMode,
                            onSafetyZoneChange = {
                                val zone = it.normalized()
                                cameraState.safetyZone = zone
                                saveBoardActivityZone(zone, cameraState.activityZoneMode)
                            },
                            onActivityZoneModeChange = {
                                cameraState.activityZoneMode = it
                                cameraState.safetyZone?.let { zone ->
                                    saveBoardActivityZone(zone, it)
                                }
                            },
                            onResetSafetyZone = {
                                cameraState.safetyZone = null
                                clearBoardActivityZone()
                                showMessage("活动区域已重置")
                            },
                            albumFolderLabel = albumFolderLabel,
                            onAlbumFolderSelected = { uri ->
                                albumStore.setAlbumFolderUri(uri)
                                albumFolderLabel = albumStore.albumFolderLabel()
                                refreshAlbumMoments()
                                showMessage("成长相册存放文件夹已更新")
                            },
                            onAlbumSync = {
                                val result = albumSyncClient.syncBoardPhotos(albumStore)
                                refreshAlbumMoments()
                                if (result.importedCount > 0) {
                                    showMessage("已同步 ${result.importedCount} 张板端照片")
                                } else {
                                    showMessage("同步失败，请检查相册文件夹和看护网络")
                                }
                            },
                            boardConnection = boardConnection,
                        )
                    }
                }
            }
        }
    }
}

private const val OVERVIEW_REFRESH_INTERVAL_MS = 500L
private const val MUSIC_VOLUME_SYNC_DEBOUNCE_MS = 40L
private const val MUSIC_STATUS_REFRESH_MS = 250L
private const val MUSIC_END_TOLERANCE_MS = 500L
private const val CRY_CLEAR_CONFIRM_POLLS = 2

private class LullabyTaskState {
    var commandRunning: Boolean = false
    var syncJob: Job? = null
}

private class CryPlaybackTriggerState {
    var active: Boolean = false
    var clearPolls: Int = 0
}

private fun Map<BoardAlertType, BoardAlert>.withLatestAlerts(alerts: List<BoardAlert>): Map<BoardAlertType, BoardAlert> {
    if (alerts.isEmpty()) return this
    val next = toMutableMap()
    alerts.forEach { alert ->
        val previous = next[alert.type]
        if (previous == null || alert.timestampMillis >= previous.timestampMillis) {
            next[alert.type] = alert
        }
    }
    return next
}

@Composable
private fun BoardAlertNotifications(
    alerts: List<BoardAlert>,
) {
    val context = LocalView.current.context
    val latestAlerts by rememberUpdatedState(alerts)
    val lastPushAt = remember { mutableMapOf<BoardAlertType, Long>() }
    val activeTypes = alerts.map { it.type }.distinct().sortedBy { it.name }

    LaunchedEffect(activeTypes) {
        while (latestAlerts.isNotEmpty()) {
            val now = SystemClock.elapsedRealtime()
            latestAlerts.distinctBy { it.type }.forEach { alert ->
                val previous = lastPushAt[alert.type]
                if (previous == null || now - previous >= ALERT_PUSH_COOLDOWN_MS) {
                    NotificationHelper.showBoardAlert(context, alert)
                    lastPushAt[alert.type] = now
                }
            }
            val nextPushIn = latestAlerts
                .distinctBy { it.type }
                .minOf { alert ->
                    val elapsed = now - (lastPushAt[alert.type] ?: 0L)
                    (ALERT_PUSH_COOLDOWN_MS - elapsed).coerceAtLeast(1L)
                }
            delay(nextPushIn)
        }
    }
}

private const val ALERT_PUSH_COOLDOWN_MS = 3_000L

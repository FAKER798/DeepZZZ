package com.naviga.app

import android.content.Context
import android.net.Uri
import androidx.activity.compose.BackHandler
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.animation.Crossfade
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
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.background
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.rounded.KeyboardArrowRight
import androidx.compose.material.icons.automirrored.rounded.VolumeUp
import androidx.compose.material.icons.rounded.CropFree
import androidx.compose.material.icons.rounded.Close
import androidx.compose.material.icons.rounded.Delete
import androidx.compose.material.icons.rounded.Done
import androidx.compose.material.icons.rounded.Edit
import androidx.compose.material.icons.rounded.Folder
import androidx.compose.material.icons.rounded.Notifications
import androidx.compose.material.icons.rounded.MusicNote
import androidx.compose.material.icons.rounded.PersonOff
import androidx.compose.material.icons.rounded.Sync
import androidx.compose.material.icons.rounded.Security
import androidx.compose.material.icons.rounded.Settings
import androidx.compose.material3.Button
import androidx.compose.material3.FilledTonalButton
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.RadioButton
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.ImageBitmap
import androidx.compose.ui.platform.LocalView
import androidx.compose.ui.unit.dp

@Composable
fun SettingsPage(
    themeMode: ThemeMode,
    onThemeModeChange: (ThemeMode) -> Unit,
    faceAlert: Boolean,
    onFaceAlertChange: (Boolean) -> Unit,
    noPersonAlert: Boolean,
    onNoPersonAlertChange: (Boolean) -> Unit,
    cryAlert: Boolean,
    onCryAlertChange: (Boolean) -> Unit,
    boundaryAlert: Boolean,
    onBoundaryAlertChange: (Boolean) -> Unit,
    autoPlayOnCry: Boolean,
    onAutoPlayOnCryChange: (Boolean) -> Unit,
    cameraFrame: ImageBitmap?,
    cameraLive: Boolean,
    safetyZone: SafetyZone?,
    activityZoneMode: ActivityZoneMode,
    onSafetyZoneChange: (SafetyZone) -> Unit,
    onActivityZoneModeChange: (ActivityZoneMode) -> Unit,
    onResetSafetyZone: () -> Unit,
    albumFolderLabel: String,
    onAlbumFolderSelected: (Uri) -> Unit,
    onAlbumSync: () -> Unit,
    boardConnection: BoardConnectionSession,
) {
    var selectedCategory by rememberSaveable { mutableStateOf<SettingsCategory?>(null) }

    Crossfade(
        targetState = selectedCategory,
        label = "settingsCrossfade",
    ) { category ->
        if (category != null) {
            BackHandler { selectedCategory = null }
        }
        when (category) {
            null -> SettingsListPage(
                onSelect = { selectedCategory = it },
            )
            SettingsCategory.Theme -> ThemeSettingsPage(
                themeMode = themeMode,
                onThemeModeChange = onThemeModeChange,
                onBack = { selectedCategory = null },
            )
            SettingsCategory.Notif -> NotifSettingsPage(
                context = LocalView.current.context,
                onBack = { selectedCategory = null },
            )
            SettingsCategory.Connection -> ConnectionSettingsPage(
                onBack = { selectedCategory = null },
                boardConnection = boardConnection,
            )
            SettingsCategory.Alerts -> AlertsSettingsPage(
                faceAlert = faceAlert,
                onFaceAlertChange = onFaceAlertChange,
                noPersonAlert = noPersonAlert,
                onNoPersonAlertChange = onNoPersonAlertChange,
                cryAlert = cryAlert,
                onCryAlertChange = onCryAlertChange,
                boundaryAlert = boundaryAlert,
                onBoundaryAlertChange = onBoundaryAlertChange,
                onBack = { selectedCategory = null },
            )
            SettingsCategory.CryCare -> CryCareSettingsPage(
                autoPlayOnCry = autoPlayOnCry,
                onAutoPlayOnCryChange = onAutoPlayOnCryChange,
                onBack = { selectedCategory = null },
            )
            SettingsCategory.SafetyZone -> SafetyZoneSettingsPage(
                cameraFrame = cameraFrame,
                cameraLive = cameraLive,
                safetyZone = safetyZone,
                activityZoneMode = activityZoneMode,
                onSafetyZoneChange = onSafetyZoneChange,
                onActivityZoneModeChange = onActivityZoneModeChange,
                onResetSafetyZone = onResetSafetyZone,
                onBack = { selectedCategory = null },
            )
            SettingsCategory.Album -> AlbumSettingsPage(
                folderLabel = albumFolderLabel,
                onFolderSelected = onAlbumFolderSelected,
                onSync = onAlbumSync,
                onBack = { selectedCategory = null },
            )
        }
    }
}

@Composable
fun CryCareSettingsPage(
    autoPlayOnCry: Boolean,
    onAutoPlayOnCryChange: (Boolean) -> Unit,
    onBack: () -> Unit,
) {
    SettingsDetailScaffold(
        title = "哭闹处理",
        onBack = onBack,
    ) {
        item {
            ControlPanel(
                controls = listOf(
                    ToggleSpec(
                        title = "自动播放摇篮曲",
                        icon = Icons.Rounded.MusicNote,
                        checked = autoPlayOnCry,
                        onCheckedChange = onAutoPlayOnCryChange,
                    ),
                ),
            )
        }
    }
}

@Composable
fun SettingsListPage(
    onSelect: (SettingsCategory) -> Unit,
) {
    PageColumn {
        item {
            PageHeader(title = "设置")
        }
        item {
            Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
                SettingsCategory.entries.forEach { category ->
                    PressedCard(
                        onClick = { onSelect(category) },
                        contentPadding = PaddingValues(14.dp),
                    ) {
                        Row(
                            verticalAlignment = Alignment.CenterVertically,
                            horizontalArrangement = Arrangement.spacedBy(12.dp),
                        ) {
                            IconBadge(icon = category.icon)
                            Column(modifier = Modifier.weight(1f)) {
                                Text(text = category.label, style = MaterialTheme.typography.titleMedium)
                            }
                            Icon(
                                imageVector = Icons.AutoMirrored.Rounded.KeyboardArrowRight,
                                contentDescription = null,
                                tint = MaterialTheme.colorScheme.onSurfaceVariant,
                                modifier = Modifier.size(20.dp),
                            )
                        }
                    }
                }
            }
        }
    }
}

@Composable
fun ThemeSettingsPage(
    themeMode: ThemeMode,
    onThemeModeChange: (ThemeMode) -> Unit,
    onBack: () -> Unit,
) {
    SettingsDetailScaffold(
        title = "颜色主题",
        onBack = onBack,
    ) {
        item {
            Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
                ThemeMode.entries.forEach { mode ->
                    PressedCard(
                        onClick = { onThemeModeChange(mode) },
                        contentPadding = PaddingValues(14.dp),
                    ) {
                        Row(
                            verticalAlignment = Alignment.CenterVertically,
                            horizontalArrangement = Arrangement.spacedBy(12.dp),
                        ) {
                            IconBadge(icon = mode.icon)
                            Column(modifier = Modifier.weight(1f)) {
                                Text(
                                    text = mode.label,
                                    style = MaterialTheme.typography.titleMedium,
                                )
                            }
                            RadioButton(
                                selected = themeMode == mode,
                                onClick = { onThemeModeChange(mode) },
                            )
                        }
                    }
                }
            }
        }
    }
}

@Composable
fun AlertsSettingsPage(
    faceAlert: Boolean,
    onFaceAlertChange: (Boolean) -> Unit,
    noPersonAlert: Boolean,
    onNoPersonAlertChange: (Boolean) -> Unit,
    cryAlert: Boolean,
    onCryAlertChange: (Boolean) -> Unit,
    boundaryAlert: Boolean,
    onBoundaryAlertChange: (Boolean) -> Unit,
    onBack: () -> Unit,
) {
    val alertControls = remember(
        faceAlert,
        noPersonAlert,
        cryAlert,
        boundaryAlert,
        onFaceAlertChange,
        onNoPersonAlertChange,
        onCryAlertChange,
        onBoundaryAlertChange,
    ) {
        listOf(
            ToggleSpec("遮脸/翻身检测", Icons.Rounded.Security, faceAlert, onFaceAlertChange),
            ToggleSpec("无人检测", Icons.Rounded.PersonOff, noPersonAlert, onNoPersonAlertChange),
            ToggleSpec("哭声检测", Icons.AutoMirrored.Rounded.VolumeUp, cryAlert, onCryAlertChange),
            ToggleSpec("边界检测", Icons.Rounded.CropFree, boundaryAlert, onBoundaryAlertChange),
        )
    }

    SettingsDetailScaffold(
        title = "警告设置",
        onBack = onBack,
    ) {
        item {
            ControlPanel(controls = alertControls)
        }
    }
}

@Composable
fun SafetyZoneSettingsPage(
    cameraFrame: ImageBitmap?,
    cameraLive: Boolean,
    safetyZone: SafetyZone?,
    activityZoneMode: ActivityZoneMode,
    onSafetyZoneChange: (SafetyZone) -> Unit,
    onActivityZoneModeChange: (ActivityZoneMode) -> Unit,
    onResetSafetyZone: () -> Unit,
    onBack: () -> Unit,
) {
    var drawing by rememberSaveable { mutableStateOf(false) }
    var draftPoints by remember { mutableStateOf<List<ZonePoint>>(emptyList()) }
    val zoneColor = when (activityZoneMode) {
        ActivityZoneMode.Safe -> MaterialTheme.colorScheme.primary
        ActivityZoneMode.Danger -> MaterialTheme.colorScheme.error
    }
    SettingsDetailScaffold(
        title = "活动区域",
        onBack = onBack,
    ) {
        item {
            PressedCard(contentPadding = PaddingValues(14.dp)) {
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(12.dp),
                ) {
                    IconBadge(icon = Icons.Rounded.CropFree)
                    Column(modifier = Modifier.weight(1f)) {
                        Text("框选${activityZoneMode.label}", style = MaterialTheme.typography.titleMedium)
                    }
                }
                Spacer(Modifier.height(12.dp))
                Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    ActivityZoneMode.entries.forEach { mode ->
                        PressedCard(
                            onClick = { onActivityZoneModeChange(mode) },
                            contentPadding = PaddingValues(horizontal = 12.dp, vertical = 10.dp),
                        ) {
                            Row(
                                modifier = Modifier.fillMaxWidth(),
                                verticalAlignment = Alignment.CenterVertically,
                                horizontalArrangement = Arrangement.spacedBy(10.dp),
                            ) {
                                Box(
                                    modifier = Modifier
                                        .size(14.dp)
                                        .clip(RoundedCornerShape(4.dp))
                                        .background(
                                            if (mode == ActivityZoneMode.Safe) {
                                                MaterialTheme.colorScheme.primary
                                            } else {
                                                MaterialTheme.colorScheme.error
                                            },
                                        ),
                                )
                                Text(
                                    text = mode.label,
                                    style = MaterialTheme.typography.bodyLarge,
                                    modifier = Modifier.weight(1f),
                                )
                                RadioButton(
                                    selected = activityZoneMode == mode,
                                    onClick = { onActivityZoneModeChange(mode) },
                                )
                            }
                        }
                    }
                }
                Spacer(Modifier.height(12.dp))
                Box(
                    modifier = Modifier
                        .fillMaxWidth()
                        .aspectRatio(16f / 9f)
                        .clip(RoundedCornerShape(18.dp))
                        .background(Color.Black),
                ) {
                    CameraStreamView(
                        frame = cameraFrame,
                        cameraLive = cameraLive,
                        modifier = Modifier.fillMaxSize(),
                    )
                    SafetyZoneOverlay(
                        safetyZone = safetyZone,
                        editing = true,
                        zoneColor = zoneColor,
                        onSafetyZoneChange = { onSafetyZoneChange(it.normalized()) },
                        modifier = Modifier.fillMaxSize(),
                        drawing = drawing,
                        draftPoints = draftPoints,
                        onDraftPointsChange = { draftPoints = it },
                    )
                }
                Spacer(Modifier.height(10.dp))
                if (drawing) {
                    Row(
                        modifier = Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.spacedBy(10.dp),
                    ) {
                        Button(
                            onClick = {
                                val candidate = SafetyZone(draftPoints).normalized()
                                if (candidate.isValid()) {
                                    onSafetyZoneChange(candidate)
                                    drawing = false
                                }
                            },
                            enabled = draftPoints.size >= 3,
                            modifier = Modifier.weight(1f),
                            shape = ControlShape,
                            contentPadding = PaddingValues(14.dp),
                        ) {
                            Icon(Icons.Rounded.Done, contentDescription = null)
                            Spacer(Modifier.width(8.dp))
                            Text("完成")
                        }
                        Button(
                            onClick = {
                                draftPoints = emptyList()
                                drawing = false
                            },
                            modifier = Modifier.weight(1f),
                            shape = ControlShape,
                            contentPadding = PaddingValues(14.dp),
                        ) {
                            Icon(Icons.Rounded.Close, contentDescription = null)
                            Spacer(Modifier.width(8.dp))
                            Text("取消")
                        }
                    }
                } else {
                    Row(
                        modifier = Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.spacedBy(10.dp),
                    ) {
                        Button(
                            onClick = {
                                draftPoints = emptyList()
                                drawing = true
                            },
                            modifier = Modifier.weight(1f),
                            shape = ControlShape,
                            contentPadding = PaddingValues(14.dp),
                        ) {
                            Icon(Icons.Rounded.Edit, contentDescription = null)
                            Spacer(Modifier.width(8.dp))
                            Text("开始绘制")
                        }
                        Button(
                            onClick = onResetSafetyZone,
                            modifier = Modifier.weight(1f),
                            shape = ControlShape,
                            contentPadding = PaddingValues(14.dp),
                        ) {
                            Icon(Icons.Rounded.Delete, contentDescription = null)
                            Spacer(Modifier.width(8.dp))
                            Text("清除")
                        }
                    }
                }
            }
        }
    }
}

@Composable
fun AlbumSettingsPage(
    folderLabel: String,
    onFolderSelected: (Uri) -> Unit,
    onSync: () -> Unit,
    onBack: () -> Unit,
) {
    val context = LocalView.current.context
    val launcher = rememberLauncherForActivityResult(
        contract = ActivityResultContracts.OpenDocumentTree(),
    ) { uri ->
        if (uri != null) {
            val flags = android.content.Intent.FLAG_GRANT_READ_URI_PERMISSION or
                android.content.Intent.FLAG_GRANT_WRITE_URI_PERMISSION
            context.contentResolver.takePersistableUriPermission(uri, flags)
            onFolderSelected(uri)
        }
    }

    SettingsDetailScaffold(
        title = "成长相册",
        onBack = onBack,
    ) {
        item {
            PressedCard(contentPadding = PaddingValues(14.dp)) {
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(12.dp),
                ) {
                    IconBadge(icon = Icons.Rounded.Folder)
                    Column(modifier = Modifier.weight(1f)) {
                        Text("存放文件夹", style = MaterialTheme.typography.titleMedium)
                        Text(
                            text = folderLabel,
                            style = MaterialTheme.typography.bodyMedium,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                    }
                }
                Spacer(Modifier.height(12.dp))
                Button(
                    onClick = { launcher.launch(null) },
                    modifier = Modifier.fillMaxWidth(),
                    shape = ControlShape,
                    contentPadding = PaddingValues(14.dp),
                ) {
                    Icon(Icons.Rounded.Folder, contentDescription = null)
                    Spacer(Modifier.width(8.dp))
                    Text("选择文件夹")
                }
                Spacer(Modifier.height(10.dp))
                Button(
                    onClick = onSync,
                    modifier = Modifier.fillMaxWidth(),
                    shape = ControlShape,
                    contentPadding = PaddingValues(14.dp),
                ) {
                    Icon(Icons.Rounded.Sync, contentDescription = null)
                    Spacer(Modifier.width(8.dp))
                    Text("同步板端照片")
                }
            }
        }
    }
}

@Composable
fun NotifSettingsPage(
    context: Context,
    onBack: () -> Unit,
) {
    SettingsDetailScaffold(
        title = "通知管理",
        onBack = onBack,
    ) {
        item {
            PressedCard(contentPadding = PaddingValues(14.dp)) {
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(12.dp),
                ) {
                    IconBadge(icon = Icons.Rounded.Notifications)
                    Column(modifier = Modifier.weight(1f)) {
                        Text("系统通知设置", style = MaterialTheme.typography.titleMedium)
                    }
                }
                Spacer(Modifier.height(12.dp))
                Button(
                    onClick = { NotificationHelper.openNotificationSettings(context) },
                    modifier = Modifier.fillMaxWidth(),
                    shape = ControlShape,
                    contentPadding = PaddingValues(14.dp),
                ) {
                    Icon(Icons.Rounded.Settings, contentDescription = null)
                    Spacer(Modifier.width(8.dp))
                    Text("打开通知设置")
                }
                Spacer(Modifier.height(10.dp))
                Button(
                    onClick = { NotificationHelper.openBatteryOptimizationSettings(context) },
                    modifier = Modifier.fillMaxWidth(),
                    shape = ControlShape,
                    contentPadding = PaddingValues(14.dp),
                ) {
                    Icon(Icons.Rounded.Settings, contentDescription = null)
                    Spacer(Modifier.width(8.dp))
                    Text("后台运行设置")
                }
            }
        }
    }
}

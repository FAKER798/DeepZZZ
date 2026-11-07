package com.naviga.app

import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.rounded.Cable
import androidx.compose.material.icons.rounded.CropFree
import androidx.compose.material.icons.rounded.DarkMode
import androidx.compose.material.icons.rounded.Image
import androidx.compose.material.icons.rounded.LightMode
import androidx.compose.material.icons.rounded.Notifications
import androidx.compose.material.icons.rounded.MusicNote
import androidx.compose.material.icons.rounded.Settings
import androidx.compose.ui.graphics.vector.ImageVector

enum class SettingsCategory(
    val label: String,
    val icon: ImageVector,
) {
    Theme("颜色主题", Icons.Rounded.LightMode),
    Connection("设备连接", Icons.Rounded.Cable),
    Alerts("警告设置", Icons.Rounded.Notifications),
    CryCare("哭闹处理", Icons.Rounded.MusicNote),
    SafetyZone("活动区域", Icons.Rounded.CropFree),
    Album("成长相册", Icons.Rounded.Image),
    Notif("通知管理", Icons.Rounded.Notifications),
}

val ThemeMode.icon: ImageVector
    get() = when (this) {
        ThemeMode.Light -> Icons.Rounded.LightMode
        ThemeMode.Dark -> Icons.Rounded.DarkMode
        ThemeMode.System -> Icons.Rounded.Settings
    }

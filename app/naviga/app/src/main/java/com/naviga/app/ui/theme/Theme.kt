package com.naviga.app.ui.theme

import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color

private val DarkColorScheme = darkColorScheme(
    primary = Color(0xFF8BC8AD),
    onPrimary = Color(0xFF0D211B),
    primaryContainer = Color(0xFF214E42),
    onPrimaryContainer = Color(0xFFD9F3E6),
    secondary = Color(0xFFE39A75),
    onSecondary = Color(0xFF34170C),
    secondaryContainer = Color(0xFF663721),
    onSecondaryContainer = Color(0xFFFFD9C7),
    tertiary = Color(0xFFEFC66D),
    onTertiary = Color(0xFF332407),
    tertiaryContainer = Color(0xFF584012),
    onTertiaryContainer = Color(0xFFF9E7B7),
    background = NightPaper,
    onBackground = NightInk,
    surface = NightSurface,
    onSurface = NightInk,
    surfaceVariant = NightSurfaceHigh,
    onSurfaceVariant = NightMuted,
    outline = Color(0xFF667467),
    error = Color(0xFFFF9A91),
    onError = Color(0xFF3C0806),
)

private val LightColorScheme = lightColorScheme(
    primary = ForestSeed,
    onPrimary = Color.White,
    primaryContainer = ForestMist,
    onPrimaryContainer = ForestDeep,
    secondary = Terracotta,
    onSecondary = Color.White,
    secondaryContainer = TerracottaMist,
    onSecondaryContainer = Color(0xFF4A1E0F),
    tertiary = AmberHush,
    onTertiary = Color(0xFF2F2308),
    tertiaryContainer = AmberMist,
    onTertiaryContainer = Color(0xFF4A3307),
    background = Paper,
    onBackground = Ink,
    surface = Color(0xFFFFFDF7),
    onSurface = Ink,
    surfaceVariant = Linen,
    onSurfaceVariant = SageText,
    outline = Color(0xFFB8B0A2),
    error = Color(0xFFB9473F),
    onError = Color.White,
)

@Composable
fun NavigaTheme(
    darkTheme: Boolean,
    content: @Composable () -> Unit,
) {
    MaterialTheme(
        colorScheme = if (darkTheme) DarkColorScheme else LightColorScheme,
        typography = Typography,
        content = content,
    )
}

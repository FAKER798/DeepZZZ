package com.naviga.app

import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.rounded.VolumeUp
import androidx.compose.material.icons.rounded.Thermostat
import androidx.compose.material.icons.rounded.WaterDrop
import androidx.compose.ui.graphics.vector.ImageVector

data class AlertEvent(
    val id: String,
    val title: String,
    val timestamp: String,
    val icon: ImageVector,
    val thumbnailJpeg: ByteArray?,
)

data class OverviewUiState(
    val statusMetrics: MetricsData,
    val metrics: List<Metric>,
    val events: List<AlertEvent>,
)

fun overviewUiState(
    environmentReading: EnvironmentReading,
    alerts: List<BoardAlert>,
) = OverviewUiState(
    statusMetrics = MetricsData(
        sound = environmentReading.soundDecibels.formatOneDecimal(),
        temperature = environmentReading.temperatureCelsius.formatOneDecimal(),
        humidity = environmentReading.humidityPercent.formatOneDecimal(),
    ),
    metrics = listOf(
        Metric(
            label = "声音",
            value = environmentReading.soundDecibels.formatOneDecimal(),
            unit = "dB",
            icon = Icons.AutoMirrored.Rounded.VolumeUp,
        ),
        Metric(
            label = "温度",
            value = environmentReading.temperatureCelsius.formatOneDecimal(),
            unit = "℃",
            icon = Icons.Rounded.Thermostat,
        ),
        Metric(
            label = "湿度",
            value = environmentReading.humidityPercent.formatOneDecimal(),
            unit = "%",
            icon = Icons.Rounded.WaterDrop,
        ),
    ),
    events = alerts.map { it.toAlertEvent() },
)

fun sampleOverviewUiState() = overviewUiState(
    environmentReading = StubEnvironmentDataSource().currentReading(),
    alerts = StubBoardAlertDataSource().activeAlerts(sampleAlertSettings()),
)

private fun BoardAlert.toAlertEvent(): AlertEvent {
    return AlertEvent(
        id = id,
        title = type.title,
        timestamp = timestampLabel(),
        icon = type.icon,
        thumbnailJpeg = thumbnailJpeg,
    )
}

private fun Float.formatOneDecimal(): String {
    return "%.1f".format(this)
}

private fun sampleAlertSettings(): AlertSettingsSnapshot {
    return AlertSettingsSnapshot(
        faceAlert = true,
        noPersonAlert = true,
        cryAlert = true,
        boundaryAlert = true,
    )
}

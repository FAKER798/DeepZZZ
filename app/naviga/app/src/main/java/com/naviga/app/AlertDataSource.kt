package com.naviga.app

import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.rounded.VolumeUp
import androidx.compose.material.icons.rounded.CropFree
import androidx.compose.material.icons.rounded.PersonOff
import androidx.compose.material.icons.rounded.Security
import androidx.compose.ui.graphics.vector.ImageVector
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

enum class BoardAlertType(
    val title: String,
    val icon: ImageVector,
) {
    FaceCovered("遮脸/翻身警告", Icons.Rounded.Security),
    NoPerson("无人检测警告", Icons.Rounded.PersonOff),
    Crying("哭声警告", Icons.AutoMirrored.Rounded.VolumeUp),
    Boundary("边界越界警告", Icons.Rounded.CropFree),
}

data class BoardAlert(
    val id: String,
    val type: BoardAlertType,
    val timestampMillis: Long,
    val thumbnailJpeg: ByteArray? = null,
)

data class AlertSettingsSnapshot(
    val faceAlert: Boolean,
    val noPersonAlert: Boolean,
    val cryAlert: Boolean,
    val boundaryAlert: Boolean,
    val safetyZone: SafetyZone? = null,
    val activityZoneMode: ActivityZoneMode = ActivityZoneMode.Safe,
)

interface BoardAlertDataSource {
    fun activeAlerts(settings: AlertSettingsSnapshot): List<BoardAlert>
}

class StubBoardAlertDataSource : BoardAlertDataSource {
    private val baseTimestampMillis = System.currentTimeMillis()

    override fun activeAlerts(settings: AlertSettingsSnapshot): List<BoardAlert> {
        return buildList {
            if (settings.faceAlert) {
                add(
                    BoardAlert(
                        id = "stub-face-$ALERT_SEED",
                        type = BoardAlertType.FaceCovered,
                        timestampMillis = baseTimestampMillis - 2 * 60_000L,
                    ),
                )
            }
            if (settings.noPersonAlert) {
                add(
                    BoardAlert(
                        id = "stub-no-person-$ALERT_SEED",
                        type = BoardAlertType.NoPerson,
                        timestampMillis = baseTimestampMillis - 3 * 60_000L,
                    ),
                )
            }
            if (settings.cryAlert) {
                add(
                    BoardAlert(
                        id = "stub-cry-$ALERT_SEED",
                        type = BoardAlertType.Crying,
                        timestampMillis = baseTimestampMillis - 5 * 60_000L,
                    ),
                )
            }
            if (settings.boundaryAlert) {
                add(
                    BoardAlert(
                        id = "stub-boundary-$ALERT_SEED",
                        type = BoardAlertType.Boundary,
                        timestampMillis = baseTimestampMillis - 6 * 60_000L,
                    ),
                )
            }
        }.sortedByDescending { it.timestampMillis }
    }

    private companion object {
        const val ALERT_SEED = "board"
    }
}

fun BoardAlert.timestampLabel(): String {
    return AlertDateFormat.get().format(Date(timestampMillis))
}

fun List<BoardAlert>.withoutAcknowledged(acknowledgedIds: Collection<String>): List<BoardAlert> {
    return filterNot { it.id in acknowledgedIds }
}

private object AlertDateFormat {
    private val formatter = ThreadLocal.withInitial {
        SimpleDateFormat("HH:mm:ss", Locale.CHINA)
    }

    fun get(): SimpleDateFormat = requireNotNull(formatter.get())
}

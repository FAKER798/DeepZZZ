package com.naviga.app

import android.Manifest
import android.app.Activity
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.media.AudioAttributes
import android.media.RingtoneManager
import android.net.Uri
import android.os.Build
import android.os.PowerManager
import android.provider.Settings
import androidx.core.app.ActivityCompat
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import androidx.core.content.ContextCompat

data class MetricsData(
    val sound: String,
    val temperature: String,
    val humidity: String,
)

private data class AlertNotificationCopy(
    val title: String,
    val message: String,
)

object NotificationHelper {

    const val CHANNEL_STATUS = "channel_status"
    const val CHANNEL_ALERT = "channel_alert_popup_v2"

    private const val GROUP_STATUS = "group_status"
    private const val GROUP_ALERT = "group_alert"

    const val ID_STATUS = 1001
    private const val ID_ALERT = 1002
    private const val ID_GROUP_STATUS = 2001
    private const val ID_GROUP_ALERT = 2002
    fun initChannels(context: Context) {
        val nm = context.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager

        val statusChannel = NotificationChannel(
            CHANNEL_STATUS,
            "设备状态",
            NotificationManager.IMPORTANCE_LOW,
        ).apply {
            setShowBadge(false)
            enableVibration(false)
            setSound(null, null)
        }

        val alertChannel = NotificationChannel(
            CHANNEL_ALERT,
            "安全警告弹窗",
            NotificationManager.IMPORTANCE_HIGH,
        ).apply {
            enableVibration(true)
            enableLights(true)
            vibrationPattern = longArrayOf(0, 300, 120, 300)
            lockscreenVisibility = NotificationCompat.VISIBILITY_PUBLIC
            setSound(
                RingtoneManager.getDefaultUri(RingtoneManager.TYPE_NOTIFICATION),
                AudioAttributes.Builder()
                    .setUsage(AudioAttributes.USAGE_ALARM)
                    .setContentType(AudioAttributes.CONTENT_TYPE_SONIFICATION)
                    .build(),
            )
        }

        nm.createNotificationChannels(listOf(statusChannel, alertChannel))
    }

    fun showStatusNotification(context: Context, metrics: MetricsData) {
        if (!canNotify(context)) return

        val nm = NotificationManagerCompat.from(context)
        nm.notify(ID_STATUS, statusNotification(context, metrics))
        nm.notify(ID_GROUP_STATUS, statusSummaryNotification(context))
    }

    fun statusNotification(context: Context, metrics: MetricsData): android.app.Notification {
        val summary = "声音${metrics.sound}dB · 温度${metrics.temperature}℃ · 湿度${metrics.humidity}%"
        val expanded = buildString {
            appendLine("声音  ${metrics.sound} dB")
            appendLine("温度  ${metrics.temperature} ℃")
            append("湿度  ${metrics.humidity} %")
        }

        return NotificationCompat.Builder(context, CHANNEL_STATUS)
            .setSmallIcon(R.drawable.ic_launcher_foreground)
            .setContentTitle("眠屿看护")
            .setContentText(summary)
            .setStyle(
                NotificationCompat.BigTextStyle()
                    .bigText(expanded)
                    .setSummaryText("实时看护中"),
            )
            .setOngoing(true)
            .setSilent(true)
            .setShowWhen(false)
            .setPriority(NotificationCompat.PRIORITY_LOW)
            .setCategory(NotificationCompat.CATEGORY_STATUS)
            .setVisibility(NotificationCompat.VISIBILITY_PUBLIC)
            .setGroup(GROUP_STATUS)
            .build()
    }

    fun defaultStatusNotification(context: Context): android.app.Notification {
        return statusNotification(
            context,
            MetricsData(sound = "--", temperature = "--", humidity = "--"),
        )
    }

    private fun statusSummaryNotification(context: Context): android.app.Notification {
        return NotificationCompat.Builder(context, CHANNEL_STATUS)
            .setSmallIcon(R.drawable.ic_launcher_foreground)
            .setStyle(
                NotificationCompat.InboxStyle()
                    .setSummaryText("设备状态"),
            )
            .setOngoing(true)
            .setSilent(true)
            .setShowWhen(false)
            .setPriority(NotificationCompat.PRIORITY_LOW)
            .setGroup(GROUP_STATUS)
            .setGroupSummary(true)
            .build()
    }

    fun showBoardAlert(context: Context, alert: BoardAlert) {
        if (!canNotify(context)) return

        val contentIntent = alertIntent(context)
        val copy = alert.type.notificationCopy()
        val notification = NotificationCompat.Builder(context, CHANNEL_ALERT)
            .setSmallIcon(R.drawable.ic_launcher_foreground)
            .setContentTitle(copy.title)
            .setContentText(copy.message)
            .setStyle(
                NotificationCompat.BigTextStyle()
                    .bigText("${copy.message}\n提醒时间 ${alert.timestampLabel()}")
                    .setSummaryText("宝宝看护提醒"),
            )
            .setContentIntent(contentIntent)
            .setFullScreenIntent(contentIntent, true)
            .setPriority(NotificationCompat.PRIORITY_MAX)
            .setAutoCancel(true)
            .setCategory(NotificationCompat.CATEGORY_ALARM)
            .setVisibility(NotificationCompat.VISIBILITY_PUBLIC)
            .setDefaults(NotificationCompat.DEFAULT_ALL)
            .setVibrate(longArrayOf(0, 300, 120, 300))
            .setOnlyAlertOnce(false)
            .build()

        val nm = NotificationManagerCompat.from(context)
        nm.notify(alert.notificationId, notification)
    }

    private fun BoardAlertType.notificationCopy(): AlertNotificationCopy {
        return when (this) {
            BoardAlertType.Crying -> AlertNotificationCopy(
                title = "宝宝在哭闹",
                message = "听到宝宝的哭声了，快去陪陪宝宝吧",
            )
            BoardAlertType.FaceCovered -> AlertNotificationCopy(
                title = "看不见宝宝脸蛋",
                message = "宝宝可能翻身或遮住了脸，快去看看吧",
            )
            BoardAlertType.NoPerson -> AlertNotificationCopy(
                title = "画面里找不到宝宝",
                message = "镜头暂时没有看到宝宝，请确认宝宝在哪里",
            )
            BoardAlertType.Boundary -> AlertNotificationCopy(
                title = "宝宝靠近活动区域",
                message = "宝宝触发了活动区域提醒，请及时留意",
            )
        }
    }

    fun cancelBoardAlert(context: Context, alertId: String) {
        NotificationManagerCompat.from(context).cancel(alertId.boardAlertNotificationId)
    }

    fun requestPermission(activity: Activity) {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            if (ContextCompat.checkSelfPermission(activity, Manifest.permission.POST_NOTIFICATIONS)
                != PackageManager.PERMISSION_GRANTED
            ) {
                ActivityCompat.requestPermissions(
                    activity,
                    arrayOf(Manifest.permission.POST_NOTIFICATIONS),
                    100,
                )
            }
        }
    }

    fun openNotificationSettings(context: Context) {
        val intent = Intent().apply {
            action = Settings.ACTION_APP_NOTIFICATION_SETTINGS
            putExtra(Settings.EXTRA_APP_PACKAGE, context.packageName)
            flags = Intent.FLAG_ACTIVITY_NEW_TASK
        }
        context.startActivity(intent)
    }

    fun openAlertNotificationSettings(context: Context) {
        val intent = Intent().apply {
            action = Settings.ACTION_CHANNEL_NOTIFICATION_SETTINGS
            putExtra(Settings.EXTRA_APP_PACKAGE, context.packageName)
            putExtra(Settings.EXTRA_CHANNEL_ID, CHANNEL_ALERT)
            flags = Intent.FLAG_ACTIVITY_NEW_TASK
        }
        context.startActivity(intent)
    }

    fun openBatteryOptimizationSettings(context: Context) {
        val powerManager = context.getSystemService(Context.POWER_SERVICE) as PowerManager
        val intent = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M &&
            !powerManager.isIgnoringBatteryOptimizations(context.packageName)
        ) {
            Intent(Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS).apply {
                data = Uri.parse("package:${context.packageName}")
                flags = Intent.FLAG_ACTIVITY_NEW_TASK
            }
        } else {
            Intent(Settings.ACTION_IGNORE_BATTERY_OPTIMIZATION_SETTINGS).apply {
                flags = Intent.FLAG_ACTIVITY_NEW_TASK
            }
        }
        context.startActivity(intent)
    }

    fun canNotify(context: Context): Boolean {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            return ContextCompat.checkSelfPermission(context, Manifest.permission.POST_NOTIFICATIONS) ==
                PackageManager.PERMISSION_GRANTED
        }
        return true
    }

    private val BoardAlert.notificationId: Int
        get() = id.boardAlertNotificationId

    private val String.boardAlertNotificationId: Int
        get() = ID_ALERT + hashCode().mod(10_000)

    private fun alertIntent(context: Context): PendingIntent {
        val intent = Intent(context, MainActivity::class.java).apply {
            flags = Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_SINGLE_TOP
        }
        return PendingIntent.getActivity(
            context,
            3001,
            intent,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )
    }
}

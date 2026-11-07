package com.naviga.app

import android.app.Service
import android.content.Intent
import android.os.IBinder

class CareForegroundService : Service() {
    override fun onCreate() {
        super.onCreate()
        NotificationHelper.initChannels(this)
        startForeground(
            NotificationHelper.ID_STATUS,
            NotificationHelper.defaultStatusNotification(this),
        )
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        return START_STICKY
    }

    override fun onBind(intent: Intent?): IBinder? = null
}

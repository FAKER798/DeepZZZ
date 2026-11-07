package com.naviga.app

import android.app.Activity
import android.os.Build
import android.os.PerformanceHintManager
import android.view.Choreographer
import android.view.Display
import androidx.lifecycle.DefaultLifecycleObserver
import androidx.lifecycle.LifecycleOwner
import kotlin.math.roundToLong

class PerformanceModeController(
    private val activity: Activity,
) : DefaultLifecycleObserver {

    private val choreographer = Choreographer.getInstance()
    private var performanceSession: PerformanceHintManager.Session? = null
    private var frameCallback: Choreographer.FrameCallback? = null
    private var lastFrameNanos = 0L

    override fun onCreate(owner: LifecycleOwner) {
        val display = currentDisplay()
        val maxRefreshRate = findMaxRefreshRate(display)
        val targetFrameNanos = refreshRateToFrameNanos(maxRefreshRate)

        activity.window.attributes = activity.window.attributes.apply {
            preferredRefreshRate = maxRefreshRate
            preferredDisplayModeId = findBestDisplayModeId(display)
        }
        activity.window.setSustainedPerformanceMode(true)

        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
            createPerformanceHintSession(targetFrameNanos)
        }
    }

    override fun onResume(owner: LifecycleOwner) {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.VANILLA_ICE_CREAM) {
            configureFrameRateBoost()
        }
        startFrameReporting()
    }

    override fun onPause(owner: LifecycleOwner) {
        stopFrameReporting()
    }

    override fun onDestroy(owner: LifecycleOwner) {
        stopPerformanceHints()
        activity.window.setSustainedPerformanceMode(false)
    }

    private fun startFrameReporting() {
        if (performanceSession == null || frameCallback != null) return

        lastFrameNanos = 0L
        frameCallback = object : Choreographer.FrameCallback {
            override fun doFrame(frameTimeNanos: Long) {
                val previousFrameNanos = lastFrameNanos
                lastFrameNanos = frameTimeNanos
                if (previousFrameNanos > 0L) {
                    val shouldContinue = reportFrameDuration(frameTimeNanos - previousFrameNanos)
                    if (!shouldContinue) return
                }
                if (frameCallback === this) {
                    choreographer.postFrameCallback(this)
                }
            }
        }.also(choreographer::postFrameCallback)
    }

    private fun stopFrameReporting() {
        frameCallback?.let(choreographer::removeFrameCallback)
        frameCallback = null
        lastFrameNanos = 0L
    }

    private fun reportFrameDuration(durationNanos: Long): Boolean {
        if (durationNanos <= 0L) return true

        return runCatching {
            performanceSession?.reportActualWorkDuration(durationNanos)
        }.fold(
            onSuccess = { true },
            onFailure = {
                stopPerformanceHints()
                false
            },
        )
    }

    private fun stopPerformanceHints() {
        stopFrameReporting()
        performanceSession?.closeQuietly()
        performanceSession = null
    }

    private fun PerformanceHintManager.Session.closeQuietly() {
        runCatching { close() }
    }

    private fun createPerformanceHintSession(targetDurationNanos: Long) {
        val performanceHintManager = activity.getSystemService(PerformanceHintManager::class.java)
        performanceSession = performanceHintManager
            ?.createHintSession(intArrayOf(android.os.Process.myTid()), targetDurationNanos)
            ?.also { it.updateTargetWorkDuration(targetDurationNanos) }
    }

    private fun configureFrameRateBoost() {
        val decorView = activity.window.decorView
        runCatching {
            decorView.javaClass
                .getMethod("setFrameRateBoostOnTouchEnabled", Boolean::class.javaPrimitiveType)
                .invoke(decorView, true)
        }
        runCatching {
            decorView.javaClass
                .getMethod("setFrameRatePowerSavingsBalanced", Boolean::class.javaPrimitiveType)
                .invoke(decorView, false)
        }
    }

    private fun findMaxRefreshRate(display: Display?): Float {
        val modes = display?.supportedModes.orEmpty()
        return modes.maxOfOrNull { it.refreshRate } ?: DEFAULT_REFRESH_RATE
    }

    private fun findBestDisplayModeId(display: Display?): Int {
        val modes = display?.supportedModes.orEmpty()
        return modes.maxByOrNull { it.refreshRate }?.modeId ?: 0
    }

    @Suppress("DEPRECATION")
    private fun currentDisplay(): Display? {
        return if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
            activity.display
        } else {
            activity.windowManager.defaultDisplay
        }
    }

    private fun refreshRateToFrameNanos(refreshRate: Float): Long {
        if (refreshRate <= 0f) return NANOS_PER_60HZ_FRAME
        return (NANOS_PER_SECOND / refreshRate).roundToLong()
    }

    private companion object {
        const val DEFAULT_REFRESH_RATE = 60f
        const val NANOS_PER_SECOND = 1_000_000_000L
        const val NANOS_PER_60HZ_FRAME = 16_666_667L
    }
}

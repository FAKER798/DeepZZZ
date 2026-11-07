package com.naviga.app

import android.Manifest
import android.content.Context
import android.content.pm.PackageManager
import android.os.Build
import android.media.AudioAttributes
import android.media.AudioDeviceInfo
import android.media.AudioManager
import android.media.AudioFormat
import android.media.AudioRecord
import android.media.AudioTrack
import android.media.MediaRecorder
import androidx.core.content.ContextCompat
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.currentCoroutineContext
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.io.ByteArrayOutputStream
import java.io.InputStream
import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.util.concurrent.atomic.AtomicBoolean

class TalkAudioStreamer(
    private val context: Context,
    private val scope: CoroutineScope,
) {
    private var listenJob: Job? = null
    private var voiceJob: Job? = null
    private val voiceStopRequested = AtomicBoolean(false)
    private val communicationModeLock = Any()
    private var communicationModeUsers = 0
    private var modeBeforeCommunication = AudioManager.MODE_NORMAL
    private var speakerphoneBeforeCommunication = false
    private var voiceStartedAtMillis: Long = 0L

    val listening: Boolean
        get() = listenJob?.isActive == true

    val recordingVoice: Boolean
        get() = voiceJob?.isActive == true

    fun startListening(boardApiProvider: () -> BoardApi): Boolean {
        if (listening) return true
        listenJob = scope.launch(Dispatchers.IO) {
            playBoardMicrophone(boardApiProvider)
        }
        return true
    }

    fun stopListening() {
        listenJob?.cancel()
        listenJob = null
    }

    fun startVoiceMessage(boardApiProvider: () -> BoardApi, onSent: (Boolean, Long) -> Unit): Boolean {
        if (recordingVoice) return true
        if (!hasRecordPermission()) return false
        voiceStopRequested.set(false)
        voiceStartedAtMillis = System.currentTimeMillis()
        voiceJob = scope.launch(Dispatchers.IO) {
            val wav = recordUntilCancelled()
            val durationMillis = System.currentTimeMillis() - voiceStartedAtMillis
            val sent = wav != null && durationMillis >= MIN_VOICE_MILLIS && boardApiProvider().playTalkAudio(wav)
            withContext(Dispatchers.Main) {
                voiceJob = null
                onSent(sent, durationMillis)
            }
        }
        return true
    }

    fun finishVoiceMessage() {
        voiceStopRequested.set(true)
    }

    fun stopAll() {
        stopListening()
        voiceStopRequested.set(true)
        voiceJob?.cancel()
        voiceJob = null
    }

    private fun hasRecordPermission(): Boolean {
        return ContextCompat.checkSelfPermission(
            context,
            Manifest.permission.RECORD_AUDIO,
        ) == PackageManager.PERMISSION_GRANTED
    }

    private suspend fun playBoardMicrophone(boardApiProvider: () -> BoardApi) {
        enterCommunicationMode()
        try {
            while (currentCoroutineContext().isActive) {
                val stream = boardApiProvider().liveAudioStream()
                if (stream == null) {
                    delay(RETRY_DELAY_MS)
                    continue
                }
                val player = createPlayer()
                if (player == null) {
                    stream.closeQuietly()
                    delay(RETRY_DELAY_MS)
                    continue
                }
                try {
                    player.play()
                    stream.copyToAudioTrack(player)
                } catch (_: Exception) {
                    delay(RETRY_DELAY_MS)
                } finally {
                    stream.closeQuietly()
                    runCatching { player.pause() }
                    player.release()
                }
            }
        } finally {
            exitCommunicationMode()
        }
    }

    private suspend fun recordUntilCancelled(): ByteArray? {
        enterCommunicationMode()
        var recorder: AudioRecord? = null
        try {
            recorder = createRecorder() ?: return null
            val pcm = ByteArrayOutputStream(SAMPLE_RATE * BYTES_PER_SAMPLE * 5)
            val buffer = ByteArray(RECORD_READ_BYTES)
            recorder.startRecording()
            while (
                currentCoroutineContext().isActive &&
                !voiceStopRequested.get() &&
                pcm.size() < MAX_VOICE_BYTES
            ) {
                val read = recorder.read(buffer, 0, buffer.size)
                if (read > 0) {
                    pcm.write(buffer, 0, read)
                } else {
                    delay(20)
                }
            }
            val pcmBytes = pcm.toByteArray()
            return if (pcmBytes.isEmpty()) null else wavBytes(pcmBytes)
        } catch (_: Exception) {
            return null
        } finally {
            recorder?.let {
                runCatching { it.stop() }
                it.release()
            }
            exitCommunicationMode()
        }
    }

    private fun createRecorder(): AudioRecord? {
        val minBuffer = AudioRecord.getMinBufferSize(
            SAMPLE_RATE,
            AudioFormat.CHANNEL_IN_MONO,
            AudioFormat.ENCODING_PCM_16BIT,
        )
        if (minBuffer <= 0) return null
        val format = AudioFormat.Builder()
            .setSampleRate(SAMPLE_RATE)
            .setChannelMask(AudioFormat.CHANNEL_IN_MONO)
            .setEncoding(AudioFormat.ENCODING_PCM_16BIT)
            .build()
        val recorder = AudioRecord.Builder()
            .setAudioSource(MediaRecorder.AudioSource.MIC)
            .setAudioFormat(format)
            .setBufferSizeInBytes(maxOf(minBuffer, RECORD_READ_BYTES * 2))
            .build()
        return recorder.takeIf { it.state == AudioRecord.STATE_INITIALIZED } ?: run {
            recorder.release()
            null
        }
    }

    private fun createPlayer(): AudioTrack? {
        val minBuffer = AudioTrack.getMinBufferSize(
            SAMPLE_RATE,
            AudioFormat.CHANNEL_OUT_MONO,
            AudioFormat.ENCODING_PCM_16BIT,
        )
        if (minBuffer <= 0) return null
        val format = AudioFormat.Builder()
            .setSampleRate(SAMPLE_RATE)
            .setChannelMask(AudioFormat.CHANNEL_OUT_MONO)
            .setEncoding(AudioFormat.ENCODING_PCM_16BIT)
            .build()
        val attributes = AudioAttributes.Builder()
            .setUsage(AudioAttributes.USAGE_VOICE_COMMUNICATION)
            .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH)
            .build()
        val player = AudioTrack.Builder()
            .setAudioAttributes(attributes)
            .setAudioFormat(format)
            .setBufferSizeInBytes(maxOf(minBuffer, PLAYBACK_BUFFER_BYTES))
            .setTransferMode(AudioTrack.MODE_STREAM)
            .setPerformanceMode(AudioTrack.PERFORMANCE_MODE_LOW_LATENCY)
            .build()
        val initialized = player.takeIf { it.state == AudioTrack.STATE_INITIALIZED } ?: run {
            player.release()
            return null
        }
        val speaker = context.getSystemService(AudioManager::class.java)
            ?.getDevices(AudioManager.GET_DEVICES_OUTPUTS)
            ?.firstOrNull { it.type == AudioDeviceInfo.TYPE_BUILTIN_SPEAKER }
        if (speaker != null) {
            initialized.setPreferredDevice(speaker)
        }
        return initialized
    }

    private fun enterCommunicationMode() {
        val audioManager = context.getSystemService(AudioManager::class.java) ?: return
        synchronized(communicationModeLock) {
            if (communicationModeUsers == 0) {
                modeBeforeCommunication = audioManager.mode
                @Suppress("DEPRECATION")
                speakerphoneBeforeCommunication = audioManager.isSpeakerphoneOn
                audioManager.mode = AudioManager.MODE_IN_COMMUNICATION
                val speaker = audioManager.getDevices(AudioManager.GET_DEVICES_OUTPUTS)
                    .firstOrNull { it.type == AudioDeviceInfo.TYPE_BUILTIN_SPEAKER }
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S && speaker != null) {
                    audioManager.setCommunicationDevice(speaker)
                } else {
                    @Suppress("DEPRECATION")
                    audioManager.isSpeakerphoneOn = true
                }
            }
            communicationModeUsers += 1
        }
    }

    private fun exitCommunicationMode() {
        val audioManager = context.getSystemService(AudioManager::class.java) ?: return
        synchronized(communicationModeLock) {
            communicationModeUsers = (communicationModeUsers - 1).coerceAtLeast(0)
            if (communicationModeUsers != 0) return
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
                audioManager.clearCommunicationDevice()
            } else {
                @Suppress("DEPRECATION")
                audioManager.isSpeakerphoneOn = speakerphoneBeforeCommunication
            }
            audioManager.mode = modeBeforeCommunication
        }
    }

    private suspend fun InputStream.copyToAudioTrack(player: AudioTrack) {
        val buffer = ByteArray(PLAYBACK_READ_BYTES)
        val discard = ByteArray(PLAYBACK_READ_BYTES)
        while (currentCoroutineContext().isActive) {
            var staleBytes = (available() - MAX_NETWORK_BACKLOG_BYTES).coerceAtLeast(0)
            while (staleBytes > 0 && currentCoroutineContext().isActive) {
                val dropped = read(discard, 0, minOf(discard.size, staleBytes))
                if (dropped <= 0) break
                staleBytes -= dropped
            }
            val read = read(buffer)
            if (read < 0) break
            if (read == 0) continue
            var offset = 0
            while (offset < read && currentCoroutineContext().isActive) {
                val written = player.write(buffer, offset, read - offset)
                if (written <= 0) break
                offset += written
            }
        }
    }

    private fun wavBytes(pcm: ByteArray): ByteArray {
        val out = ByteArrayOutputStream(44 + pcm.size)
        out.write("RIFF".toByteArray(Charsets.US_ASCII))
        out.writeIntLE(36 + pcm.size)
        out.write("WAVE".toByteArray(Charsets.US_ASCII))
        out.write("fmt ".toByteArray(Charsets.US_ASCII))
        out.writeIntLE(16)
        out.writeShortLE(1)
        out.writeShortLE(1)
        out.writeIntLE(SAMPLE_RATE)
        out.writeIntLE(SAMPLE_RATE * BYTES_PER_SAMPLE)
        out.writeShortLE(BYTES_PER_SAMPLE)
        out.writeShortLE(16)
        out.write("data".toByteArray(Charsets.US_ASCII))
        out.writeIntLE(pcm.size)
        out.write(pcm)
        return out.toByteArray()
    }

    private fun ByteArrayOutputStream.writeIntLE(value: Int) {
        write(ByteBuffer.allocate(4).order(ByteOrder.LITTLE_ENDIAN).putInt(value).array())
    }

    private fun ByteArrayOutputStream.writeShortLE(value: Int) {
        write(ByteBuffer.allocate(2).order(ByteOrder.LITTLE_ENDIAN).putShort(value.toShort()).array())
    }

    private fun InputStream.closeQuietly() {
        runCatching { close() }
    }

    private companion object {
        const val SAMPLE_RATE = 16_000
        const val BYTES_PER_SAMPLE = 2
        const val RECORD_READ_BYTES = 3_200
        const val PLAYBACK_BUFFER_BYTES = SAMPLE_RATE * BYTES_PER_SAMPLE / 20
        const val PLAYBACK_READ_BYTES = SAMPLE_RATE * BYTES_PER_SAMPLE / 50
        const val MAX_NETWORK_BACKLOG_BYTES = SAMPLE_RATE * BYTES_PER_SAMPLE / 25
        const val MIN_VOICE_MILLIS = 350L
        const val MAX_VOICE_MILLIS = 15_000L
        const val MAX_VOICE_BYTES = SAMPLE_RATE * BYTES_PER_SAMPLE * MAX_VOICE_MILLIS.toInt() / 1_000
        const val RETRY_DELAY_MS = 50L
    }
}

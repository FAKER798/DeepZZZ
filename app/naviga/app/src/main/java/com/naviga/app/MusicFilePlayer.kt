package com.naviga.app

data class MusicTrack(
    val title: String,
    val sizeBytes: Long,
    val durationMillis: Long?,
    val boardTrackId: String,
)

data class MusicPlaybackResult(
    val ok: Boolean,
    val message: String,
)

class MusicFilePlayer(
    private val boardApiProvider: () -> BoardApi,
) {
    fun play(
        track: MusicTrack,
        volume: Float,
        positionMillis: Long? = null,
        repeatOne: Boolean = true,
    ): MusicPlaybackResult {
        val started = boardApiProvider().playStoredLullaby(
            trackId = track.boardTrackId,
            volume = volume.coerceIn(0f, 1f),
            positionMillis = positionMillis,
            repeatOne = repeatOne,
        )
        return result(started, "板端摇篮曲播放中", "板端播放失败")
    }

    fun pause(): MusicPlaybackResult {
        return result(boardApiProvider().pauseLullaby(), "已暂停", "暂停失败")
    }

    fun stop(): MusicPlaybackResult {
        return result(boardApiProvider().stopMusicAudio(), "已停止板端播放", "停止播放失败")
    }

    fun seek(positionMillis: Long): MusicPlaybackResult {
        return result(boardApiProvider().seekLullaby(positionMillis), "播放位置已更新", "调整进度失败")
    }

    fun setVolume(volume: Float): MusicPlaybackResult {
        return result(
            boardApiProvider().setMusicVolume(volume.coerceIn(0f, 1f)),
            "音量已调整",
            "音量调整失败",
        )
    }

    private fun result(ok: Boolean, success: String, failure: String): MusicPlaybackResult {
        return MusicPlaybackResult(ok, if (ok) success else failure)
    }
}

fun BoardLullabyTrack.toMusicTrack(): MusicTrack {
    return MusicTrack(
        title = title,
        sizeBytes = sizeBytes,
        durationMillis = durationMillis,
        boardTrackId = id,
    )
}

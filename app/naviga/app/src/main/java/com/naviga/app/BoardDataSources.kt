package com.naviga.app

class BoardEnvironmentDataSource(
    private val boardApi: BoardApi,
    private val fallback: EnvironmentDataSource = StubEnvironmentDataSource(),
) : EnvironmentDataSource {
    private var lastBoardReading: EnvironmentReading? = null

    override fun currentReading(): EnvironmentReading {
        val reading = boardApi.environment()
        if (reading != null) {
            lastBoardReading = reading
            return reading
        }
        return lastBoardReading ?: fallback.currentReading()
    }
}

class BoardAlertDataSourceAdapter(
    private val boardApi: BoardApi,
) : BoardAlertDataSource {
    override fun activeAlerts(settings: AlertSettingsSnapshot): List<BoardAlert> {
        return boardApi.alerts(settings)
    }
}

class BoardGrowthAlbumSyncClient(
    private val boardApi: BoardApi,
) : GrowthAlbumSyncClient {
    override fun syncBoardPhotos(albumStore: GrowthAlbumStore): AlbumSyncResult {
        val imported = boardApi.photos().mapNotNull { photo ->
            boardApi.photoStream(photo)?.let { stream ->
                albumStore.importMoment(
                    capturedAtMillis = photo.capturedAtMillis,
                    source = stream,
                )
            }
        }
        return AlbumSyncResult(importedMoments = imported)
    }
}

package com.naviga.app

data class AlbumSyncResult(
    val importedMoments: List<GrowthMoment>,
) {
    val importedCount: Int
        get() = importedMoments.size
}

interface GrowthAlbumSyncClient {
    fun syncBoardPhotos(albumStore: GrowthAlbumStore): AlbumSyncResult
}

class StubGrowthAlbumSyncClient : GrowthAlbumSyncClient {
    override fun syncBoardPhotos(albumStore: GrowthAlbumStore): AlbumSyncResult {
        val now = System.currentTimeMillis()
        val imported = listOf(now - 122_000L, now - 64_000L).map { capturedAt ->
            albumStore.addSyncedMoment(capturedAtMillis = capturedAt)
        }.filterNotNull()
        return AlbumSyncResult(importedMoments = imported)
    }
}

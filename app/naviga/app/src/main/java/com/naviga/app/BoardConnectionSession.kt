package com.naviga.app

import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext

enum class BoardConnectionStatus(val label: String) {
    Unknown("未检查"),
    Checking("检查中"),
    Connected("已连接"),
    Unreachable("不可达"),
}

class BoardConnectionSession(
    val identity: BoardIdentity = DefaultBoardConnection.identity,
    val api: BoardApi = LanBoardApi(HttpBoardRequestClient(identity.endpoint)),
) {
    var status: BoardConnectionStatus by mutableStateOf(BoardConnectionStatus.Unknown)
        private set

    val endpointLabel: String
        get() = identity.endpoint.baseUrl

    suspend fun checkHealth(): Boolean {
        status = BoardConnectionStatus.Checking
        val healthy = withContext(Dispatchers.IO) {
            api.health()
        }
        status = if (healthy) BoardConnectionStatus.Connected else BoardConnectionStatus.Unreachable
        return healthy
    }
}

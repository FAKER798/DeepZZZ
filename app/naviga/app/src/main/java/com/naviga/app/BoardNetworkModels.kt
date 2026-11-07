package com.naviga.app

data class BoardEndpoint(
    val host: String,
    val port: Int = DEFAULT_BOARD_PORT,
) {
    val baseUrl: String
        get() = "http://$host:$port"

    companion object {
        const val DEFAULT_BOARD_PORT = 7860
    }
}

data class BoardIdentity(
    val id: String,
    val name: String,
    val endpoint: BoardEndpoint,
)

object DefaultBoardConnection {
    const val HOST = "192.168.22.193"
    const val PORT = BoardEndpoint.DEFAULT_BOARD_PORT
    const val ID = "fixed-192.168.22.193-7860"
    const val NAME = "DeepZZZ K1"

    val endpoint = BoardEndpoint(host = HOST, port = PORT)
    val identity = BoardIdentity(
        id = ID,
        name = NAME,
        endpoint = endpoint,
    )
}

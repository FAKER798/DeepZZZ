package com.naviga.app

import java.io.FilterInputStream
import java.io.InputStream
import java.net.HttpURLConnection
import java.net.URL
import java.net.SocketTimeoutException

data class BoardHttpResponse(
    val code: Int,
    val body: String,
) {
    val ok: Boolean
        get() = code in 200..299
}

interface BoardRequestClient {
    fun url(path: String): String

    fun get(path: String): BoardHttpResponse

    fun post(path: String, body: ByteArray, contentType: String): BoardHttpResponse

    fun post(path: String, body: ByteArray, contentType: String, readTimeoutMillis: Int): BoardHttpResponse

    fun delete(path: String): BoardHttpResponse

    fun getStream(path: String): InputStream?

    /** POST whose response is read incrementally. Not retried: the board may already be answering. */
    fun postStream(path: String, body: ByteArray, contentType: String, readTimeoutMillis: Int): BoardStreamResponse
}

/**
 * A streamed response. [stream] is set for 2xx; otherwise [errorBody] holds the board's error JSON.
 * [cancel] may be called from any thread and makes a blocked read fail right away.
 */
class BoardStreamResponse(
    val code: Int,
    val stream: InputStream?,
    val errorBody: String,
    private val onCancel: () -> Unit,
) : java.io.Closeable {
    fun cancel() = onCancel()

    override fun close() {
        try {
            stream?.close()
        } finally {
            onCancel()
        }
    }
}

class HttpBoardRequestClient(
    private val endpoint: BoardEndpoint,
    private val connectTimeoutMillis: Int = DEFAULT_TIMEOUT_MS,
    private val readTimeoutMillis: Int = DEFAULT_TIMEOUT_MS,
) : BoardRequestClient {
    override fun url(path: String): String {
        val normalizedPath = if (path.startsWith("/")) path else "/$path"
        return "${endpoint.baseUrl}$normalizedPath"
    }

    override fun get(path: String): BoardHttpResponse {
        return withRetry {
            open(path).useConnection { connection ->
                val code = connection.responseCode
                val stream = if (code in 200..299) connection.inputStream else connection.errorStream
                BoardHttpResponse(
                    code = code,
                    body = stream?.bufferedReader()?.use { it.readText() }.orEmpty(),
                )
            }
        }
    }

    override fun post(path: String, body: ByteArray, contentType: String): BoardHttpResponse {
        return post(path, body, contentType, readTimeoutMillis)
    }

    override fun post(
        path: String,
        body: ByteArray,
        contentType: String,
        readTimeoutMillis: Int,
    ): BoardHttpResponse {
        return withRetry {
            open(path, readTimeoutMillis = readTimeoutMillis).useConnection { connection ->
                connection.requestMethod = "POST"
                connection.doOutput = true
                connection.setRequestProperty("Content-Type", contentType)
                connection.setRequestProperty("Content-Length", body.size.toString())
                connection.outputStream.use { it.write(body) }
                val code = connection.responseCode
                val stream = if (code in 200..299) connection.inputStream else connection.errorStream
                BoardHttpResponse(
                    code = code,
                    body = stream?.bufferedReader()?.use { it.readText() }.orEmpty(),
                )
            }
        }
    }

    override fun delete(path: String): BoardHttpResponse {
        return withRetry {
            open(path).useConnection { connection ->
                connection.requestMethod = "DELETE"
                val code = connection.responseCode
                val stream = if (code in 200..299) connection.inputStream else connection.errorStream
                BoardHttpResponse(
                    code = code,
                    body = stream?.bufferedReader()?.use { it.readText() }.orEmpty(),
                )
            }
        }
    }

    override fun getStream(path: String): InputStream? {
        val connection = open(path, readTimeoutMillis = STREAM_READ_TIMEOUT_MS)
        val code = connection.responseCode
        if (code !in 200..299) {
            connection.disconnect()
            return null
        }
        return DisconnectingInputStream(connection.inputStream, connection)
    }

    override fun postStream(
        path: String,
        body: ByteArray,
        contentType: String,
        readTimeoutMillis: Int,
    ): BoardStreamResponse {
        val connection = open(path, readTimeoutMillis = readTimeoutMillis)
        try {
            connection.requestMethod = "POST"
            connection.doOutput = true
            connection.setRequestProperty("Content-Type", contentType)
            connection.setFixedLengthStreamingMode(body.size)
            connection.outputStream.use { it.write(body) }
            val code = connection.responseCode
            if (code !in 200..299) {
                val text = connection.errorStream?.bufferedReader()?.use { it.readText() }.orEmpty()
                connection.disconnect()
                return BoardStreamResponse(code, null, text) {}
            }
            return BoardStreamResponse(code, connection.inputStream, "") { connection.disconnect() }
        } catch (error: Exception) {
            connection.disconnect()
            throw error
        }
    }

    private fun open(path: String, readTimeoutMillis: Int = this.readTimeoutMillis): HttpURLConnection {
        return (URL(url(path)).openConnection() as HttpURLConnection).apply {
            requestMethod = "GET"
            connectTimeout = connectTimeoutMillis
            readTimeout = readTimeoutMillis
        }
    }

    private inline fun <T> HttpURLConnection.useConnection(block: (HttpURLConnection) -> T): T {
        return try {
            block(this)
        } finally {
            disconnect()
        }
    }

    private fun <T> withRetry(block: () -> T): T {
        var lastError: Exception? = null
        repeat(REQUEST_ATTEMPTS) { attempt ->
            try {
                return block()
            } catch (error: Exception) {
                lastError = error
                if (!error.isRetryableNetworkError() || attempt == REQUEST_ATTEMPTS - 1) {
                    throw error
                }
                Thread.sleep(RETRY_DELAY_MS * (attempt + 1))
            }
        }
        throw lastError ?: IllegalStateException("HTTP request failed")
    }

    private fun Exception.isRetryableNetworkError(): Boolean {
        return this is SocketTimeoutException ||
            this is java.net.ConnectException ||
            this is java.net.NoRouteToHostException ||
            this is java.net.UnknownHostException ||
            this is java.io.EOFException ||
            this is java.io.IOException
    }

    private companion object {
        const val DEFAULT_TIMEOUT_MS = 1_500
        const val STREAM_READ_TIMEOUT_MS = 0
        const val REQUEST_ATTEMPTS = 2
        const val RETRY_DELAY_MS = 40L
    }
}

private class DisconnectingInputStream(
    input: InputStream,
    private val connection: HttpURLConnection,
) : FilterInputStream(input) {
    override fun close() {
        try {
            super.close()
        } finally {
            connection.disconnect()
        }
    }
}

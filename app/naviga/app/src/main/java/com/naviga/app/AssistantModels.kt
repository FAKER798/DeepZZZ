package com.naviga.app

import org.json.JSONObject

enum class AssistantRole(val apiValue: String) {
    User("user"),
    Assistant("assistant"),
}

/** One turn of the conversation as sent to the board. */
data class AssistantTurn(val role: AssistantRole, val text: String)

data class AssistantStatus(
    /** The board has an LLM URL configured. */
    val available: Boolean,
    /** llama-server answered its health check. */
    val llmReady: Boolean,
    val busy: Boolean,
)

sealed interface AssistantEvent {
    /** The board accepted the question; [facts] are the device records it put into the prompt. */
    data class Start(val facts: List<String>) : AssistantEvent

    data class Delta(val text: String) : AssistantEvent

    data class Done(
        val reply: String,
        val truncated: Boolean,
        val elapsedMillis: Long?,
        val firstTokenMillis: Long?,
        val tokens: Int?,
        val tokensPerSecond: Double?,
    ) : AssistantEvent

    data class Failed(val message: String, val busy: Boolean = false) : AssistantEvent
}

/**
 * Lets the UI stop an answer that is being read on a background thread: [cancel] disconnects the
 * HTTP stream, which makes the blocked read fail and the board stop generating.
 */
class AssistantCancelSignal {
    @Volatile
    var cancelled: Boolean = false
        private set

    private var action: (() -> Unit)? = null

    @Synchronized
    fun attach(block: () -> Unit) {
        if (cancelled) block() else action = block
    }

    @Synchronized
    fun detach() {
        action = null
    }

    fun cancel() {
        val pending = synchronized(this) {
            cancelled = true
            action.also { action = null }
        }
        pending?.invoke()
    }
}

internal fun JSONObject.toAssistantStatus(): AssistantStatus {
    val llm = optJSONObject("llm")
    return AssistantStatus(
        available = optBoolean("available", false),
        llmReady = llm != null && llm.optBoolean("ok", false),
        busy = optBoolean("busy", false),
    )
}

/** Parses one NDJSON line of `/api/assistant/chat`; unknown lines return null. */
internal fun parseAssistantEvent(line: String): AssistantEvent? {
    val json = runCatching { JSONObject(line) }.getOrNull() ?: return null
    return when (json.optString("type")) {
        "start" -> {
            val facts = json.optJSONArray("facts")
            AssistantEvent.Start(
                List(facts?.length() ?: 0) { index -> facts!!.optString(index) }.filter { it.isNotBlank() },
            )
        }
        "delta" -> AssistantEvent.Delta(json.optString("text"))
        "done" -> AssistantEvent.Done(
            reply = json.optString("reply"),
            truncated = json.optBoolean("truncated", false),
            elapsedMillis = json.optDoubleOrNull("elapsed_ms")?.toLong(),
            firstTokenMillis = json.optDoubleOrNull("first_token_ms")?.toLong(),
            tokens = json.optDoubleOrNull("tokens")?.toInt(),
            tokensPerSecond = json.optDoubleOrNull("tokens_per_s"),
        )
        "error" -> AssistantEvent.Failed(
            message = json.optString("error").ifBlank { "本地模型出错了，请重试" },
            busy = json.optString("code") == "busy",
        )
        else -> null
    }
}

/** Maps a non-2xx answer of `/api/assistant/chat` to a message for parents. */
internal fun assistantErrorMessage(code: Int, body: String): String {
    val boardError = runCatching { JSONObject(body).optString("error") }.getOrNull().orEmpty()
    return when {
        code == 404 -> "看护设备上的程序还没更新，暂不支持 AI 问答"
        code == 503 -> "看护设备上的本地模型没有启动"
        boardError.contains("longer than") -> "问题太长了，请精简到 500 字以内"
        code == 400 -> "问题格式不对，请重新输入"
        else -> "看护设备出错了（$code），请稍后重试"
    }
}

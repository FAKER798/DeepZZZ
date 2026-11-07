package com.naviga.app

import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.rounded.Send
import androidx.compose.material.icons.rounded.AutoAwesome
import androidx.compose.material.icons.rounded.Refresh
import androidx.compose.material.icons.rounded.Stop
import androidx.compose.material.icons.rounded.Tune
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.FilledIconButton
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.SuggestionChip
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.Stable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateListOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.unit.dp
import java.util.Locale
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

private val SUGGESTED_QUESTIONS = listOf(
    "宝宝现在该小睡了吗？",
    "今天的奶量够不够？",
    "现在的室温适合宝宝睡觉吗？",
    "宝宝吐奶怎么办？",
    "夜里频繁醒怎么哄睡？",
    "什么时候开始加辅食？",
)

private const val MAX_QUESTION_CHARS = 500

enum class AssistantBubbleState { Waiting, Streaming, Done, Failed, Stopped }

@Stable
class AssistantMessage(val id: Long, val role: AssistantRole, text: String) {
    var text by mutableStateOf(text)
    var state by mutableStateOf(if (role == AssistantRole.User) AssistantBubbleState.Done else AssistantBubbleState.Waiting)
    var facts by mutableStateOf(emptyList<String>())
    var stats by mutableStateOf<String?>(null)
    var error by mutableStateOf<String?>(null)
    var truncated by mutableStateOf(false)
}

/**
 * Conversation held above the page, so switching tabs keeps it and an answer keeps streaming.
 * [scope] must outlive the page (MainActivity's scope).
 */
@Stable
class AssistantChatState(private val boardApi: BoardApi, private val scope: CoroutineScope) {
    val messages = mutableStateListOf<AssistantMessage>()
    var answering by mutableStateOf(false)
        private set
    var status by mutableStateOf<AssistantStatus?>(null)
        private set
    var statusChecked by mutableStateOf(false)
        private set

    private var nextId = 0L
    private var cancelSignal: AssistantCancelSignal? = null

    fun refreshStatus() {
        scope.launch {
            val latest = withContext(Dispatchers.IO) { boardApi.assistantStatus() }
            status = latest
            statusChecked = true
        }
    }

    fun send(text: String) {
        val question = text.trim().take(MAX_QUESTION_CHARS)
        if (question.isEmpty() || answering) return
        val history = completedTurns() + AssistantTurn(AssistantRole.User, question)
        messages += AssistantMessage(nextId++, AssistantRole.User, question)
        val reply = AssistantMessage(nextId++, AssistantRole.Assistant, "")
        messages += reply
        val signal = AssistantCancelSignal()
        cancelSignal = signal
        answering = true
        scope.launch {
            try {
                withContext(Dispatchers.IO) {
                    boardApi.askAssistant(history, signal) { event -> reply.apply(event) }
                }
            } finally {
                if (signal.cancelled && reply.state != AssistantBubbleState.Done) {
                    reply.state = AssistantBubbleState.Stopped
                }
                if (cancelSignal === signal) cancelSignal = null
                answering = false
                if (reply.state == AssistantBubbleState.Failed) refreshStatus()
            }
        }
    }

    fun stop() {
        val signal = cancelSignal ?: return
        // Disconnecting touches the socket, so keep it off the main thread.
        scope.launch(Dispatchers.IO) { signal.cancel() }
    }

    fun retry(reply: AssistantMessage) {
        if (answering) return
        val index = messages.indexOf(reply)
        val question = messages.getOrNull(index - 1)?.takeIf { it.role == AssistantRole.User } ?: return
        messages.removeAt(index)
        messages.removeAt(index - 1)
        send(question.text)
    }

    fun clear() {
        stop()
        messages.clear()
    }

    /** Question/answer pairs that got an answer; failed or stopped empty attempts are left out. */
    private fun completedTurns(): List<AssistantTurn> {
        val turns = mutableListOf<AssistantTurn>()
        messages.zipWithNext().forEach { (question, answer) ->
            val answered = answer.role == AssistantRole.Assistant &&
                answer.text.isNotBlank() &&
                (answer.state == AssistantBubbleState.Done || answer.state == AssistantBubbleState.Stopped)
            if (question.role == AssistantRole.User && answered) {
                turns += AssistantTurn(AssistantRole.User, question.text)
                turns += AssistantTurn(AssistantRole.Assistant, answer.text)
            }
        }
        return turns
    }

    // Called on the IO thread; snapshot state may be written from any thread.
    private fun AssistantMessage.apply(event: AssistantEvent) {
        when (event) {
            is AssistantEvent.Start -> facts = event.facts
            is AssistantEvent.Delta -> {
                text += event.text
                state = AssistantBubbleState.Streaming
            }
            is AssistantEvent.Done -> {
                if (event.reply.isNotBlank()) text = event.reply
                truncated = event.truncated
                stats = formatStats(event)
                state = AssistantBubbleState.Done
            }
            is AssistantEvent.Failed -> {
                error = event.message
                state = AssistantBubbleState.Failed
            }
        }
    }
}

private fun formatStats(event: AssistantEvent.Done): String? {
    val parts = mutableListOf<String>()
    event.firstTokenMillis?.let { parts += "首字 ${seconds(it)} 秒" }
    event.elapsedMillis?.let { parts += "用时 ${seconds(it)} 秒" }
    event.tokensPerSecond?.let { parts += String.format(Locale.US, "%.1f token/s", it) }
    return parts.takeIf { it.isNotEmpty() }?.joinToString(" · ")
}

private fun seconds(millis: Long): String = String.format(Locale.US, "%.1f", millis / 1000.0)

/** Small models still sprinkle Markdown; show plain text. */
private fun plainText(text: String): String {
    return text
        .replace("**", "")
        .replace("__", "")
        .lines()
        .joinToString("\n") { line -> line.trimStart().removePrefix("### ").removePrefix("## ").removePrefix("# ") }
        .trim()
}

@Composable
fun AssistantPage(state: AssistantChatState) {
    LaunchedEffect(state) { state.refreshStatus() }
    var input by rememberSaveable { mutableStateOf("") }
    val listState = rememberLazyListState()
    val lastText = state.messages.lastOrNull()?.text?.length ?: 0
    LaunchedEffect(state.messages.size, lastText) {
        if (state.messages.isNotEmpty()) listState.animateScrollToItem(state.messages.size - 1, Int.MAX_VALUE / 2)
    }

    fun submit(text: String) {
        if (text.isBlank() || state.answering) return
        state.send(text)
        input = ""
    }

    Column(modifier = Modifier.fillMaxSize().imePadding()) {
        AssistantHeader(
            state = state,
            onNewChat = { state.clear() },
            modifier = Modifier.padding(start = 18.dp, end = 8.dp, top = 18.dp),
        )
        Box(modifier = Modifier.weight(1f).fillMaxWidth()) {
            if (state.messages.isEmpty()) {
                EmptyAssistant(enabled = !state.answering, onAsk = ::submit)
            } else {
                LazyColumn(
                    state = listState,
                    modifier = Modifier.fillMaxSize(),
                    contentPadding = PaddingValues(horizontal = 18.dp, vertical = 12.dp),
                    verticalArrangement = Arrangement.spacedBy(12.dp),
                ) {
                    items(state.messages, key = { it.id }) { message ->
                        if (message.role == AssistantRole.User) {
                            UserBubble(message)
                        } else {
                            AnswerBubble(
                                message = message,
                                onRetry = { state.retry(message) },
                                retryEnabled = !state.answering,
                            )
                        }
                    }
                }
            }
        }
        InputBar(
            value = input,
            onValueChange = { input = it.take(MAX_QUESTION_CHARS) },
            answering = state.answering,
            onSend = { submit(input) },
            onStop = { state.stop() },
        )
    }
}

@Composable
private fun AssistantHeader(state: AssistantChatState, onNewChat: () -> Unit, modifier: Modifier = Modifier) {
    Column(modifier = modifier) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            PageHeader(title = "AI 问答")
            Spacer(Modifier.weight(1f))
            IconButton(onClick = { state.refreshStatus() }) {
                Icon(Icons.Rounded.Refresh, contentDescription = "检查模型状态")
            }
            TextButton(onClick = onNewChat, enabled = state.messages.isNotEmpty()) { Text("新对话") }
        }
        val status = state.status
        val (color, label) = when {
            !state.statusChecked -> MaterialTheme.colorScheme.outline to "正在检查本地模型…"
            status == null -> MaterialTheme.colorScheme.error to "连不上看护设备"
            !status.available -> MaterialTheme.colorScheme.error to "设备上没有配置本地模型"
            !status.llmReady -> MaterialTheme.colorScheme.error to "本地模型没有启动"
            status.busy || state.answering -> MaterialTheme.colorScheme.tertiary to "本地模型正在回答"
            else -> MaterialTheme.colorScheme.primary to "本地模型就绪 · 在看护设备上运行，不联网"
        }
        Row(verticalAlignment = Alignment.CenterVertically) {
            Box(Modifier.size(8.dp).background(color, CircleShape))
            Text(
                text = label,
                style = MaterialTheme.typography.bodyMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier.padding(start = 8.dp),
            )
        }
    }
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun EmptyAssistant(enabled: Boolean, onAsk: (String) -> Unit) {
    Column(
        modifier = Modifier
            .fillMaxSize()
            .verticalScroll(rememberScrollState())
            .padding(horizontal = 18.dp, vertical = 16.dp),
        verticalArrangement = Arrangement.spacedBy(16.dp),
    ) {
        PressedCard(contentPadding = PaddingValues(18.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                IconBadge(icon = Icons.Rounded.AutoAwesome)
                Text(
                    text = "育儿小助手",
                    style = MaterialTheme.typography.titleLarge,
                    modifier = Modifier.padding(start = 12.dp),
                )
            }
            Spacer(Modifier.size(10.dp))
            Text(
                text = "问喂养、睡眠、护理的问题。回答由看护设备上的本地小模型生成，" +
                    "会参考宝宝月龄、今天的记录和房间温湿度，数据不出家门。",
                style = MaterialTheme.typography.bodyMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
        Text("可以这样问", style = MaterialTheme.typography.titleMedium)
        FlowRow(
            horizontalArrangement = Arrangement.spacedBy(8.dp),
            verticalArrangement = Arrangement.spacedBy(4.dp),
        ) {
            SUGGESTED_QUESTIONS.forEach { question ->
                SuggestionChip(onClick = { onAsk(question) }, label = { Text(question) }, enabled = enabled)
            }
        }
        Text(
            text = "AI 建议仅供参考，不能代替医生。宝宝发烧、呼吸困难、精神差时请及时就医。",
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
    }
}

@Composable
private fun UserBubble(message: AssistantMessage) {
    Box(modifier = Modifier.fillMaxWidth(), contentAlignment = Alignment.CenterEnd) {
        Surface(
            color = MaterialTheme.colorScheme.primary,
            contentColor = MaterialTheme.colorScheme.onPrimary,
            shape = RoundedCornerShape(topStart = 20.dp, topEnd = 6.dp, bottomStart = 20.dp, bottomEnd = 20.dp),
            modifier = Modifier.widthIn(max = 300.dp),
        ) {
            Text(
                text = message.text,
                style = MaterialTheme.typography.bodyLarge,
                modifier = Modifier.padding(horizontal = 14.dp, vertical = 10.dp),
            )
        }
    }
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun AnswerBubble(message: AssistantMessage, onRetry: () -> Unit, retryEnabled: Boolean) {
    Surface(
        color = MaterialTheme.colorScheme.surface,
        shape = RoundedCornerShape(topStart = 6.dp, topEnd = 20.dp, bottomStart = 20.dp, bottomEnd = 20.dp),
        tonalElevation = 1.dp,
        shadowElevation = 1.dp,
        modifier = Modifier.fillMaxWidth(0.92f),
    ) {
        Column(
            modifier = Modifier.padding(horizontal = 14.dp, vertical = 12.dp),
            verticalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            if (message.facts.isNotEmpty()) {
                FlowRow(horizontalArrangement = Arrangement.spacedBy(6.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                    Icon(
                        Icons.Rounded.Tune,
                        contentDescription = "参考了设备记录",
                        tint = MaterialTheme.colorScheme.primary,
                        modifier = Modifier.size(18.dp).align(Alignment.CenterVertically),
                    )
                    message.facts.forEach { fact -> FactTag(fact) }
                }
            }
            when {
                message.text.isEmpty() && message.state == AssistantBubbleState.Waiting -> ThinkingRow(message.facts.isNotEmpty())
                message.text.isNotEmpty() -> Text(
                    text = plainText(message.text) + if (message.state == AssistantBubbleState.Streaming) " ▍" else "",
                    style = MaterialTheme.typography.bodyLarge,
                )
            }
            if (message.truncated) {
                Text("回答较长，已截断。可以接着问“继续说”。", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
            when (message.state) {
                AssistantBubbleState.Failed -> Row(verticalAlignment = Alignment.CenterVertically) {
                    Text(
                        text = message.error ?: "出错了",
                        style = MaterialTheme.typography.bodyMedium,
                        color = MaterialTheme.colorScheme.error,
                        modifier = Modifier.weight(1f),
                    )
                    TextButton(onClick = onRetry, enabled = retryEnabled) { Text("重试") }
                }
                AssistantBubbleState.Stopped -> Text(
                    text = if (message.text.isEmpty()) "已停止" else "已停止回答",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
                AssistantBubbleState.Done -> message.stats?.let {
                    Text(
                        text = "本地模型 · $it",
                        style = MaterialTheme.typography.labelSmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
                else -> Unit
            }
        }
    }
}

@Composable
private fun FactTag(text: String) {
    Surface(
        color = MaterialTheme.colorScheme.secondaryContainer,
        contentColor = MaterialTheme.colorScheme.onSecondaryContainer,
        shape = RoundedCornerShape(8.dp),
    ) {
        Text(text, style = MaterialTheme.typography.labelMedium, modifier = Modifier.padding(horizontal = 8.dp, vertical = 3.dp))
    }
}

@Composable
private fun ThinkingRow(readContext: Boolean) {
    val transition = rememberInfiniteTransition(label = "thinking")
    val alpha by transition.animateFloat(
        initialValue = 0.35f,
        targetValue = 1f,
        animationSpec = infiniteRepeatable(tween(700), RepeatMode.Reverse),
        label = "thinkingAlpha",
    )
    Row(verticalAlignment = Alignment.CenterVertically) {
        CircularProgressIndicator(modifier = Modifier.size(16.dp), strokeWidth = 2.dp)
        Text(
            text = if (readContext) "本地模型思考中…" else "正在连接看护设备…",
            style = MaterialTheme.typography.bodyMedium,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            modifier = Modifier.padding(start = 10.dp).graphicsLayer { this.alpha = alpha },
        )
    }
}

@Composable
private fun InputBar(
    value: String,
    onValueChange: (String) -> Unit,
    answering: Boolean,
    onSend: () -> Unit,
    onStop: () -> Unit,
) {
    Surface(color = Color.Transparent) {
        Row(
            modifier = Modifier.fillMaxWidth().padding(start = 18.dp, end = 12.dp, top = 6.dp, bottom = 10.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            OutlinedTextField(
                value = value,
                onValueChange = onValueChange,
                placeholder = { Text("问问育儿小助手…") },
                maxLines = 4,
                shape = RoundedCornerShape(24.dp),
                keyboardOptions = KeyboardOptions(imeAction = ImeAction.Send),
                keyboardActions = KeyboardActions(onSend = { if (!answering) onSend() }),
                modifier = Modifier.weight(1f),
            )
            Spacer(Modifier.size(8.dp))
            if (answering) {
                FilledIconButton(onClick = onStop, modifier = Modifier.size(52.dp)) {
                    Icon(Icons.Rounded.Stop, contentDescription = "停止回答")
                }
            } else {
                FilledIconButton(onClick = onSend, enabled = value.isNotBlank(), modifier = Modifier.size(52.dp)) {
                    Icon(Icons.AutoMirrored.Rounded.Send, contentDescription = "发送")
                }
            }
        }
    }
}

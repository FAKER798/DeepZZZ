package com.naviga.app

import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.rounded.BabyChangingStation
import androidx.compose.material.icons.rounded.Bedtime
import androidx.compose.material.icons.rounded.Cake
import androidx.compose.material.icons.rounded.Edit
import androidx.compose.material.icons.rounded.LocalDrink
import androidx.compose.material.icons.rounded.MonitorWeight
import androidx.compose.material.icons.rounded.Refresh
import androidx.compose.material.icons.rounded.Restaurant
import androidx.compose.material.icons.rounded.WbSunny
import androidx.compose.material3.Button
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FilledTonalButton
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.pulltorefresh.PullToRefreshBox
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableLongStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.geometry.CornerRadius
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.drawText
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.rememberTextMeasurer
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.compose.LocalLifecycleOwner
import androidx.lifecycle.repeatOnLifecycle
import java.time.Instant
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext

// While the page is on screen it re-reads the board every few seconds, so records
// added by another phone or by the board itself show up without a manual refresh.
private const val CARE_LIVE_REFRESH_MS = 5_000L
private const val CARE_RETRY_REFRESH_MS = 10_000L
private const val CARE_CLOCK_TICK_MS = 15_000L
private const val RECENT_RECORD_DAYS = 3
private const val DAY_MILLIS = 86_400_000L

/** Loads and writes the board's care log; one instance per screen. */
class CarePageState(private val boardApi: BoardApi) {
    var overview by mutableStateOf<CareOverview?>(null)
        private set
    var recentRecords by mutableStateOf<List<CareRecord>>(emptyList())
        private set
    var growthRecords by mutableStateOf<List<CareRecord>>(emptyList())
        private set
    var loadFailed by mutableStateOf(false)
        private set
    var saving by mutableStateOf(false)
        private set

    /** Phone time of the last refresh that read everything, or null before the first one. */
    var lastSyncedMillis by mutableStateOf<Long?>(null)
        private set

    /** True only while a refresh the user asked for (pull or button) is running. */
    var userRefreshing by mutableStateOf(false)
        private set

    // The periodic loop, pull-to-refresh and post-save refreshes share one request at a time.
    private val refreshLock = Mutex()

    // Board clock minus phone clock, so "now" and elapsed times follow the board.
    private var clockOffsetMillis = 0L

    fun boardNow(): Long = System.currentTimeMillis() + clockOffsetMillis

    suspend fun refresh() {
        refreshLock.withLock { load() }
    }

    /** Refresh started by the user; shows the pull-to-refresh spinner. */
    suspend fun refreshByUser() {
        if (userRefreshing) return
        userRefreshing = true
        try {
            refresh()
        } finally {
            userRefreshing = false
        }
    }

    private suspend fun load() {
        val phoneBefore = System.currentTimeMillis()
        val nextOverview = withContext(Dispatchers.IO) { boardApi.careOverview() }
        val phoneAfter = System.currentTimeMillis()
        if (nextOverview == null) {
            loadFailed = true
            return
        }
        clockOffsetMillis = nextOverview.boardNowMillis - (phoneBefore + phoneAfter) / 2
        val since = boardNow() - RECENT_RECORD_DAYS * DAY_MILLIS
        val (recent, growth) = withContext(Dispatchers.IO) {
            boardApi.careRecords(sinceMillis = since, limit = 300) to
                boardApi.careRecords(sinceMillis = null, type = CareRecordType.Growth, limit = 30)
        }
        overview = nextOverview
        recent?.let { recentRecords = it }
        growth?.let { growthRecords = it }
        loadFailed = recent == null || growth == null
        if (!loadFailed) lastSyncedMillis = System.currentTimeMillis()
    }

    /** Returns an error message for the parent, or null when the board saved it. */
    suspend fun save(record: CareRecord, isNew: Boolean): String? = write {
        boardApi.saveCareRecord(record, isNew).errorMessage
    }

    suspend fun delete(id: String): String? = write {
        boardApi.deleteCareRecord(id).errorMessage
    }

    suspend fun saveProfile(profile: CareProfile): String? = write {
        boardApi.saveCareProfile(profile).errorMessage
    }

    private suspend fun write(call: () -> String?): String? {
        if (saving) return "上一条还在保存"
        saving = true
        try {
            val error = withContext(Dispatchers.IO) { call() }
            if (error == null) refresh()
            return error
        } finally {
            saving = false
        }
    }
}

private data class CareEditorRequest(
    val type: CareRecordType,
    val existing: CareRecord? = null,
)

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun CarePage(
    boardApi: BoardApi,
    showMessage: (String) -> Unit,
) {
    val state = remember(boardApi) { CarePageState(boardApi) }
    val scope = rememberCoroutineScope()
    var nowMillis by remember { mutableLongStateOf(state.boardNow()) }
    var editor by remember { mutableStateOf<CareEditorRequest?>(null) }
    var editingProfile by remember { mutableStateOf(false) }

    val lifecycleOwner = LocalLifecycleOwner.current
    LaunchedEffect(state, lifecycleOwner) {
        // Stops when the app goes to the background and refreshes at once when it comes back.
        lifecycleOwner.repeatOnLifecycle(Lifecycle.State.STARTED) {
            while (true) {
                state.refresh()
                nowMillis = state.boardNow()
                delay(if (state.loadFailed) CARE_RETRY_REFRESH_MS else CARE_LIVE_REFRESH_MS)
            }
        }
    }
    LaunchedEffect(state) {
        while (true) {
            delay(CARE_CLOCK_TICK_MS)
            nowMillis = state.boardNow()
        }
    }

    fun launchWrite(successMessage: String, call: suspend () -> String?) {
        scope.launch {
            val error = call()
            nowMillis = state.boardNow()
            showMessage(error ?: successMessage)
            if (error == null) editor = null
        }
    }

    val overview = state.overview
    val ongoingSleep = state.recentRecords.firstOrNull { it.ongoing }
        ?: overview?.nap?.takeIf { it.state == NapState.Sleeping && it.sleepId != null }?.let { nap ->
            CareRecord(id = nap.sleepId!!, type = CareRecordType.Sleep, startAtMillis = nap.sleepStartedAtMillis ?: nowMillis)
        }

    fun startSleep() {
        launchWrite("已开始记录睡眠") {
            state.save(CareRecord(type = CareRecordType.Sleep, startAtMillis = state.boardNow()), isNew = true)
        }
    }

    fun endSleep() {
        val sleep = ongoingSleep ?: return
        launchWrite("已记录醒来") {
            state.save(sleep.copy(endAtMillis = state.boardNow()), isNew = false)
        }
    }

    fun refreshNow() {
        scope.launch {
            state.refreshByUser()
            nowMillis = state.boardNow()
        }
    }

    PullToRefreshBox(
        isRefreshing = state.userRefreshing,
        onRefresh = ::refreshNow,
        modifier = Modifier.fillMaxSize(),
    ) {
        PageColumn {
            item {
                CareHeader(
                    overview = overview,
                    lastSyncedMillis = state.lastSyncedMillis,
                    connected = !state.loadFailed,
                    onEditProfile = { editingProfile = true },
                    onRefresh = ::refreshNow,
                )
            }
            if (state.loadFailed && overview == null) {
                item {
                    InfoCard(
                        icon = Icons.Rounded.Refresh,
                        title = "连不上看护设备",
                        body = "记录保存在看护设备上，请确认手机和设备在同一网络。",
                        actionLabel = "重试",
                        onAction = ::refreshNow,
                    )
                }
            }
            if (overview != null && overview.profile.birthDate == null) {
                item {
                    InfoCard(
                        icon = Icons.Rounded.Cake,
                        title = "填写宝宝生日",
                        body = "小睡预测会按月龄给出参考清醒时长，再根据记录逐步贴合宝宝自己的规律。",
                        actionLabel = "去填写",
                        onAction = { editingProfile = true },
                    )
                }
            }
            if (overview != null) {
                item {
                    NapCard(
                        nap = overview.nap,
                        nowMillis = nowMillis,
                        saving = state.saving,
                        onStartSleep = ::startSleep,
                        onEndSleep = ::endSleep,
                        onLogPastSleep = { editor = CareEditorRequest(CareRecordType.Sleep) },
                    )
                }
            }
            item {
                QuickLogRow(onLog = { type ->
                    editor = if (type == CareRecordType.Sleep && ongoingSleep != null) {
                        CareEditorRequest(type, ongoingSleep)
                    } else {
                        CareEditorRequest(type)
                    }
                })
            }
            if (overview != null) {
                item { TodaySummaryCard(overview = overview, nowMillis = nowMillis) }
                item {
                    TodayTimelineCard(
                        records = state.recentRecords,
                        dayStartMillis = overview.dayStartMillis,
                        nowMillis = nowMillis,
                    )
                }
                item { GrowthCard(records = state.growthRecords, onAdd = { editor = CareEditorRequest(CareRecordType.Growth) }) }
            }
            item { SectionHeader(title = "最近记录") }
            if (state.recentRecords.isEmpty()) {
                item {
                    Text(
                        text = if (overview == null) "正在读取…" else "最近 $RECENT_RECORD_DAYS 天还没有记录，点上方按钮开始记录。",
                        style = MaterialTheme.typography.bodyMedium,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
            }
            items(state.recentRecords, key = { it.id }) { record ->
                CareRecordRow(
                    record = record,
                    nowMillis = nowMillis,
                    onClick = { editor = CareEditorRequest(record.type, record) },
                )
            }
        }
    }

    editor?.let { request ->
        CareRecordSheet(
            type = request.type,
            existing = request.existing,
            defaults = state.recentRecords,
            nowMillis = { state.boardNow() },
            saving = state.saving,
            onDismiss = { editor = null },
            onSave = { record, isNew ->
                launchWrite(if (isNew) "已记录" else "已更新") { state.save(record, isNew) }
            },
            onDelete = { id -> launchWrite("已删除") { state.delete(id) } },
        )
    }
    if (editingProfile) {
        CareProfileDialog(
            profile = overview?.profile ?: CareProfile(),
            todayMillis = state.boardNow(),
            onDismiss = { editingProfile = false },
            onSave = { profile ->
                scope.launch {
                    val error = state.saveProfile(profile)
                    showMessage(error ?: "宝宝信息已保存")
                    if (error == null) editingProfile = false
                }
            },
        )
    }
}

// ── header & info ────────────────────────────────────────────

@Composable
private fun CareHeader(
    overview: CareOverview?,
    lastSyncedMillis: Long?,
    connected: Boolean,
    onEditProfile: () -> Unit,
    onRefresh: () -> Unit,
) {
    Column {
        Row(verticalAlignment = Alignment.CenterVertically) {
            PageHeader(title = "记录")
            Spacer(Modifier.weight(1f))
            IconButton(onClick = onRefresh) {
                Icon(Icons.Rounded.Refresh, contentDescription = "刷新")
            }
            IconButton(onClick = onEditProfile) {
                Icon(Icons.Rounded.Edit, contentDescription = "编辑宝宝信息")
            }
        }
        val subtitle = if (overview == null) {
            "正在连接看护设备…"
        } else if (overview.profile.birthDate == null) {
            overview.profile.name ?: "还没有填写宝宝信息"
        } else {
            listOfNotNull(overview.profile.name ?: "宝宝", overview.age?.label).joinToString(" · ")
        }
        Text(
            text = subtitle,
            style = MaterialTheme.typography.bodyLarge,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
        if (lastSyncedMillis != null) {
            SyncStatusRow(lastSyncedMillis = lastSyncedMillis, connected = connected)
        }
    }
}

private val SYNC_TIME_FORMAT: DateTimeFormatter = DateTimeFormatter.ofPattern("HH:mm:ss")

@Composable
private fun SyncStatusRow(lastSyncedMillis: Long, connected: Boolean) {
    val scheme = MaterialTheme.colorScheme
    val time = Instant.ofEpochMilli(lastSyncedMillis).atZone(ZoneId.systemDefault()).format(SYNC_TIME_FORMAT)
    Row(
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(6.dp),
        modifier = Modifier.padding(top = 4.dp),
    ) {
        Box(
            Modifier
                .size(8.dp)
                .clip(CircleShape)
                .background(if (connected) scheme.primary else scheme.error),
        )
        Text(
            text = if (connected) "实时同步 · $time 更新" else "连接中断，正在重连 · 显示 $time 的记录",
            style = MaterialTheme.typography.bodySmall,
            color = if (connected) scheme.onSurfaceVariant else scheme.error,
        )
    }
}

@Composable
private fun InfoCard(
    icon: ImageVector,
    title: String,
    body: String,
    actionLabel: String,
    onAction: () -> Unit,
) {
    PressedCard(contentPadding = PaddingValues(16.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(12.dp)) {
            IconBadge(icon = icon)
            Column(modifier = Modifier.weight(1f)) {
                Text(title, style = MaterialTheme.typography.titleMedium)
                Text(body, style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
        }
        FilledTonalButton(onClick = onAction, modifier = Modifier.align(Alignment.End)) {
            Text(actionLabel)
        }
    }
}

// ── nap prediction ───────────────────────────────────────────

@Composable
private fun NapCard(
    nap: NapPrediction,
    nowMillis: Long,
    saving: Boolean,
    onStartSleep: () -> Unit,
    onEndSleep: () -> Unit,
    onLogPastSleep: () -> Unit,
) {
    val scheme = MaterialTheme.colorScheme
    PressedCard(contentPadding = PaddingValues(18.dp)) {
        when (nap.state) {
            NapState.Sleeping -> {
                val started = nap.sleepStartedAtMillis ?: nowMillis
                NapTitle(icon = Icons.Rounded.Bedtime, title = "宝宝正在睡觉")
                Text(
                    text = "已睡 ${formatDurationMinutes((nowMillis - started) / 60_000.0)}",
                    style = MaterialTheme.typography.headlineMedium,
                    fontWeight = FontWeight.Bold,
                )
                Text("${formatClock(started)} 入睡", style = MaterialTheme.typography.bodyMedium, color = scheme.onSurfaceVariant)
                Button(onClick = onEndSleep, enabled = !saving, modifier = Modifier.fillMaxWidth()) {
                    Icon(Icons.Rounded.WbSunny, contentDescription = null)
                    Spacer(Modifier.width(8.dp))
                    Text("醒了")
                }
            }
            NapState.Awake -> AwakeNapContent(nap, nowMillis, saving, onStartSleep)
            NapState.NeedSleep -> {
                NapTitle(icon = Icons.Rounded.Bedtime, title = "下次小睡")
                Text(
                    "记录一次睡眠后开始预测。宝宝醒着的话，可以补记上一次睡眠。",
                    style = MaterialTheme.typography.bodyMedium,
                    color = scheme.onSurfaceVariant,
                )
                Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                    FilledTonalButton(onClick = onLogPastSleep, enabled = !saving, modifier = Modifier.weight(1f)) {
                        Text("补记睡眠")
                    }
                    Button(onClick = onStartSleep, enabled = !saving, modifier = Modifier.weight(1f)) {
                        Text("开始睡觉")
                    }
                }
            }
            NapState.NeedProfile, NapState.Unknown -> {
                NapTitle(icon = Icons.Rounded.Bedtime, title = "下次小睡")
                Text(
                    "填写宝宝生日后显示预测。",
                    style = MaterialTheme.typography.bodyMedium,
                    color = scheme.onSurfaceVariant,
                )
                Button(onClick = onStartSleep, enabled = !saving, modifier = Modifier.fillMaxWidth()) {
                    Text("开始睡觉")
                }
            }
        }
    }
}

@Composable
private fun AwakeNapContent(
    nap: NapPrediction,
    nowMillis: Long,
    saving: Boolean,
    onStartSleep: () -> Unit,
) {
    val scheme = MaterialTheme.colorScheme
    val lastWake = nap.lastWakeAtMillis ?: nowMillis
    val earliest = nap.earliestAtMillis ?: nowMillis
    val latest = nap.latestAtMillis ?: nowMillis
    val ideal = nap.idealAtMillis ?: nowMillis
    // Recomputed locally so the card keeps moving between board refreshes.
    val timing = when {
        nowMillis < earliest -> NapTiming.Early
        nowMillis <= latest -> NapTiming.Due
        else -> NapTiming.Overdue
    }
    val (statusText, statusColor) = when (timing) {
        NapTiming.Early -> "还早，${formatDurationMinutes((earliest - nowMillis) / 60_000.0)}后进入建议时段" to scheme.onSurfaceVariant
        NapTiming.Due -> "现在是哄睡的好时机" to scheme.primary
        NapTiming.Overdue -> "已超过建议时段，宝宝可能有点累了" to scheme.error
    }
    NapTitle(icon = Icons.Rounded.Bedtime, title = "下次小睡")
    Text(
        text = "${formatClock(earliest)} – ${formatClock(latest)}",
        style = MaterialTheme.typography.headlineMedium,
        fontWeight = FontWeight.Bold,
    )
    Text(
        text = "最佳 ${formatClock(ideal)} · 已清醒 ${formatDurationMinutes((nowMillis - lastWake) / 60_000.0)}",
        style = MaterialTheme.typography.bodyMedium,
        color = scheme.onSurfaceVariant,
    )
    val span = (latest - lastWake).coerceAtLeast(1L)
    LinearProgressIndicator(
        progress = { ((nowMillis - lastWake).toFloat() / span).coerceIn(0f, 1f) },
        modifier = Modifier.fillMaxWidth().padding(vertical = 6.dp),
        color = if (timing == NapTiming.Overdue) scheme.error else scheme.primary,
    )
    Text(statusText, style = MaterialTheme.typography.titleSmall, color = statusColor)
    val basis = when (nap.basis) {
        NapBasis.History, NapBasis.Blend -> "结合宝宝最近 ${nap.samples} 次白天清醒时长"
        else -> nap.ageRangeMin?.let { (low, high) -> "按月龄参考：每次清醒约 $low–$high 分钟" } ?: "按月龄参考"
    }
    val night = if (nap.night) "；夜间以长睡为主，仅供参考" else ""
    Text("$basis$night", style = MaterialTheme.typography.bodySmall, color = scheme.onSurfaceVariant)
    Button(onClick = onStartSleep, enabled = !saving, modifier = Modifier.fillMaxWidth()) {
        Icon(Icons.Rounded.Bedtime, contentDescription = null)
        Spacer(Modifier.width(8.dp))
        Text("开始睡觉")
    }
}

@Composable
private fun NapTitle(icon: ImageVector, title: String) {
    Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(10.dp)) {
        IconBadge(icon = icon)
        Text(title, style = MaterialTheme.typography.titleMedium)
    }
}

// ── quick log ────────────────────────────────────────────────

@Composable
private fun QuickLogRow(onLog: (CareRecordType) -> Unit) {
    Row(horizontalArrangement = Arrangement.spacedBy(10.dp), modifier = Modifier.fillMaxWidth()) {
        CareRecordType.entries.forEach { type ->
            MiniActionButton(
                label = type.label,
                icon = type.icon(),
                selected = false,
                onClick = { onLog(type) },
                modifier = Modifier.weight(1f),
            )
        }
    }
}

internal fun CareRecordType.icon(): ImageVector = when (this) {
    CareRecordType.Feeding -> Icons.Rounded.LocalDrink
    CareRecordType.Diaper -> Icons.Rounded.BabyChangingStation
    CareRecordType.Sleep -> Icons.Rounded.Bedtime
    CareRecordType.Growth -> Icons.Rounded.MonitorWeight
}

private fun CareRecord.icon(): ImageVector =
    if (type == CareRecordType.Feeding && feedingKind == FeedingKind.Solid) Icons.Rounded.Restaurant else type.icon()

// ── today ────────────────────────────────────────────────────

@Composable
private fun TodaySummaryCard(overview: CareOverview, nowMillis: Long) {
    val today = overview.today
    val lastFeeding = overview.lastFeeding
    PressedCard(contentPadding = PaddingValues(16.dp)) {
        Text("今天", style = MaterialTheme.typography.titleMedium)
        Row(horizontalArrangement = Arrangement.spacedBy(12.dp), modifier = Modifier.fillMaxWidth()) {
            SummaryBlock(
                label = "喂养",
                value = "${today.feedingCount} 次",
                detail = listOfNotNull(
                    today.bottleMl.takeIf { it > 0 }?.let { "奶量 $it ml" },
                    today.breastMin.takeIf { it > 0 }?.let { "亲喂 $it 分" },
                    today.solidCount.takeIf { it > 0 }?.let { "辅食 $it 次" },
                ).joinToString(" · ").ifEmpty { "—" },
                modifier = Modifier.weight(1f),
            )
            SummaryBlock(
                label = "尿布",
                value = "${today.diaperCount} 次",
                detail = "嘘嘘 ${today.wetCount} · 便便 ${today.dirtyCount}",
                modifier = Modifier.weight(1f),
            )
        }
        Row(horizontalArrangement = Arrangement.spacedBy(12.dp), modifier = Modifier.fillMaxWidth()) {
            SummaryBlock(
                label = "睡眠",
                value = formatDurationMinutes(today.sleepTotalMin),
                detail = if (today.sleepCount == 0) "—" else "${today.sleepCount} 段 · 最长 ${formatDurationMinutes(today.sleepLongestMin)}",
                modifier = Modifier.weight(1f),
            )
            SummaryBlock(
                label = "距上次喂养",
                value = lastFeeding?.let { formatDurationMinutes((nowMillis - it.startAtMillis) / 60_000.0) } ?: "—",
                detail = lastFeeding?.let { "${formatClock(it.startAtMillis)} ${it.title()}" } ?: "还没有记录",
                modifier = Modifier.weight(1f),
            )
        }
    }
}

@Composable
private fun SummaryBlock(label: String, value: String, detail: String, modifier: Modifier = Modifier) {
    Column(
        modifier = modifier
            .clip(ControlShape)
            .background(MaterialTheme.colorScheme.surfaceVariant.copy(alpha = 0.55f))
            .padding(horizontal = 12.dp, vertical = 10.dp),
        verticalArrangement = Arrangement.spacedBy(2.dp),
    ) {
        Text(label, style = MaterialTheme.typography.labelLarge, color = MaterialTheme.colorScheme.onSurfaceVariant)
        Text(value, style = MaterialTheme.typography.titleLarge, fontWeight = FontWeight.Bold, maxLines = 1)
        Text(detail, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant, maxLines = 2)
    }
}

private val TimelineLaneTypes = listOf(CareRecordType.Feeding, CareRecordType.Diaper, CareRecordType.Sleep)

@Composable
private fun TodayTimelineCard(records: List<CareRecord>, dayStartMillis: Long, nowMillis: Long) {
    val scheme = MaterialTheme.colorScheme
    val laneColors = mapOf(
        CareRecordType.Feeding to scheme.tertiary,
        CareRecordType.Diaper to scheme.secondary,
        CareRecordType.Sleep to scheme.primary,
    )
    val textMeasurer = rememberTextMeasurer()
    val dayEnd = dayStartMillis + DAY_MILLIS
    val todays = records.filter { record ->
        val end = if (record.type == CareRecordType.Sleep) record.endAtMillis ?: nowMillis else record.startAtMillis
        end >= dayStartMillis && record.startAtMillis < dayEnd
    }
    PressedCard(contentPadding = PaddingValues(16.dp)) {
        Text("今日时间线", style = MaterialTheme.typography.titleMedium)
        Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
            TimelineLaneTypes.forEach { type ->
                Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(5.dp)) {
                    Box(Modifier.size(8.dp).clip(CircleShape).background(laneColors.getValue(type)))
                    Text(type.label, style = MaterialTheme.typography.labelSmall, color = scheme.onSurfaceVariant)
                }
            }
        }
        val axisColor = scheme.onSurfaceVariant.copy(alpha = 0.6f)
        val gridColor = scheme.outline.copy(alpha = 0.12f)
        val nowColor = scheme.error
        Canvas(modifier = Modifier.fillMaxWidth().height(120.dp)) {
            if (dayStartMillis <= 0L) return@Canvas
            val left = 4.dp.toPx()
            val right = size.width - 4.dp.toPx()
            val axisHeight = 18.dp.toPx()
            val laneHeight = (size.height - axisHeight) / TimelineLaneTypes.size
            fun x(millis: Long): Float =
                left + ((millis - dayStartMillis).toFloat() / DAY_MILLIS).coerceIn(0f, 1f) * (right - left)

            for (lane in 0..TimelineLaneTypes.size) {
                drawLine(gridColor, Offset(left, lane * laneHeight), Offset(right, lane * laneHeight), strokeWidth = 1f)
            }
            for (hour in 0..24 step 6) {
                val hx = x(dayStartMillis + hour * 3_600_000L)
                drawLine(gridColor, Offset(hx, 0f), Offset(hx, laneHeight * TimelineLaneTypes.size), strokeWidth = 1f)
                val label = textMeasurer.measure("%02d".format(hour), TextStyle(fontSize = 9.sp, color = axisColor))
                drawText(label, topLeft = Offset((hx - label.size.width / 2f).coerceIn(0f, size.width - label.size.width), size.height - label.size.height))
            }
            todays.forEach { record ->
                val lane = TimelineLaneTypes.indexOf(record.type).takeIf { it >= 0 } ?: return@forEach
                val color = laneColors.getValue(record.type)
                val top = lane * laneHeight + 4.dp.toPx()
                val height = laneHeight - 8.dp.toPx()
                if (record.type == CareRecordType.Sleep) {
                    val x1 = x(record.startAtMillis)
                    val x2 = x(record.endAtMillis ?: nowMillis)
                    drawRoundRect(
                        color = color.copy(alpha = 0.45f),
                        topLeft = Offset(x1, top),
                        size = Size((x2 - x1).coerceAtLeast(3.dp.toPx()), height),
                        cornerRadius = CornerRadius(4.dp.toPx()),
                    )
                } else {
                    val width = 4.dp.toPx()
                    drawRoundRect(
                        color = color,
                        topLeft = Offset(x(record.startAtMillis) - width / 2f, top),
                        size = Size(width, height),
                        cornerRadius = CornerRadius(2.dp.toPx()),
                    )
                }
            }
            if (nowMillis in dayStartMillis until dayStartMillis + DAY_MILLIS) {
                val nx = x(nowMillis)
                drawLine(nowColor, Offset(nx, 0f), Offset(nx, laneHeight * TimelineLaneTypes.size), strokeWidth = 2f)
            }
        }
    }
}

// ── growth ───────────────────────────────────────────────────

@Composable
private fun GrowthCard(records: List<CareRecord>, onAdd: () -> Unit) {
    val scheme = MaterialTheme.colorScheme
    val latest = records.firstOrNull()
    PressedCard(contentPadding = PaddingValues(16.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text("生长", style = MaterialTheme.typography.titleMedium, modifier = Modifier.weight(1f))
            FilledTonalButton(onClick = onAdd) { Text("记录") }
        }
        if (latest == null) {
            Text("还没有体重、身长或头围记录。", style = MaterialTheme.typography.bodyMedium, color = scheme.onSurfaceVariant)
            return@PressedCard
        }
        Row(horizontalArrangement = Arrangement.spacedBy(12.dp), modifier = Modifier.fillMaxWidth()) {
            GrowthValue("体重", latestValue(records) { it.weightKg }, "kg", Modifier.weight(1f))
            GrowthValue("身长", latestValue(records) { it.heightCm }, "cm", Modifier.weight(1f))
            GrowthValue("头围", latestValue(records) { it.headCm }, "cm", Modifier.weight(1f))
        }
        val weights = records.filter { it.weightKg != null }.sortedBy { it.startAtMillis }
        if (weights.size >= 2) {
            val first = weights.first()
            val last = weights.last()
            val gain = (last.weightKg ?: 0.0) - (first.weightKg ?: 0.0)
            Text(
                "体重 ${formatDay(first.startAtMillis)} 至 ${formatDay(last.startAtMillis)} ${if (gain >= 0) "增加" else "减少"} ${formatNumber(kotlin.math.abs(gain))} kg",
                style = MaterialTheme.typography.bodySmall,
                color = scheme.onSurfaceVariant,
            )
            WeightSparkline(weights, color = scheme.primary)
        } else {
            Text("最近测量：${formatDay(latest.startAtMillis)}", style = MaterialTheme.typography.bodySmall, color = scheme.onSurfaceVariant)
        }
    }
}

private fun latestValue(records: List<CareRecord>, pick: (CareRecord) -> Double?): Double? =
    records.firstNotNullOfOrNull(pick)

@Composable
private fun GrowthValue(label: String, value: Double?, unit: String, modifier: Modifier = Modifier) {
    Column(modifier = modifier) {
        Text(label, style = MaterialTheme.typography.labelLarge, color = MaterialTheme.colorScheme.onSurfaceVariant)
        Text(
            text = value?.let { "${formatNumber(it)} $unit" } ?: "—",
            style = MaterialTheme.typography.titleMedium,
            fontWeight = FontWeight.Bold,
        )
    }
}

@Composable
private fun WeightSparkline(weights: List<CareRecord>, color: Color) {
    Canvas(modifier = Modifier.fillMaxWidth().height(56.dp).padding(vertical = 4.dp)) {
        val values = weights.mapNotNull { it.weightKg }
        val minValue = values.min()
        val maxValue = values.max()
        val firstAt = weights.first().startAtMillis
        val spanAt = (weights.last().startAtMillis - firstAt).coerceAtLeast(1L)
        val spanValue = (maxValue - minValue).takeIf { it > 0.0 } ?: 1.0
        val path = Path()
        weights.forEachIndexed { index, record ->
            val px = ((record.startAtMillis - firstAt).toFloat() / spanAt) * size.width
            val py = size.height - (((record.weightKg ?: minValue) - minValue) / spanValue).toFloat() * size.height
            if (index == 0) path.moveTo(px, py) else path.lineTo(px, py)
            drawCircle(color, radius = 3.dp.toPx(), center = Offset(px, py))
        }
        drawPath(path, color = color, style = Stroke(width = 2.dp.toPx()))
    }
}

// ── records list ─────────────────────────────────────────────

@Composable
private fun CareRecordRow(record: CareRecord, nowMillis: Long, onClick: () -> Unit) {
    PressedCard(onClick = onClick, contentPadding = PaddingValues(horizontal = 14.dp, vertical = 12.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(12.dp)) {
            IconBadge(icon = record.icon())
            Column(modifier = Modifier.weight(1f)) {
                Text(record.title(), style = MaterialTheme.typography.titleMedium)
                val detail = record.detail(nowMillis)
                if (detail.isNotBlank()) {
                    Text(detail, style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
            }
            Text(
                formatDayAndClock(record.startAtMillis, nowMillis),
                style = MaterialTheme.typography.labelMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
    }
}

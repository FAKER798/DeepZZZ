package com.naviga.app

import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.rounded.Add
import androidx.compose.material.icons.rounded.Remove
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.DatePicker
import androidx.compose.material3.DatePickerDialog
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FilledTonalIconButton
import androidx.compose.material3.FilterChip
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.SegmentedButton
import androidx.compose.material3.SegmentedButtonDefaults
import androidx.compose.material3.SelectableDates
import androidx.compose.material3.SingleChoiceSegmentedButtonRow
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TimePicker
import androidx.compose.material3.rememberDatePickerState
import androidx.compose.material3.rememberModalBottomSheetState
import androidx.compose.material3.rememberTimePickerState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableLongStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import java.time.Instant
import java.time.LocalDate
import java.time.ZoneId
import java.time.ZoneOffset

private const val MINUTE_MILLIS = 60_000L
private const val MAX_SLEEP_MILLIS = 24 * 60 * MINUTE_MILLIS
private const val CLOCK_SLACK_MILLIS = MINUTE_MILLIS

/** Bottom sheet that adds a new record of [type] or edits [existing]. */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
internal fun CareRecordSheet(
    type: CareRecordType,
    existing: CareRecord?,
    defaults: List<CareRecord>,
    nowMillis: () -> Long,
    saving: Boolean,
    onDismiss: () -> Unit,
    onSave: (CareRecord, Boolean) -> Unit,
    onDelete: (String) -> Unit,
) {
    val isNew = existing == null
    // A new record keeps one id while the sheet is open, so a double tap or a retried
    // request lands on the board once.
    val recordId = remember { existing?.id ?: newCareRecordId() }
    val openedAt = remember { nowMillis() }
    val lastFeeding = remember { defaults.firstOrNull { it.type == CareRecordType.Feeding } }
    val lastBottle = remember { defaults.firstOrNull { it.feedingKind == FeedingKind.Bottle } }

    var startAt by remember { mutableLongStateOf(existing?.startAtMillis ?: openedAt) }
    var stillAsleep by remember { mutableStateOf(existing?.ongoing ?: true) }
    var endAt by remember { mutableLongStateOf(existing?.endAtMillis ?: openedAt) }
    var feedingKind by remember {
        mutableStateOf(existing?.feedingKind ?: lastFeeding?.feedingKind ?: FeedingKind.Bottle)
    }
    var side by remember { mutableStateOf(existing?.side ?: BreastSide.Left) }
    var durationMin by remember { mutableIntStateOf(existing?.durationMin ?: 15) }
    var amountMl by remember { mutableIntStateOf(existing?.amountMl ?: lastBottle?.amountMl ?: 90) }
    var milk by remember { mutableStateOf(existing?.milk ?: lastBottle?.milk) }
    var food by remember { mutableStateOf(existing?.food.orEmpty()) }
    var diaperKind by remember { mutableStateOf(existing?.diaperKind ?: DiaperKind.Wet) }
    var weight by remember { mutableStateOf(existing?.weightKg?.let(::formatNumber).orEmpty()) }
    var height by remember { mutableStateOf(existing?.heightCm?.let(::formatNumber).orEmpty()) }
    var head by remember { mutableStateOf(existing?.headCm?.let(::formatNumber).orEmpty()) }
    var note by remember { mutableStateOf(existing?.note.orEmpty()) }
    var error by remember { mutableStateOf<String?>(null) }
    var confirmDelete by remember { mutableStateOf(false) }

    fun buildRecord(): CareRecord? {
        val now = nowMillis()
        if (startAt > now + CLOCK_SLACK_MILLIS) {
            error = "时间不能晚于现在"
            return null
        }
        val base = CareRecord(id = recordId, type = type, startAtMillis = startAt, note = note.trim().ifEmpty { null })
        return when (type) {
            CareRecordType.Feeding -> when (feedingKind) {
                FeedingKind.Breast -> base.copy(feedingKind = feedingKind, side = side, durationMin = durationMin)
                FeedingKind.Bottle -> base.copy(feedingKind = feedingKind, amountMl = amountMl, milk = milk)
                FeedingKind.Solid -> base.copy(feedingKind = feedingKind, food = food.trim().ifEmpty { null })
            }
            CareRecordType.Diaper -> base.copy(diaperKind = diaperKind)
            CareRecordType.Sleep -> {
                if (stillAsleep) {
                    base.copy(endAtMillis = null)
                } else {
                    error = when {
                        endAt <= startAt -> "醒来时间要晚于入睡时间"
                        endAt > now + CLOCK_SLACK_MILLIS -> "醒来时间不能晚于现在"
                        endAt - startAt > MAX_SLEEP_MILLIS -> "一段睡眠不能超过 24 小时"
                        else -> null
                    }
                    if (error != null) return null
                    base.copy(endAtMillis = endAt)
                }
            }
            CareRecordType.Growth -> {
                val values = listOf(
                    parseMeasurement(weight, 0.5, 40.0, "体重"),
                    parseMeasurement(height, 30.0, 150.0, "身长"),
                    parseMeasurement(head, 20.0, 70.0, "头围"),
                )
                values.firstNotNullOfOrNull { it.error }?.let {
                    error = it
                    return null
                }
                if (values.all { it.value == null }) {
                    error = "至少填写体重、身长、头围中的一项"
                    return null
                }
                base.copy(weightKg = values[0].value, heightCm = values[1].value, headCm = values[2].value)
            }
        }
    }

    ModalBottomSheet(
        onDismissRequest = onDismiss,
        sheetState = rememberModalBottomSheetState(skipPartiallyExpanded = true),
    ) {
        Column(
            modifier = Modifier
                .fillMaxWidth()
                .verticalScroll(rememberScrollState())
                .padding(horizontal = 20.dp)
                .padding(bottom = 24.dp),
            verticalArrangement = Arrangement.spacedBy(14.dp),
        ) {
            Text(
                text = (if (isNew) "记录" else "编辑") + type.label,
                style = MaterialTheme.typography.headlineSmall,
            )
            when (type) {
                CareRecordType.Feeding -> {
                    SingleChoiceSegmentedButtonRow(modifier = Modifier.fillMaxWidth()) {
                        FeedingKind.entries.forEachIndexed { index, kind ->
                            SegmentedButton(
                                selected = feedingKind == kind,
                                onClick = { feedingKind = kind },
                                shape = SegmentedButtonDefaults.itemShape(index = index, count = FeedingKind.entries.size),
                            ) { Text(kind.label) }
                        }
                    }
                    when (feedingKind) {
                        FeedingKind.Breast -> {
                            ChoiceChips(BreastSide.entries, side, { it.label }) { picked -> picked?.let { side = it } }
                            Stepper("时长", durationMin, "分钟", step = 5, range = 0..120) { durationMin = it }
                        }
                        FeedingKind.Bottle -> {
                            Stepper("奶量", amountMl, "ml", step = 10, range = 0..300) { amountMl = it }
                            ChoiceChips(MilkType.entries, milk, { it.label }, allowNone = true) { milk = it }
                        }
                        FeedingKind.Solid -> OutlinedTextField(
                            value = food,
                            onValueChange = { food = it.take(40) },
                            label = { Text("吃了什么") },
                            singleLine = true,
                            modifier = Modifier.fillMaxWidth(),
                        )
                    }
                    TimeField("开始时间", startAt, { startAt = it }, nowMillis)
                }
                CareRecordType.Diaper -> {
                    ChoiceChips(DiaperKind.entries, diaperKind, { it.label }) { picked -> picked?.let { diaperKind = it } }
                    TimeField("时间", startAt, { startAt = it }, nowMillis)
                }
                CareRecordType.Sleep -> {
                    TimeField("入睡时间", startAt, { startAt = it }, nowMillis)
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Text("还在睡", style = MaterialTheme.typography.titleMedium, modifier = Modifier.weight(1f))
                        Switch(
                            checked = stillAsleep,
                            onCheckedChange = { asleep ->
                                stillAsleep = asleep
                                if (!asleep) {
                                    val now = nowMillis()
                                    endAt = now
                                    // Starting "now" and waking "now" would be an empty sleep; suggest one hour.
                                    if (startAt > now - 10 * MINUTE_MILLIS) startAt = now - 60 * MINUTE_MILLIS
                                }
                            },
                        )
                    }
                    if (!stillAsleep) {
                        TimeField("醒来时间", endAt, { endAt = it }, nowMillis)
                    }
                }
                CareRecordType.Growth -> {
                    MeasurementField("体重", weight, "kg") { weight = it }
                    MeasurementField("身长", height, "cm") { height = it }
                    MeasurementField("头围", head, "cm") { head = it }
                    TimeField("测量时间", startAt, { startAt = it }, nowMillis, quickOffsets = false)
                }
            }
            OutlinedTextField(
                value = note,
                onValueChange = { note = it.take(200) },
                label = { Text("备注（可选）") },
                modifier = Modifier.fillMaxWidth(),
            )
            error?.let {
                Text(it, color = MaterialTheme.colorScheme.error, style = MaterialTheme.typography.bodyMedium)
            }
            Row(verticalAlignment = Alignment.CenterVertically) {
                if (!isNew) {
                    TextButton(onClick = { confirmDelete = true }, enabled = !saving) {
                        Text("删除", color = MaterialTheme.colorScheme.error)
                    }
                }
                Spacer(Modifier.weight(1f))
                TextButton(onClick = onDismiss) { Text("取消") }
                Spacer(Modifier.width(8.dp))
                Button(
                    onClick = {
                        error = null
                        buildRecord()?.let { onSave(it, isNew) }
                    },
                    enabled = !saving,
                ) { Text(if (saving) "保存中…" else "保存") }
            }
        }
    }

    if (confirmDelete && existing != null) {
        AlertDialog(
            onDismissRequest = { confirmDelete = false },
            title = { Text("删除这条记录？") },
            text = { Text("${existing.title()} · ${formatDayAndClock(existing.startAtMillis, nowMillis())}") },
            confirmButton = {
                TextButton(onClick = {
                    confirmDelete = false
                    onDelete(existing.id)
                }) { Text("删除", color = MaterialTheme.colorScheme.error) }
            },
            dismissButton = { TextButton(onClick = { confirmDelete = false }) { Text("取消") } },
        )
    }
}

/** Baby name, birthday and sex; the birthday drives the age-based nap ranges. */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
internal fun CareProfileDialog(
    profile: CareProfile,
    todayMillis: Long,
    onDismiss: () -> Unit,
    onSave: (CareProfile) -> Unit,
) {
    var name by remember { mutableStateOf(profile.name.orEmpty()) }
    var birthDate by remember { mutableStateOf(profile.birthDate) }
    var sex by remember { mutableStateOf(profile.sex) }
    var pickDate by remember { mutableStateOf(false) }
    val today = remember(todayMillis) { Instant.ofEpochMilli(todayMillis).atZone(ZoneId.systemDefault()).toLocalDate() }

    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("宝宝信息") },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
                OutlinedTextField(
                    value = name,
                    onValueChange = { name = it.take(20) },
                    label = { Text("小名") },
                    singleLine = true,
                    modifier = Modifier.fillMaxWidth(),
                )
                OutlinedButton(onClick = { pickDate = true }, modifier = Modifier.fillMaxWidth()) {
                    Text(birthDate?.let { "生日 ${it.year}年${it.monthValue}月${it.dayOfMonth}日" } ?: "选择生日")
                }
                ChoiceChips(BabySex.entries, sex, { it.label }, allowNone = true) { sex = it }
            }
        },
        confirmButton = {
            TextButton(onClick = { onSave(CareProfile(name.trim().ifEmpty { null }, birthDate, sex)) }) { Text("保存") }
        },
        dismissButton = { TextButton(onClick = onDismiss) { Text("取消") } },
    )

    if (pickDate) {
        val state = rememberDatePickerState(
            initialSelectedDateMillis = (birthDate ?: today).toUtcMillis(),
            selectableDates = notAfter(today),
        )
        DatePickerDialog(
            onDismissRequest = { pickDate = false },
            confirmButton = {
                TextButton(onClick = {
                    state.selectedDateMillis?.let { birthDate = utcMillisToLocalDate(it) }
                    pickDate = false
                }) { Text("确定") }
            },
            dismissButton = { TextButton(onClick = { pickDate = false }) { Text("取消") } },
        ) {
            DatePicker(state = state)
        }
    }
}

// ── fields ───────────────────────────────────────────────────

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun TimeField(
    label: String,
    millis: Long,
    onChange: (Long) -> Unit,
    nowMillis: () -> Long,
    quickOffsets: Boolean = true,
) {
    var pickDate by remember { mutableStateOf(false) }
    var pickTime by remember { mutableStateOf(false) }
    val zone = ZoneId.systemDefault()
    val dateTime = Instant.ofEpochMilli(millis).atZone(zone)
    val now = nowMillis()

    Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
        Text(label, style = MaterialTheme.typography.labelLarge, color = MaterialTheme.colorScheme.onSurfaceVariant)
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            OutlinedButton(onClick = { pickDate = true }) {
                Text(formatDayAndClock(millis, now).substringBefore(' '))
            }
            OutlinedButton(onClick = { pickTime = true }) { Text(formatClock(millis)) }
        }
        if (quickOffsets) {
            Row(
                modifier = Modifier.horizontalScroll(rememberScrollState()),
                horizontalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                listOf(0 to "现在", 15 to "15 分钟前", 30 to "30 分钟前", 60 to "1 小时前").forEach { (minutes, text) ->
                    FilterChip(
                        selected = false,
                        onClick = { onChange(nowMillis() - minutes * MINUTE_MILLIS) },
                        label = { Text(text) },
                    )
                }
            }
        }
    }

    if (pickDate) {
        val today = Instant.ofEpochMilli(now).atZone(zone).toLocalDate()
        val state = rememberDatePickerState(
            initialSelectedDateMillis = dateTime.toLocalDate().toUtcMillis(),
            selectableDates = notAfter(today),
        )
        DatePickerDialog(
            onDismissRequest = { pickDate = false },
            confirmButton = {
                TextButton(onClick = {
                    state.selectedDateMillis?.let { utc ->
                        onChange(dateTime.with(utcMillisToLocalDate(utc)).toInstant().toEpochMilli())
                    }
                    pickDate = false
                }) { Text("确定") }
            },
            dismissButton = { TextButton(onClick = { pickDate = false }) { Text("取消") } },
        ) {
            DatePicker(state = state)
        }
    }
    if (pickTime) {
        val state = rememberTimePickerState(
            initialHour = dateTime.hour,
            initialMinute = dateTime.minute,
            is24Hour = true,
        )
        AlertDialog(
            onDismissRequest = { pickTime = false },
            confirmButton = {
                TextButton(onClick = {
                    val picked = dateTime.withHour(state.hour).withMinute(state.minute).withSecond(0).withNano(0)
                    onChange(picked.toInstant().toEpochMilli())
                    pickTime = false
                }) { Text("确定") }
            },
            dismissButton = { TextButton(onClick = { pickTime = false }) { Text("取消") } },
            text = { TimePicker(state = state) },
        )
    }
}

@Composable
private fun <T> ChoiceChips(
    options: List<T>,
    selected: T?,
    label: (T) -> String,
    allowNone: Boolean = false,
    onSelect: (T?) -> Unit,
) {
    Row(
        modifier = Modifier.horizontalScroll(rememberScrollState()),
        horizontalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        options.forEach { option ->
            FilterChip(
                selected = option == selected,
                onClick = { onSelect(if (allowNone && option == selected) null else option) },
                label = { Text(label(option)) },
            )
        }
    }
}

@Composable
private fun Stepper(
    label: String,
    value: Int,
    unit: String,
    step: Int,
    range: IntRange,
    onChange: (Int) -> Unit,
) {
    Row(verticalAlignment = Alignment.CenterVertically) {
        Text(label, style = MaterialTheme.typography.titleMedium, modifier = Modifier.weight(1f))
        FilledTonalIconButton(onClick = { onChange((value - step).coerceIn(range)) }) {
            Icon(Icons.Rounded.Remove, contentDescription = "减少$label")
        }
        Text(
            text = "$value $unit",
            style = MaterialTheme.typography.titleMedium,
            textAlign = TextAlign.Center,
            modifier = Modifier.width(96.dp),
        )
        FilledTonalIconButton(onClick = { onChange((value + step).coerceIn(range)) }) {
            Icon(Icons.Rounded.Add, contentDescription = "增加$label")
        }
    }
}

@Composable
private fun MeasurementField(label: String, value: String, unit: String, onChange: (String) -> Unit) {
    OutlinedTextField(
        value = value,
        onValueChange = { text -> onChange(text.filter { it.isDigit() || it == '.' || it == ',' }.take(6)) },
        label = { Text(label) },
        suffix = { Text(unit) },
        singleLine = true,
        keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Decimal),
        modifier = Modifier.fillMaxWidth(),
    )
}

private data class Measurement(val value: Double?, val error: String?)

private fun parseMeasurement(text: String, low: Double, high: Double, label: String): Measurement {
    val trimmed = text.trim().replace(',', '.')
    if (trimmed.isEmpty()) return Measurement(null, null)
    val number = trimmed.toDoubleOrNull() ?: return Measurement(null, "${label}不是有效数字")
    if (number < low || number > high) {
        return Measurement(null, "${label}应在 ${formatNumber(low)}–${formatNumber(high)} 之间")
    }
    return Measurement(number, null)
}

// Material date pickers work in UTC midnights; convert at the edges.
private fun LocalDate.toUtcMillis(): Long = atStartOfDay(ZoneOffset.UTC).toInstant().toEpochMilli()

private fun utcMillisToLocalDate(millis: Long): LocalDate = Instant.ofEpochMilli(millis).atZone(ZoneOffset.UTC).toLocalDate()

@OptIn(ExperimentalMaterial3Api::class)
private fun notAfter(day: LocalDate): SelectableDates {
    val limit = day.toUtcMillis()
    return object : SelectableDates {
        override fun isSelectableDate(utcTimeMillis: Long): Boolean = utcTimeMillis <= limit

        override fun isSelectableYear(year: Int): Boolean = year <= day.year
    }
}

package com.naviga.app

import java.io.InputStream
import java.net.URLEncoder
import org.json.JSONArray
import org.json.JSONObject

data class BoardPhoto(
    val id: String,
    val capturedAtMillis: Long,
    val path: String,
)

data class BoardLullabyTrack(
    val id: String,
    val title: String,
    val durationMillis: Long?,
    val sizeBytes: Long,
    val updatedAtMillis: Long,
)

data class BoardLullabyStatus(
    val tracks: List<BoardLullabyTrack>,
    val currentTrack: BoardLullabyTrack?,
    val playing: Boolean,
    val paused: Boolean,
    val positionMillis: Long,
    val repeatOne: Boolean,
    val volume: Float?,
)

data class BoardLullabyUploadResult(
    val track: BoardLullabyTrack?,
    val playing: Boolean,
)

data class BoardActivityZone(
    val zone: SafetyZone?,
    val mode: ActivityZoneMode,
)

interface BoardApi {
    fun health(): Boolean

    fun environment(): EnvironmentReading?

    fun snapshotUrl(): String

    fun snapshotStream(): InputStream?

    fun playTalkAudio(wavBytes: ByteArray): Boolean

    fun playMusicAudio(wavBytes: ByteArray, volume: Float): Boolean

    fun uploadLullaby(wavBytes: ByteArray, title: String, volume: Float): BoardLullabyUploadResult?

    fun playStoredLullaby(
        trackId: String?,
        volume: Float,
        positionMillis: Long? = null,
        repeatOne: Boolean = true,
    ): Boolean

    fun pauseLullaby(): Boolean

    fun seekLullaby(positionMillis: Long): Boolean

    fun lullabyStatus(): BoardLullabyStatus?

    fun stopMusicAudio(): Boolean

    fun setMusicVolume(volume: Float): Boolean

    fun startTalkAudio(): Boolean

    fun sendTalkPcm(pcmBytes: ByteArray): Boolean

    fun stopTalkAudio(): Boolean

    fun liveAudioStream(): InputStream?

    fun alerts(settings: AlertSettingsSnapshot): List<BoardAlert>

    fun activityZone(): BoardActivityZone?

    fun setActivityZone(zone: SafetyZone, mode: ActivityZoneMode): Boolean

    fun clearActivityZone(): Boolean

    fun photos(): List<BoardPhoto>

    fun photoStream(photo: BoardPhoto): InputStream?

    fun careOverview(): CareOverview?

    fun careRecords(sinceMillis: Long?, type: CareRecordType? = null, limit: Int = 200): List<CareRecord>?

    /** Creates or updates a record. The record id is sent along, so a retried create never duplicates. */
    fun saveCareRecord(record: CareRecord, isNew: Boolean): CareCallResult<CareRecord>

    fun deleteCareRecord(id: String): CareCallResult<Unit>

    fun saveCareProfile(profile: CareProfile): CareCallResult<CareProfile>

    fun assistantStatus(): AssistantStatus?

    /**
     * Asks the board's local LLM and blocks until the answer ends, reporting each [AssistantEvent]
     * as it arrives. Always finishes with [AssistantEvent.Done] or [AssistantEvent.Failed] unless
     * [cancel] was triggered.
     */
    fun askAssistant(
        history: List<AssistantTurn>,
        cancel: AssistantCancelSignal,
        onEvent: (AssistantEvent) -> Unit,
    )
}

class LanBoardApi(
    private val client: BoardRequestClient,
) : BoardApi {
    private val alertStateLock = Any()
    private val activeAlertEvents = mutableMapOf<BoardAlertType, BoardAlert>()

    override fun health(): Boolean {
        return runCatching {
            val response = client.get("/api/healthz")
            response.ok && JSONObject(response.body).optBoolean("ok", false)
        }.getOrDefault(false)
    }

    override fun environment(): EnvironmentReading? {
        return runCatching {
            val response = client.get("/api/status")
            if (!response.ok) return@runCatching null
            val json = JSONObject(response.body)
            if (!json.optBoolean("ok", false)) return@runCatching null
            val yamnet = json.optJSONObject("yamnet")
            val temperatureHumidity = json.optJSONObject("temperature_humidity")
                ?: return@runCatching null
            EnvironmentReading(
                soundDecibels = yamnet?.optDouble("noise_db", 0.0)?.toFloat() ?: 0.0f,
                temperatureCelsius = temperatureHumidity.optDouble("temperature_c", 0.0).toFloat(),
                humidityPercent = temperatureHumidity.optDouble("humidity_percent", 0.0).toFloat(),
            )
        }.getOrNull()
    }

    override fun snapshotUrl(): String {
        return client.url("/api/preview-snapshot")
    }

    override fun snapshotStream(): InputStream? {
        return runCatching { client.getStream("/api/preview-snapshot") }.getOrNull()
    }

    override fun playTalkAudio(wavBytes: ByteArray): Boolean {
        return runCatching {
            val response = client.post("/api/audio/playback/upload", wavBytes, "audio/wav")
            response.ok && JSONObject(response.body).optBoolean("ok", false)
        }.getOrDefault(false)
    }

    override fun playMusicAudio(wavBytes: ByteArray, volume: Float): Boolean {
        val clampedVolume = volume.coerceIn(0f, 1f)
        return runCatching {
            val response = client.post(
                "/api/audio/playback/upload?volume=$clampedVolume&loop=1",
                wavBytes,
                "audio/wav",
                MUSIC_UPLOAD_TIMEOUT_MS,
            )
            response.ok && JSONObject(response.body).optBoolean("ok", false)
        }.getOrDefault(false)
    }

    override fun uploadLullaby(wavBytes: ByteArray, title: String, volume: Float): BoardLullabyUploadResult? {
        val clampedVolume = volume.coerceIn(0f, 1f)
        val encodedTitle = URLEncoder.encode(title, "UTF-8")
        return runCatching {
            val response = client.post(
                "/api/lullabies/upload?volume=$clampedVolume&title=$encodedTitle",
                wavBytes,
                "audio/wav",
                MUSIC_UPLOAD_TIMEOUT_MS,
            )
            if (!response.ok) return@runCatching null
            val json = JSONObject(response.body)
            if (!json.optBoolean("ok", false)) return@runCatching null
            val result = json.optJSONObject("result") ?: return@runCatching null
            BoardLullabyUploadResult(
                track = result.optJSONObject("track")?.toLullabyTrack(),
                playing = result.optJSONObject("playback")?.optString("status") == "playing",
            )
        }.getOrNull()
    }

    override fun playStoredLullaby(
        trackId: String?,
        volume: Float,
        positionMillis: Long?,
        repeatOne: Boolean,
    ): Boolean {
        val clampedVolume = volume.coerceIn(0f, 1f)
        return runCatching {
            val body = JSONObject().put("volume", clampedVolume)
            if (!trackId.isNullOrBlank()) body.put("track_id", trackId)
            if (positionMillis != null) body.put("position_ms", positionMillis.coerceAtLeast(0L))
            body.put("loop", repeatOne)
            val response = client.post(
                "/api/lullabies/play",
                body.toString().toByteArray(Charsets.UTF_8),
                "application/json; charset=utf-8",
            )
            response.ok && JSONObject(response.body).optBoolean("ok", false)
        }.getOrDefault(false)
    }

    override fun pauseLullaby(): Boolean {
        return runCatching {
            val response = client.post("/api/lullabies/pause", ByteArray(0), "application/octet-stream")
            response.ok && JSONObject(response.body).optBoolean("ok", false)
        }.getOrDefault(false)
    }

    override fun seekLullaby(positionMillis: Long): Boolean {
        return runCatching {
            val response = client.post(
                "/api/lullabies/seek",
                JSONObject().put("position_ms", positionMillis.coerceAtLeast(0L)).toString().toByteArray(Charsets.UTF_8),
                "application/json; charset=utf-8",
            )
            response.ok && JSONObject(response.body).optBoolean("ok", false)
        }.getOrDefault(false)
    }

    override fun lullabyStatus(): BoardLullabyStatus? {
        return runCatching {
            val response = client.get("/api/lullabies")
            if (!response.ok) return@runCatching null
            val json = JSONObject(response.body)
            if (!json.optBoolean("ok", false)) return@runCatching null
            json.optJSONObject("result")?.toLullabyStatus()
        }.getOrNull()
    }

    override fun setMusicVolume(volume: Float): Boolean {
        val clampedVolume = volume.coerceIn(0f, 1f)
        return runCatching {
            val response = client.post(
                "/api/audio/playback/volume",
                JSONObject().put("volume", clampedVolume).toString().toByteArray(Charsets.UTF_8),
                "application/json; charset=utf-8",
            )
            response.ok && JSONObject(response.body).optBoolean("ok", false)
        }.getOrDefault(false)
    }

    override fun stopMusicAudio(): Boolean {
        return runCatching {
            val response = client.post("/api/audio/playback/stop", ByteArray(0), "application/octet-stream")
            response.ok && JSONObject(response.body).optBoolean("ok", false)
        }.getOrDefault(false)
    }

    override fun startTalkAudio(): Boolean {
        return runCatching {
            val response = client.post("/api/audio/talk/start", ByteArray(0), "application/octet-stream")
            response.ok && JSONObject(response.body).optBoolean("ok", false)
        }.getOrDefault(false)
    }

    override fun sendTalkPcm(pcmBytes: ByteArray): Boolean {
        if (pcmBytes.isEmpty()) return true
        return runCatching {
            val response = client.post(
                "/api/audio/talk/chunk",
                pcmBytes,
                "application/octet-stream",
                TALK_CHUNK_TIMEOUT_MS,
            )
            response.ok && JSONObject(response.body).optBoolean("ok", false)
        }.getOrDefault(false)
    }

    override fun stopTalkAudio(): Boolean {
        return runCatching {
            val response = client.post("/api/audio/talk/stop", ByteArray(0), "application/octet-stream")
            response.ok && JSONObject(response.body).optBoolean("ok", false)
        }.getOrDefault(false)
    }

    override fun liveAudioStream(): InputStream? {
        return runCatching { client.getStream("/api/audio/live.pcm") }.getOrNull()
    }

    override fun alerts(settings: AlertSettingsSnapshot): List<BoardAlert> {
        return runCatching {
            val response = client.get("/api/status")
            if (!response.ok) return@runCatching emptyList()
            val json = JSONObject(response.body)
            if (!json.optBoolean("ok", false)) return@runCatching emptyList()
            val yamnet = json.optJSONObject("yamnet")
            val vision = json.optJSONObject("vision")
            val visionSafetyJson = json.optJSONObject("vision_safety")
                ?: vision?.optJSONObject("safety")
            val visionSafety = visionSafetyJson?.toVisionSafetyState()
                ?: visionSafetyFromLatest(vision?.optJSONObject("latest"))
            val candidates = buildList {
                if (settings.cryAlert && yamnet?.optBoolean("crying", false) == true) {
                    val updatedAtSeconds = yamnet.optDouble("updated_at", 0.0)
                    val timestampMillis = if (updatedAtSeconds > 0.0) {
                        (updatedAtSeconds * 1_000).toLong()
                    } else {
                        System.currentTimeMillis()
                    }
                    add(
                        BoardAlert(
                            id = "cry-${timestampMillis / 10_000L}",
                            type = BoardAlertType.Crying,
                            timestampMillis = timestampMillis,
                        ),
                    )
                }
                if (settings.noPersonAlert && visionSafety.noPerson) {
                    val timestampMillis = visionSafety.timestampMillis
                    add(
                        BoardAlert(
                            id = "no-person-${timestampMillis / 10_000L}",
                            type = BoardAlertType.NoPerson,
                            timestampMillis = timestampMillis,
                        ),
                    )
                }
                if (settings.faceAlert && visionSafety.faceCoverRollover) {
                    val timestampMillis = visionSafety.timestampMillis
                    add(
                        BoardAlert(
                            id = "face-${timestampMillis / 10_000L}",
                            type = BoardAlertType.FaceCovered,
                            timestampMillis = timestampMillis,
                        ),
                    )
                }
                val boundaryTimestampMillis = latestBoundaryTimestampMillis(
                    latest = vision?.optJSONObject("latest"),
                    safetyZone = settings.safetyZone,
                    activityZoneMode = settings.activityZoneMode,
                )
                if (settings.boundaryAlert && boundaryTimestampMillis != null) {
                    add(
                        BoardAlert(
                            id = "boundary-${boundaryTimestampMillis / 10_000L}",
                            type = BoardAlertType.Boundary,
                            timestampMillis = boundaryTimestampMillis,
                        ),
                    )
                }
            }
            stabilizeAlertEvents(candidates)
        }.getOrDefault(emptyList())
    }

    private fun stabilizeAlertEvents(candidates: List<BoardAlert>): List<BoardAlert> {
        synchronized(alertStateLock) {
            val activeTypes = candidates.mapTo(mutableSetOf()) { it.type }
            activeAlertEvents.keys.retainAll(activeTypes)

            val needsJudgmentFrame = candidates.any { candidate ->
                activeAlertEvents[candidate.type]?.thumbnailJpeg == null
            }
            val judgmentFrame = if (needsJudgmentFrame) {
                runCatching {
                    client.getStream("/api/preview-snapshot")?.use { it.readBytes() }
                }.getOrNull()
            } else {
                null
            }
            val events = candidates.map { candidate ->
                val previous = activeAlertEvents[candidate.type]
                when {
                    previous == null -> {
                        val generatedAtMillis = System.currentTimeMillis()
                        candidate.copy(
                            id = "${candidate.type.name}-$generatedAtMillis",
                            timestampMillis = generatedAtMillis,
                            thumbnailJpeg = judgmentFrame,
                        )
                    }
                    previous.thumbnailJpeg == null && judgmentFrame != null -> {
                        previous.copy(thumbnailJpeg = judgmentFrame)
                    }
                    else -> previous
                }.also {
                    activeAlertEvents[candidate.type] = it
                }
            }
            return events.sortedByDescending { it.timestampMillis }
        }
    }

    override fun activityZone(): BoardActivityZone? {
        return runCatching {
            val response = client.get("/api/activity-zone")
            if (!response.ok) return@runCatching null
            val json = JSONObject(response.body)
            if (!json.optBoolean("ok", false)) return@runCatching null
            json.optJSONObject("result")?.toActivityZone()
        }.getOrNull()
    }

    override fun setActivityZone(zone: SafetyZone, mode: ActivityZoneMode): Boolean {
        val normalized = zone.normalized()
        val body = JSONObject()
            .put("mode", mode.apiValue)
            .put(
                "zone",
                JSONObject()
                    .put("left", normalized.left)
                    .put("top", normalized.top)
                    .put("right", normalized.right)
                    .put("bottom", normalized.bottom)
                    .put(
                        "points",
                        JSONArray().apply {
                            normalized.points.forEach { point ->
                                put(JSONArray().put(point.x).put(point.y))
                            }
                        },
                    ),
            )
        return runCatching {
            val response = client.post(
                "/api/activity-zone",
                body.toString().toByteArray(Charsets.UTF_8),
                "application/json; charset=utf-8",
            )
            response.ok && JSONObject(response.body).optBoolean("ok", false)
        }.getOrDefault(false)
    }

    override fun clearActivityZone(): Boolean {
        return runCatching {
            val response = client.delete("/api/activity-zone")
            response.ok && JSONObject(response.body).optBoolean("ok", false)
        }.getOrDefault(false)
    }

    override fun photos(): List<BoardPhoto> {
        return runCatching {
            val response = client.get("/api/photos")
            if (!response.ok) return@runCatching emptyList()
            val items = JSONArray(response.body)
            buildList {
                for (index in 0 until items.length()) {
                    val item = items.getJSONObject(index)
                    add(
                        BoardPhoto(
                            id = item.optString("id", "board-photo-$index"),
                            capturedAtMillis = item.optLong("capturedAtMillis", System.currentTimeMillis()),
                            path = item.optString("path"),
                        ),
                    )
                }
            }.filter { it.path.isNotBlank() }
        }.getOrDefault(emptyList())
    }

    override fun photoStream(photo: BoardPhoto): InputStream? {
        if (photo.path.isBlank()) return null
        return runCatching { client.getStream(photo.downloadPath()) }.getOrNull()
    }

    override fun careOverview(): CareOverview? {
        return runCatching {
            val response = client.get("/api/care/overview")
            if (!response.ok) return@runCatching null
            val json = JSONObject(response.body)
            if (!json.optBoolean("ok", false)) return@runCatching null
            json.optJSONObject("result")?.toCareOverview()
        }.getOrNull()
    }

    override fun careRecords(sinceMillis: Long?, type: CareRecordType?, limit: Int): List<CareRecord>? {
        val query = buildList {
            if (sinceMillis != null) add("since=${sinceMillis / 1_000.0}")
            if (type != null) add("type=${type.apiValue}")
            add("limit=$limit")
        }.joinToString("&")
        return runCatching<List<CareRecord>?> {
            val response = client.get("/api/care/records?$query")
            if (!response.ok) return@runCatching null
            val json = JSONObject(response.body)
            if (!json.optBoolean("ok", false)) return@runCatching null
            val items = json.optJSONArray("result") ?: return@runCatching emptyList()
            buildList<CareRecord> {
                for (index in 0 until items.length()) {
                    items.optJSONObject(index)?.toCareRecord()?.let(::add)
                }
            }
        }.getOrNull()
    }

    override fun saveCareRecord(record: CareRecord, isNew: Boolean): CareCallResult<CareRecord> {
        val path = if (isNew) "/api/care/records" else "/api/care/records/${URLEncoder.encode(record.id, "UTF-8")}"
        return careCall(
            call = {
                client.post(
                    path,
                    record.toJson(includeId = isNew).toString().toByteArray(Charsets.UTF_8),
                    "application/json; charset=utf-8",
                )
            },
            parse = { it.optJSONObject("result")?.toCareRecord() },
        )
    }

    override fun deleteCareRecord(id: String): CareCallResult<Unit> {
        return careCall(
            call = { client.delete("/api/care/records/${URLEncoder.encode(id, "UTF-8")}") },
            parse = { Unit },
            // A retried delete can find the record already gone; that still counts as deleted.
            treatNotFoundAsOk = true,
        )
    }

    override fun saveCareProfile(profile: CareProfile): CareCallResult<CareProfile> {
        return careCall(
            call = {
                client.post(
                    "/api/care/profile",
                    profile.toJson().toString().toByteArray(Charsets.UTF_8),
                    "application/json; charset=utf-8",
                )
            },
            parse = { it.optJSONObject("result")?.toCareProfile() },
        )
    }

    override fun assistantStatus(): AssistantStatus? {
        return runCatching {
            val response = client.get("/api/assistant/status")
            if (!response.ok) return@runCatching null
            val json = JSONObject(response.body)
            if (!json.optBoolean("ok", false)) return@runCatching null
            json.optJSONObject("result")?.toAssistantStatus()
        }.getOrNull()
    }

    override fun askAssistant(
        history: List<AssistantTurn>,
        cancel: AssistantCancelSignal,
        onEvent: (AssistantEvent) -> Unit,
    ) {
        val messages = JSONArray()
        history.forEach { turn ->
            messages.put(JSONObject().put("role", turn.role.apiValue).put("content", turn.text))
        }
        val body = JSONObject().put("stream", true).put("messages", messages)
        val response = try {
            client.postStream(
                "/api/assistant/chat",
                body.toString().toByteArray(Charsets.UTF_8),
                "application/json; charset=utf-8",
                ASSISTANT_READ_TIMEOUT_MS,
            )
        } catch (error: Exception) {
            if (!cancel.cancelled) onEvent(AssistantEvent.Failed("连不上看护设备，请确认手机和设备在同一网络"))
            return
        }
        response.use {
            val stream = response.stream
            if (stream == null) {
                onEvent(AssistantEvent.Failed(assistantErrorMessage(response.code, response.errorBody)))
                return
            }
            cancel.attach { response.cancel() }
            var finished = false
            try {
                val reader = stream.bufferedReader(Charsets.UTF_8)
                while (!finished) {
                    val line = reader.readLine() ?: break
                    if (line.isBlank()) continue
                    val event = parseAssistantEvent(line) ?: continue
                    onEvent(event)
                    finished = event is AssistantEvent.Done || event is AssistantEvent.Failed
                }
                if (!finished && !cancel.cancelled) onEvent(AssistantEvent.Failed("回答中断了，请重试"))
            } catch (error: java.io.IOException) {
                if (!cancel.cancelled) {
                    val message = if (error is java.net.SocketTimeoutException) {
                        "本地模型太久没有回应，请稍后重试"
                    } else {
                        "连接中断了，请重试"
                    }
                    onEvent(AssistantEvent.Failed(message))
                }
            } finally {
                cancel.detach()
            }
        }
    }

    private fun <T> careCall(
        call: () -> BoardHttpResponse,
        parse: (JSONObject) -> T?,
        treatNotFoundAsOk: Boolean = false,
    ): CareCallResult<T> {
        val response = runCatching(call).getOrElse {
            return CareCallResult(null, "连不上看护设备，记录没有保存")
        }
        if (treatNotFoundAsOk && response.code == 404) return CareCallResult(parse(JSONObject()))
        val json = runCatching { JSONObject(response.body) }.getOrNull()
        if (response.ok && json != null && json.optBoolean("ok", false)) {
            val value = parse(json) ?: return CareCallResult(null, "设备返回的数据无法识别")
            return CareCallResult(value)
        }
        val boardError = json?.optStringOrNull("error").orEmpty()
        val message = when {
            boardError.contains("already in progress") -> "已经有一段睡眠在进行中"
            boardError.contains("no sleep in progress") -> "当前没有进行中的睡眠"
            boardError.contains("in the future") -> "时间不能晚于现在"
            boardError.contains("end_at must be after") -> "醒来时间要晚于入睡时间"
            boardError.contains("longer than") -> "一段睡眠不能超过 24 小时"
            boardError.contains("birth_date") -> "生日格式不对，或不在合理范围内"
            boardError.contains("must be between") -> "数值超出合理范围：$boardError"
            response.code == 404 -> "这条记录已经不存在"
            boardError.isNotBlank() -> "保存失败：$boardError"
            else -> "保存失败（${response.code}）"
        }
        return CareCallResult(null, message)
    }

    private fun BoardPhoto.downloadPath(): String {
        return when {
            path.startsWith("/api/photos/") -> path
            path.startsWith("/") -> path
            else -> "/api/photos/$path"
        }
    }

    private fun visionSafetyFromLatest(latest: JSONObject?): VisionSafetyState {
        val timestampMillis = latest?.timestampMillisOrNow() ?: System.currentTimeMillis()
        val person = latest?.optJSONArray("persons")?.optJSONObject(0)
        val keypoints = person?.optJSONArray("keypoints") ?: return VisionSafetyState(
            noPerson = true,
            faceCoverRollover = false,
            timestampMillis = timestampMillis,
        )

        val frontVisible = keypoints.facePointPresent(0) ||
            keypoints.facePointPresent(1) ||
            keypoints.facePointPresent(2)

        return VisionSafetyState(
            noPerson = false,
            faceCoverRollover = !frontVisible,
            timestampMillis = timestampMillis,
        )
    }

    private fun JSONObject.toVisionSafetyState(): VisionSafetyState {
        val timestampMillis = timestampMillisOrNow()
        return VisionSafetyState(
            noPerson = optBoolean("no_person", false),
            faceCoverRollover = optBoolean("face_cover_rollover", false),
            timestampMillis = timestampMillis,
        )
    }

    private fun latestBoundaryTimestampMillis(
        latest: JSONObject?,
        safetyZone: SafetyZone?,
        activityZoneMode: ActivityZoneMode,
    ): Long? {
        if (safetyZone == null) return null
        val timestampMillis = latest?.timestampMillisOrNow() ?: return null
        val person = latest.optJSONArray("persons")?.optJSONObject(0) ?: return null
        val keypoints = person.optJSONArray("keypoints") ?: return null
        val frameSize = latest.optJSONArray("frame_size")
        val frameWidth = frameSize?.optDouble(0, 320.0)?.takeIf { it > 0.0 } ?: 320.0
        val frameHeight = frameSize?.optDouble(1, 180.0)?.takeIf { it > 0.0 } ?: 180.0
        val zone = safetyZone.normalized()
        val bodyPoints = buildList {
            for (index in 0 until keypoints.length()) {
                val point = keypoints.optJSONArray(index) ?: continue
                if (point.optDouble(2, 0.0) < BODY_POINT_THRESHOLD) continue
                add(
                    ZonePoint(
                        x = (point.optDouble(0) / frameWidth).toFloat(),
                        y = (point.optDouble(1) / frameHeight).toFloat(),
                    ),
                )
            }
        }
        if (bodyPoints.isEmpty()) return null
        val alert = when (activityZoneMode) {
            ActivityZoneMode.Safe -> !bodyPoints.all { zone.contains(it.x, it.y) }
            ActivityZoneMode.Danger -> bodyPoints.any { zone.contains(it.x, it.y) }
        }
        return if (alert) timestampMillis else null
    }

    private fun JSONArray.facePointPresent(index: Int): Boolean {
        val point = optJSONArray(index) ?: return false
        val confidence = point.optDouble(2, 0.0)
        return confidence >= FACE_POINT_THRESHOLD
    }

    private fun JSONObject.timestampMillisOrNow(): Long {
        val updatedAtSeconds = optDouble("updated_at", 0.0)
        if (updatedAtSeconds > 0.0) return (updatedAtSeconds * 1_000).toLong()
        val capturedAtSeconds = optDouble("captured_at", 0.0)
        if (capturedAtSeconds > 0.0) return (capturedAtSeconds * 1_000).toLong()
        return System.currentTimeMillis()
    }

    private fun JSONObject.toLullabyStatus(): BoardLullabyStatus {
        val playback = optJSONObject("playback")
        val lastResult = playback?.optJSONObject("last_result")
        val tracksJson = optJSONArray("tracks") ?: JSONArray()
        val tracks = buildList {
            for (index in 0 until tracksJson.length()) {
                tracksJson.optJSONObject(index)?.toLullabyTrack()?.let(::add)
            }
        }
        val currentTrack = optJSONObject("current_track")?.toLullabyTrack()
        val volume = lastResult?.takeIf { it.has("volume") }?.optDouble("volume")?.toFloat()
        return BoardLullabyStatus(
            tracks = tracks,
            currentTrack = currentTrack,
            playing = optBoolean("playing", false),
            paused = optBoolean("paused", false),
            positionMillis = optLong("position_ms", 0L).coerceAtLeast(0L),
            repeatOne = lastResult?.optBoolean("loop", true) ?: true,
            volume = volume,
        )
    }

    private fun JSONObject.toLullabyTrack(): BoardLullabyTrack {
        val durationSeconds = optDouble("duration_s", -1.0)
        val updatedSeconds = optDouble("updated_at", 0.0)
        return BoardLullabyTrack(
            id = optString("id"),
            title = optString("title", "板端摇篮曲"),
            durationMillis = if (durationSeconds >= 0.0) (durationSeconds * 1_000).toLong() else null,
            sizeBytes = optLong("stored_bytes", optLong("bytes", 0L)),
            updatedAtMillis = if (updatedSeconds > 0.0) (updatedSeconds * 1_000).toLong() else 0L,
        )
    }

    private fun JSONObject.toActivityZone(): BoardActivityZone {
        val mode = when (optString("mode", "safe")) {
            "danger" -> ActivityZoneMode.Danger
            else -> ActivityZoneMode.Safe
        }
        val zoneJson = optJSONObject("zone")
        val zone = if (zoneJson != null) {
            val pointsJson = zoneJson.optJSONArray("points")
            val points = buildList {
                if (pointsJson != null) {
                    for (index in 0 until pointsJson.length()) {
                        pointsJson.optJSONArray(index)?.let { point ->
                            if (point.length() >= 2) {
                                add(ZonePoint(point.optDouble(0).toFloat(), point.optDouble(1).toFloat()))
                            }
                        }
                    }
                }
            }
            if (points.size >= 3) {
                SafetyZone(points).normalized().takeIf { it.isValid() }
            } else {
                SafetyZone(
                    left = zoneJson.optDouble("left", 0.0).toFloat(),
                    top = zoneJson.optDouble("top", 0.0).toFloat(),
                    right = zoneJson.optDouble("right", 0.0).toFloat(),
                    bottom = zoneJson.optDouble("bottom", 0.0).toFloat(),
                ).normalized().takeIf { it.isValid() }
            }
        } else {
            null
        }
        return BoardActivityZone(zone = zone, mode = mode)
    }

    private companion object {
        const val TALK_CHUNK_TIMEOUT_MS = 400
        const val MUSIC_UPLOAD_TIMEOUT_MS = 60_000

        // Gap allowed between two reads: prompt prefill on the K1 can take a while before the first token.
        const val ASSISTANT_READ_TIMEOUT_MS = 150_000
        const val FACE_POINT_THRESHOLD = 0.25
        const val BODY_POINT_THRESHOLD = 0.25
    }
}

private data class VisionSafetyState(
    val noPerson: Boolean,
    val faceCoverRollover: Boolean,
    val timestampMillis: Long,
)

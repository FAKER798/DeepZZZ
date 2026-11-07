package com.naviga.app

import android.content.Context
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.LinearGradient
import android.graphics.Paint
import android.graphics.Shader
import android.net.Uri
import android.provider.DocumentsContract
import java.io.File
import java.io.InputStream
import java.io.OutputStream
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

data class GrowthMoment(
    val id: String,
    val capturedAtMillis: Long,
    val imageUri: String? = null,
    val aspectRatio: Float,
    val accent: GrowthMomentAccent,
)

enum class GrowthMomentAccent {
    Primary,
    Secondary,
    Tertiary,
}

class GrowthAlbumStore(context: Context) {
    private val appContext = context.applicationContext
    private val preferences = appContext.getSharedPreferences(
        "growth_album",
        Context.MODE_PRIVATE,
    )

    fun loadMoments(): List<GrowthMoment> {
        return albumFolderUri()?.let(::loadFolderMoments).orEmpty()
    }

    fun addMoment(capturedAtMillis: Long = System.currentTimeMillis()): GrowthMoment? {
        return createMoment(capturedAtMillis = capturedAtMillis)
    }

    fun addSyncedMoment(capturedAtMillis: Long = System.currentTimeMillis()): GrowthMoment? {
        return createMoment(capturedAtMillis = capturedAtMillis)
    }

    fun importMoment(
        capturedAtMillis: Long,
        source: InputStream,
        mimeType: String = "image/jpeg",
    ): GrowthMoment? {
        return source.use { input ->
            runCatching {
                val current = loadMoments()
                val id = "board-$capturedAtMillis-${current.size + 1}"
                val imageUri = createImageOutput(id, mimeType).use { output ->
                    input.copyTo(output.stream)
                    output.uriString
                }
                GrowthMoment(
                    id = id,
                    capturedAtMillis = capturedAtMillis,
                    imageUri = imageUri,
                    aspectRatio = readAspectRatio(imageUri),
                    accent = GrowthMomentAccent.entries[current.size % GrowthMomentAccent.entries.size],
                )
            }.getOrNull()
        }
    }

    private fun createMoment(
        capturedAtMillis: Long,
    ): GrowthMoment? {
        val current = loadMoments()
        val id = "moment-$capturedAtMillis-${current.size + 1}"
        val accent = GrowthMomentAccent.entries[current.size % GrowthMomentAccent.entries.size]
        val aspectRatio = momentAspectRatio(current.size)
        val imageUri = createSnapshotImage(id, capturedAtMillis, aspectRatio, accent) ?: return null
        val moment = GrowthMoment(
            id = id,
            capturedAtMillis = capturedAtMillis,
            imageUri = imageUri,
            aspectRatio = aspectRatio,
            accent = accent,
        )
        return moment
    }

    fun deleteMoment(id: String) {
        loadMoments()
            .firstOrNull { it.id == id }
            ?.imageUri
            ?.let {
                runCatching {
                    DocumentsContract.deleteDocument(appContext.contentResolver, Uri.parse(it))
                }
            }
    }

    fun albumFolderLabel(): String {
        return albumFolderUri()?.toString() ?: "未选择"
    }

    fun setAlbumFolderUri(uri: Uri?) {
        preferences.edit()
            .putString(KEY_ALBUM_FOLDER_URI, uri?.toString().orEmpty())
            .apply()
    }

    private fun createSnapshotImage(
        id: String,
        capturedAtMillis: Long,
        aspectRatio: Float,
        accent: GrowthMomentAccent,
    ): String? {
        return runCatching {
            val width = SNAPSHOT_WIDTH
            val height = (width / aspectRatio).toInt()
            val bitmap = Bitmap.createBitmap(width, height, Bitmap.Config.ARGB_8888)
            val canvas = Canvas(bitmap)
            val background = Paint(Paint.ANTI_ALIAS_FLAG).apply {
                shader = LinearGradient(
                    0f,
                    0f,
                    width.toFloat(),
                    height.toFloat(),
                    accent.startColor,
                    accent.endColor,
                    Shader.TileMode.CLAMP,
                )
            }
            canvas.drawRect(0f, 0f, width.toFloat(), height.toFloat(), background)

            val overlay = Paint(Paint.ANTI_ALIAS_FLAG).apply {
                color = Color.argb(84, 255, 255, 255)
            }
            canvas.drawRoundRect(
                56f,
                56f,
                width - 56f,
                height - 56f,
                42f,
                42f,
                overlay,
            )

            val timePaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
                color = Color.argb(218, 255, 255, 255)
                textSize = 34f
            }
            canvas.drawText(formatMomentTime(capturedAtMillis), 92f, height - 94f, timePaint)

            val savedUri = createImageOutput(id, "image/png").use { output ->
                output.compress(bitmap)
                output.uriString
            }
            bitmap.recycle()
            savedUri
        }.getOrNull()
    }

    private fun createImageOutput(
        id: String,
        mimeType: String,
    ): ImageOutput {
        val folderUri = requireNotNull(albumFolderUri())
        val extension = mimeType.imageExtension()
        val parentDocumentUri = DocumentsContract.buildDocumentUriUsingTree(
            folderUri,
            DocumentsContract.getTreeDocumentId(folderUri),
        )
        val imageUri = requireNotNull(
            DocumentsContract.createDocument(
                appContext.contentResolver,
                parentDocumentUri,
                mimeType,
                "$id.$extension",
            ),
        )
        return ImageOutput(
            stream = requireNotNull(appContext.contentResolver.openOutputStream(imageUri)),
            uriString = imageUri.toString(),
        )
    }

    private fun loadFolderMoments(folderUri: Uri): List<GrowthMoment> {
        return runCatching {
            val childDocumentsUri = DocumentsContract.buildChildDocumentsUriUsingTree(
                folderUri,
                DocumentsContract.getTreeDocumentId(folderUri),
            )
            appContext.contentResolver.query(
                childDocumentsUri,
                AlbumProjection,
                null,
                null,
                null,
            )?.use { cursor ->
                buildList {
                    while (cursor.moveToNext()) {
                        val mimeType = cursor.getString(AlbumMimeTypeIndex).orEmpty()
                        if (!mimeType.startsWith("image/")) continue

                        val documentId = cursor.getString(AlbumDocumentIdIndex)
                        val imageUri = DocumentsContract
                            .buildDocumentUriUsingTree(folderUri, documentId)
                            .toString()
                        val lastModified = cursor
                            .getLong(AlbumLastModifiedIndex)
                            .takeIf { it > 0L }
                            ?: System.currentTimeMillis()
                        add(
                            GrowthMoment(
                                id = documentId,
                                capturedAtMillis = lastModified,
                                imageUri = imageUri,
                                aspectRatio = readAspectRatio(imageUri),
                                accent = GrowthMomentAccent.entries[size % GrowthMomentAccent.entries.size],
                            ),
                        )
                    }
                }
            }.orEmpty().sortedByDescending { it.capturedAtMillis }
        }.getOrDefault(emptyList())
    }

    private fun readAspectRatio(imageUri: String): Float {
        val options = BitmapFactory.Options().apply {
            inJustDecodeBounds = true
        }
        return runCatching {
            appContext.contentResolver.openInputStream(Uri.parse(imageUri))?.use { input ->
                BitmapFactory.decodeStream(input, null, options)
            }
            if (options.outWidth > 0 && options.outHeight > 0) {
                options.outWidth.toFloat() / options.outHeight.toFloat()
            } else {
                DEFAULT_ASPECT_RATIO
            }
        }.getOrDefault(DEFAULT_ASPECT_RATIO)
    }

    private fun albumFolderUri(): Uri? {
        return preferences
            .getString(KEY_ALBUM_FOLDER_URI, null)
            ?.takeIf { it.isNotBlank() }
            ?.let(Uri::parse)
    }

    private fun momentAspectRatio(index: Int): Float {
        return MOMENT_ASPECT_RATIOS[index % MOMENT_ASPECT_RATIOS.size]
    }

    private class ImageOutput(
        val stream: OutputStream,
        val uriString: String,
    ) : AutoCloseable {
        fun compress(bitmap: Bitmap) {
            bitmap.compress(Bitmap.CompressFormat.PNG, 96, stream)
        }

        override fun close() {
            stream.close()
        }
    }

    private companion object {
        const val KEY_ALBUM_FOLDER_URI = "album_folder_uri"
        const val DEFAULT_ASPECT_RATIO = 0.78f
        const val SNAPSHOT_WIDTH = 960
        val MOMENT_ASPECT_RATIOS = listOf(0.78f, 1.0f, 0.68f, 0.86f, 1.16f)
        val AlbumProjection = arrayOf(
            DocumentsContract.Document.COLUMN_DOCUMENT_ID,
            DocumentsContract.Document.COLUMN_MIME_TYPE,
            DocumentsContract.Document.COLUMN_LAST_MODIFIED,
        )
        const val AlbumDocumentIdIndex = 0
        const val AlbumMimeTypeIndex = 1
        const val AlbumLastModifiedIndex = 2
    }
}

private fun String.imageExtension(): String {
    return when (lowercase()) {
        "image/png" -> "png"
        "image/webp" -> "webp"
        else -> "jpg"
    }
}

class GrowthAlbumImageLoader(private val context: Context) {
    fun load(imageUri: String?) = runCatching {
        when {
            imageUri.isNullOrBlank() -> null
            imageUri.startsWith("content://") -> {
                context.contentResolver.openInputStream(Uri.parse(imageUri))?.use(BitmapFactory::decodeStream)
            }
            else -> File(imageUri).takeIf { it.exists() }?.let { BitmapFactory.decodeFile(it.absolutePath) }
        }
    }.getOrNull()
}

fun GrowthMoment.capturedAtLabel(): String {
    return MomentDateFormat.get().format(Date(capturedAtMillis))
}

private object MomentDateFormat {
    private val formatter = ThreadLocal.withInitial {
        SimpleDateFormat("M月d日 HH:mm", Locale.CHINA)
    }

    fun get(): SimpleDateFormat = requireNotNull(formatter.get())
}

private fun formatMomentTime(capturedAtMillis: Long): String {
    return MomentDateFormat.get().format(Date(capturedAtMillis))
}

private val GrowthMomentAccent.startColor: Int
    get() = when (this) {
        GrowthMomentAccent.Primary -> Color.rgb(89, 132, 255)
        GrowthMomentAccent.Secondary -> Color.rgb(44, 186, 146)
        GrowthMomentAccent.Tertiary -> Color.rgb(245, 142, 101)
    }

private val GrowthMomentAccent.endColor: Int
    get() = when (this) {
        GrowthMomentAccent.Primary -> Color.rgb(131, 96, 195)
        GrowthMomentAccent.Secondary -> Color.rgb(76, 154, 255)
        GrowthMomentAccent.Tertiary -> Color.rgb(231, 86, 113)
    }

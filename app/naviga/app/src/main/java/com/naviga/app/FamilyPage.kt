package com.naviga.app

import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
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
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.rounded.AddPhotoAlternate
import androidx.compose.material.icons.rounded.Close
import androidx.compose.material.icons.rounded.Image
import androidx.compose.material.icons.rounded.PersonAdd
import androidx.compose.material3.Button
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.ElevatedCard
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.ImageBitmap
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.window.Dialog
import androidx.compose.ui.window.DialogProperties
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext

@Composable
fun FamilyPage(
    moments: List<GrowthMoment>,
    imageLoader: GrowthAlbumImageLoader,
    inviteCount: Int,
    onInvite: () -> Unit,
) {
    val members = remember { familyMembers() }
    val imageCache = remember { mutableMapOf<String, ImageBitmap?>() }
    var selectedMoment by remember { mutableStateOf<GrowthMoment?>(null) }

    PageColumn {
        item {
            PageHeader(title = "家人")
        }
        item { MembersCard(members = members, inviteCount = inviteCount, onInvite = onInvite) }
        item {
            GrowthAlbumCard(
                moments = moments,
                imageLoader = imageLoader,
                imageCache = imageCache,
                onOpenMoment = { selectedMoment = it },
            )
        }
    }

    selectedMoment?.let { moment ->
        GrowthMomentViewer(
            moment = moment,
            imageLoader = imageLoader,
            imageCache = imageCache,
            onDismiss = { selectedMoment = null },
        )
    }
}

@Composable
private fun MembersCard(
    members: List<FamilyMember>,
    inviteCount: Int,
    onInvite: () -> Unit,
) {
    ElevatedCard(
        modifier = Modifier.fillMaxWidth(),
        shape = CardShape,
        colors = CardDefaults.elevatedCardColors(containerColor = MaterialTheme.colorScheme.surface),
    ) {
        Column(
            modifier = Modifier.padding(18.dp),
            verticalArrangement = Arrangement.spacedBy(16.dp),
        ) {
            members.forEach { member ->
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(14.dp),
                ) {
                    Avatar(
                        label = member.name.first().toString(),
                        color = when (member.role) {
                            FamilyRole.Mother -> MaterialTheme.colorScheme.primaryContainer
                            FamilyRole.Father -> MaterialTheme.colorScheme.secondaryContainer
                        },
                    )
                    Column(modifier = Modifier.weight(1f)) {
                        Text(
                            text = member.name,
                            style = MaterialTheme.typography.titleMedium,
                        )
                        Text(
                            text = member.role.label,
                            style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                    }
                    OnlineIndicator(online = member.online)
                }
            }

            Button(
                onClick = onInvite,
                modifier = Modifier.fillMaxWidth(),
                shape = ControlShape,
                contentPadding = PaddingValues(14.dp),
            ) {
                Icon(Icons.Rounded.PersonAdd, contentDescription = null)
                Spacer(Modifier.width(8.dp))
                Text(if (inviteCount == 0) "邀请家人" else "再生成邀请链接")
            }
        }
    }
}

@Composable
private fun OnlineIndicator(online: Boolean) {
    Row(
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(6.dp),
    ) {
        Box(
            modifier = Modifier
                .size(8.dp)
                .clip(CircleShape)
                .background(
                    if (online) MaterialTheme.colorScheme.primary
                    else MaterialTheme.colorScheme.outline.copy(alpha = 0.4f),
                ),
        )
        Text(
            text = if (online) "在线" else "离线",
            style = MaterialTheme.typography.labelSmall,
            color = if (online) MaterialTheme.colorScheme.primary
            else MaterialTheme.colorScheme.onSurfaceVariant,
        )
    }
}

@Composable
private fun GrowthAlbumCard(
    moments: List<GrowthMoment>,
    imageLoader: GrowthAlbumImageLoader,
    imageCache: MutableMap<String, ImageBitmap?>,
    onOpenMoment: (GrowthMoment) -> Unit,
) {
    PressedCard {
        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Text("成长相册", style = MaterialTheme.typography.titleLarge)
            IconBadge(icon = Icons.Rounded.Image)
        }
        Spacer(Modifier.height(14.dp))

        if (moments.isEmpty()) {
            EmptyAlbumState()
        } else {
            MasonryGrid(
                moments = moments,
                imageLoader = imageLoader,
                imageCache = imageCache,
                onOpenMoment = onOpenMoment,
            )
        }
    }
}

@Composable
private fun MasonryGrid(
    moments: List<GrowthMoment>,
    imageLoader: GrowthAlbumImageLoader,
    imageCache: MutableMap<String, ImageBitmap?>,
    onOpenMoment: (GrowthMoment) -> Unit,
) {
    val columns = remember(moments) { moments.masonryColumns() }

    Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
        columns.forEach { columnMoments ->
            Column(
                modifier = Modifier.weight(1f),
                verticalArrangement = Arrangement.spacedBy(10.dp),
            ) {
                columnMoments.forEach { moment ->
                    GrowthMomentTile(
                        moment = moment,
                        imageLoader = imageLoader,
                        imageCache = imageCache,
                        onClick = { onOpenMoment(moment) },
                    )
                }
            }
        }
    }
}

@Composable
private fun EmptyAlbumState() {
    Column(
        modifier = Modifier
            .fillMaxWidth()
            .clip(ControlShape)
            .background(MaterialTheme.colorScheme.surfaceVariant.copy(alpha = 0.62f))
            .padding(16.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        Icon(
            imageVector = Icons.Rounded.AddPhotoAlternate,
            contentDescription = null,
            tint = MaterialTheme.colorScheme.primary,
        )
        Text("暂无成长瞬间", style = MaterialTheme.typography.titleMedium)
    }
}

@Composable
private fun GrowthMomentTile(
    moment: GrowthMoment,
    imageLoader: GrowthAlbumImageLoader,
    imageCache: MutableMap<String, ImageBitmap?>,
    onClick: () -> Unit,
) {
    Box(
        modifier = Modifier
            .fillMaxWidth()
            .height((160 / moment.aspectRatio).dp)
            .clip(ControlShape)
            .background(moment.accent.color)
            .clickable(onClick = onClick),
        contentAlignment = Alignment.Center,
    ) {
        AsyncGrowthImage(
            imageUri = moment.imageUri,
            imageLoader = imageLoader,
            imageCache = imageCache,
            modifier = Modifier.fillMaxSize(),
            contentScale = ContentScale.Crop,
            placeholder = {
                Icon(
                    imageVector = Icons.Rounded.Image,
                    contentDescription = null,
                    tint = MaterialTheme.colorScheme.primary,
                    modifier = Modifier.size(28.dp),
                )
            },
        )
    }
}

@Composable
private fun AsyncGrowthImage(
    imageUri: String?,
    imageLoader: GrowthAlbumImageLoader,
    imageCache: MutableMap<String, ImageBitmap?>,
    modifier: Modifier,
    contentScale: ContentScale,
    placeholder: @Composable () -> Unit = {},
) {
    var bitmap by remember(imageUri) { mutableStateOf(imageUri?.let { imageCache[it] }) }

    LaunchedEffect(imageUri, imageLoader) {
        if (imageUri.isNullOrBlank()) {
            bitmap = null
            return@LaunchedEffect
        }
        imageCache[imageUri]?.let {
            bitmap = it
            return@LaunchedEffect
        }
        bitmap = withContext(Dispatchers.IO) {
            imageLoader.load(imageUri)?.asImageBitmap()
        }.also {
            imageCache[imageUri] = it
        }
    }

    if (bitmap != null) {
        Image(
            bitmap = requireNotNull(bitmap),
            contentDescription = null,
            modifier = modifier,
            contentScale = contentScale,
        )
    } else {
        Box(modifier = modifier, contentAlignment = Alignment.Center) {
            placeholder()
        }
    }
}

@Composable
private fun GrowthMomentViewer(
    moment: GrowthMoment,
    imageLoader: GrowthAlbumImageLoader,
    imageCache: MutableMap<String, ImageBitmap?>,
    onDismiss: () -> Unit,
) {
    Dialog(
        onDismissRequest = onDismiss,
        properties = DialogProperties(usePlatformDefaultWidth = false),
    ) {
        Surface(
            modifier = Modifier.fillMaxSize(),
            color = Color.Black,
        ) {
            Box(modifier = Modifier.fillMaxSize()) {
                AsyncGrowthImage(
                    imageUri = moment.imageUri,
                    imageLoader = imageLoader,
                    imageCache = imageCache,
                    modifier = Modifier.fillMaxSize(),
                    contentScale = ContentScale.Fit,
                )
                IconButton(
                    onClick = onDismiss,
                    modifier = Modifier
                        .align(Alignment.TopEnd)
                        .padding(14.dp),
                ) {
                    Icon(
                        imageVector = Icons.Rounded.Close,
                        contentDescription = "关闭",
                        tint = Color.White,
                    )
                }
                Box(
                    modifier = Modifier
                        .align(Alignment.BottomCenter)
                        .fillMaxWidth()
                        .background(Color.Black.copy(alpha = 0.58f))
                        .padding(horizontal = 20.dp, vertical = 18.dp),
                ) {
                    Text(
                        text = moment.capturedAtLabel(),
                        style = MaterialTheme.typography.titleMedium,
                        color = Color.White,
                    )
                }
            }
        }
    }
}

@Composable
private fun Avatar(label: String, color: Color) {
    Box(
        modifier = Modifier
            .size(44.dp)
            .clip(CircleShape)
            .background(color),
        contentAlignment = Alignment.Center,
    ) {
        Text(
            text = label,
            style = MaterialTheme.typography.titleMedium,
            fontWeight = FontWeight.Bold,
            color = MaterialTheme.colorScheme.onSurface,
        )
    }
}

private val GrowthMomentAccent.color: Color
    @Composable
    get() = when (this) {
        GrowthMomentAccent.Primary -> MaterialTheme.colorScheme.primaryContainer
        GrowthMomentAccent.Secondary -> MaterialTheme.colorScheme.secondaryContainer
        GrowthMomentAccent.Tertiary -> MaterialTheme.colorScheme.tertiaryContainer
    }

private fun List<GrowthMoment>.masonryColumns(): List<List<GrowthMoment>> {
    val left = mutableListOf<GrowthMoment>()
    val right = mutableListOf<GrowthMoment>()
    var leftHeight = 0f
    var rightHeight = 0f

    forEach { moment ->
        val estimatedHeight = 1f / moment.aspectRatio
        if (leftHeight <= rightHeight) {
            left += moment
            leftHeight += estimatedHeight
        } else {
            right += moment
            rightHeight += estimatedHeight
        }
    }

    return listOf(left, right)
}

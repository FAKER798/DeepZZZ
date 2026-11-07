package com.naviga.app

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.width
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.rounded.Refresh
import androidx.compose.material.icons.rounded.Router
import androidx.compose.material3.Button
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.launch

@Composable
fun ConnectionSettingsPage(
    onBack: () -> Unit,
    boardConnection: BoardConnectionSession = remember { BoardConnectionSession() },
) {
    val scope = rememberCoroutineScope()

    SettingsDetailScaffold(
        title = "设备连接",
        onBack = onBack,
    ) {
        item {
            ConnectionStatusCard(
                name = boardConnection.identity.name,
                endpoint = boardConnection.endpointLabel,
                status = boardConnection.status,
            )
        }
        item {
            Button(
                onClick = {
                    scope.launch {
                        boardConnection.checkHealth()
                    }
                },
                modifier = Modifier.fillMaxWidth(),
                contentPadding = PaddingValues(14.dp),
                enabled = boardConnection.status != BoardConnectionStatus.Checking,
            ) {
                Icon(Icons.Rounded.Refresh, contentDescription = null)
                Spacer(Modifier.width(8.dp))
                Text("检查连接")
            }
        }
    }
}

@Composable
private fun ConnectionStatusCard(
    name: String,
    endpoint: String,
    status: BoardConnectionStatus,
) {
    PressedCard(contentPadding = PaddingValues(14.dp)) {
        Row(
            modifier = Modifier.fillMaxWidth(),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            IconBadge(icon = Icons.Rounded.Router)
            Column(modifier = Modifier.weight(1f)) {
                Text(name, style = MaterialTheme.typography.titleMedium)
                Text(
                    text = endpoint,
                    style = MaterialTheme.typography.bodyMedium,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
                Text(
                    text = status.label,
                    style = MaterialTheme.typography.bodyMedium,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
        }
    }
}

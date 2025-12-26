# Asana MCP Server

MCP server for Asana integration providing bidirectional sync, import, export, and metadata management between Asana workspaces and local parquet data.

## Credits

This MCP server consolidates functionality from the Asana integration scripts in the personal workflow repository, providing a standardized interface for AI assistants.

## Features

- **Bidirectional Sync**: Three-way merge sync between Asana workspaces and local parquet
- **Task Import**: Import tasks from Asana with intelligent merge
- **Task Export**: Export local tasks to Asana with duplicate detection
- **Comments Import**: Import task comments and attachments
- **Metadata Import**: Import custom fields, dependencies, and stories
- **Webhook Registration**: Register webhooks for real-time sync
- **Utility Tools**: Get individual tasks, list projects, get workspace info
- **Parquet MCP Integration**: All data operations go through parquet MCP server

## Installation

```bash
cd execution/mcp-servers/asana
pip install -r requirements.txt
```

## Configuration

### Authentication

The server supports multiple authentication methods (checked in priority order):

1. **Environment Variables** (recommended, highest priority):
   ```bash
   export ASANA_SOURCE_PAT="your-source-pat"
   export ASANA_TARGET_PAT="your-target-pat"  # Optional, defaults to source
   export SOURCE_WORKSPACE_GID="source-workspace-gid"
   export TARGET_WORKSPACE_GID="target-workspace-gid"
   export FALLBACK_ASSIGNEE_EMAIL="your@email.com"  # Optional
   ```

2. **Config Directory `.env` File** (portable, user-specific):
   - Location: `~/.config/asana-mcp/.env`
   - Format:
     ```
     ASANA_SOURCE_PAT=your-source-pat
     ASANA_TARGET_PAT=your-target-pat
     SOURCE_WORKSPACE_GID=source-workspace-gid
     TARGET_WORKSPACE_GID=target-workspace-gid
     FALLBACK_ASSIGNEE_EMAIL=your@email.com
     ```
   - The config directory is created automatically on first use

3. **1Password Integration** (optional, for backward compatibility):
   - Only available if parent repository structure exists
   - Configure 1Password item titled "Asana"
   - Add fields: "source_pat", "target_pat", "source_workspace_gid", "target_workspace_gid"

**Note:** The MCP server is self-contained and portable. It does not require any specific repository structure.

### Parquet MCP Server

This server requires the parquet MCP server to be configured and accessible. The server auto-detects the parquet server location or you can set:

```bash
export PARQUET_MCP_SERVER_PATH="/path/to/parquet_mcp_server.py"
```

### Cursor Configuration

Add to your Cursor MCP settings (typically `~/.cursor/mcp.json` or Cursor settings):

```json
{
  "mcpServers": {
    "asana": {
      "command": "python3",
      "args": [
        "/path/to/asana_mcp_server.py"
      ],
      "env": {
        "ASANA_SOURCE_PAT": "your-source-pat",
        "SOURCE_WORKSPACE_GID": "source-workspace-gid",
        "TARGET_WORKSPACE_GID": "target-workspace-gid"
      }
    }
  }
}
```

**Note:** Replace `/path/to/asana_mcp_server.py` with the actual path to the server file.

### Claude Desktop Configuration

Add to `claude_desktop_config.json` (typically `~/Library/Application Support/Claude/claude_desktop_config.json` on macOS):

```json
{
  "mcpServers": {
    "asana": {
      "command": "python3",
      "args": [
        "/path/to/asana_mcp_server.py"
      ],
      "env": {
        "ASANA_SOURCE_PAT": "your-source-pat",
        "SOURCE_WORKSPACE_GID": "source-workspace-gid",
        "TARGET_WORKSPACE_GID": "target-workspace-gid"
      }
    }
  }
}
```

**Note:** Replace `/path/to/asana_mcp_server.py` with the actual path to the server file.

## Available Tools

### `import_asana_tasks`

Import tasks from Asana workspace to local parquet via parquet MCP server.

**Parameters:**
- `workspace` (optional): Workspace to import from ("source" or "target", default: "source")
- `only_incomplete` (optional): Only fetch incomplete tasks (default: false)
- `assignee_gid` (optional): Filter tasks by assignee GID
- `max_tasks` (optional): Maximum number of tasks to import
- `recalculate` (optional): Force recalculation of domain/priority/urgency (default: false)
- `include_archived` (optional): Include tasks from archived projects (default: true)

**Returns:**
- `success`: Boolean indicating success
- `workspace`: Workspace imported from
- `fetched`: Number of tasks fetched from Asana
- `updated`: Number of existing tasks updated
- `new`: Number of new tasks added

**Example Request:**
```json
{
  "workspace": "source",
  "only_incomplete": true,
  "max_tasks": 50
}
```

**Example Response:**
```json
{
  "success": true,
  "workspace": "source",
  "fetched": 45,
  "updated": 30,
  "new": 15
}
```

### `export_asana_tasks`

Export local tasks to Asana workspace.

**Parameters:**
- `workspace` (optional): Workspace to export to ("target", default: "target")
- `limit` (optional): Maximum number of tasks to export (default: 10)
- `sync_log_filter` (optional): Filter tasks by sync_log value (e.g., "pending_export")

**Returns:**
- `success`: Boolean indicating success
- `workspace`: Workspace exported to
- `processed`: Number of tasks processed
- `created`: Number of tasks created in Asana
- `updated`: Number of tasks updated in Asana
- `failed`: Number of tasks that failed to export

**Example Request:**
```json
{
  "workspace": "target",
  "limit": 20,
  "sync_log_filter": "pending_export"
}
```

**Example Response:**
```json
{
  "success": true,
  "workspace": "target",
  "processed": 20,
  "created": 12,
  "updated": 7,
  "failed": 1
}
```

### `sync_asana_tasks`

Bidirectional sync between Asana workspaces and local parquet using three-way merge for field-level reconciliation.

**Parameters:**
- `sync_scope` (optional): Scope of sync ("both", "source", or "target", default: "both")
- `dry_run` (optional): Preview changes without applying them (default: false)

**Returns:**
- `success`: Boolean indicating success
- `source_to_local`: Statistics for source → local sync
- `target_to_local`: Statistics for target → local sync
- `local_to_source`: Statistics for local → source sync
- `local_to_target`: Statistics for local → target sync
- `sync_scope`: Scope of sync performed

**Example Request:**
```json
{
  "sync_scope": "both",
  "dry_run": false
}
```

**Example Response:**
```json
{
  "success": true,
  "source_to_local": {
    "updated": 5,
    "new": 2
  },
  "target_to_local": {
    "updated": 3,
    "new": 1
  },
  "local_to_source": {
    "updated": 2,
    "created": 0
  },
  "local_to_target": {
    "updated": 4,
    "created": 1
  },
  "sync_scope": "both"
}
```

### `import_asana_task_comments`

Import comments for specific tasks.

**Parameters:**
- `task_gids` (required): Array of task GIDs to import comments for
- `workspace` (optional): Workspace ("source" or "target", default: "source")

**Returns:**
- `success`: Boolean indicating success
- `workspace`: Workspace imported from
- `task_count`: Number of tasks processed
- `comments_imported`: Number of comments imported

**Example Request:**
```json
{
  "task_gids": ["1234567890", "0987654321"],
  "workspace": "source"
}
```

**Example Response:**
```json
{
  "success": true,
  "workspace": "source",
  "task_count": 2,
  "comments_imported": 15
}
```

### `import_asana_task_metadata`

Import task metadata: custom fields, dependencies, and stories.

**Parameters:**
- `task_gids` (required): Array of task GIDs to import metadata for
- `workspace` (optional): Workspace ("source" or "target", default: "source")
- `metadata_types` (optional): Types of metadata to import (array, default: ["custom_fields", "dependencies", "stories"])

**Returns:**
- `success`: Boolean indicating success
- `workspace`: Workspace imported from
- `task_count`: Number of tasks processed
- `custom_fields`: Number of custom fields imported
- `dependencies`: Number of dependencies imported
- `stories`: Number of stories imported

**Example Request:**
```json
{
  "task_gids": ["1234567890", "0987654321"],
  "workspace": "source",
  "metadata_types": ["custom_fields", "dependencies"]
}
```

**Example Response:**
```json
{
  "success": true,
  "workspace": "source",
  "task_count": 2,
  "custom_fields": 5,
  "dependencies": 3,
  "stories": 0
}
```

### `register_asana_webhooks`

Register Asana webhooks for projects in workspaces.

**Parameters:**
- `webhook_url` (required): Public webhook URL (must be HTTPS)
- `workspace` (optional): Workspace to register webhooks for ("source", "target", or "both", default: "both")

**Returns:**
- `source` (if applicable): Registration results for source workspace
- `target` (if applicable): Registration results for target workspace
- Each workspace result includes:
  - `workspace`: Workspace name
  - `registered_count`: Number of webhooks registered
  - `failed_count`: Number of failed registrations
  - `registered`: Array of registered webhooks
  - `failed`: Array of failed registrations

**Example Request:**
```json
{
  "webhook_url": "https://example.com/webhooks/asana",
  "workspace": "both"
}
```

**Example Response:**
```json
{
  "source": {
    "workspace": "source",
    "registered_count": 10,
    "failed_count": 0,
    "registered": [
      {
        "project_gid": "1234567890",
        "project_name": "Project Name",
        "webhook_gid": "9876543210",
        "status": "registered"
      }
    ],
    "failed": []
  },
  "target": {
    "workspace": "target",
    "registered_count": 5,
    "failed_count": 0,
    "registered": [],
    "failed": []
  }
}
```

### `get_asana_task`

Get a single task from Asana by GID.

**Parameters:**
- `task_gid` (required): Asana task GID
- `workspace` (optional): Workspace ("source" or "target", default: "source")

**Returns:**
- `success`: Boolean indicating success
- `workspace`: Workspace fetched from
- `task`: Task data object

**Example Request:**
```json
{
  "task_gid": "1234567890",
  "workspace": "source"
}
```

**Example Response:**
```json
{
  "success": true,
  "workspace": "source",
  "task": {
    "gid": "1234567890",
    "name": "Task Title",
    "notes": "Task description",
    "completed": false,
    "due_on": "2026-01-15",
    "assignee": {
      "gid": "0987654321",
      "name": "User Name"
    }
  }
}
```

### `list_asana_projects`

List projects in an Asana workspace.

**Parameters:**
- `workspace` (optional): Workspace ("source" or "target", default: "source")
- `archived` (optional): Include archived projects (true, false, or null for all)

**Returns:**
- `success`: Boolean indicating success
- `workspace`: Workspace queried
- `workspace_gid`: Workspace GID
- `count`: Number of projects
- `projects`: Array of project objects

**Example Request:**
```json
{
  "workspace": "source",
  "archived": false
}
```

**Example Response:**
```json
{
  "success": true,
  "workspace": "source",
  "workspace_gid": "1234567890",
  "count": 15,
  "projects": [
    {
      "gid": "1234567890",
      "name": "Project Name",
      "archived": false
    }
  ]
}
```

### `get_asana_workspace_info`

Get information about an Asana workspace.

**Parameters:**
- `workspace` (optional): Workspace ("source" or "target", default: "source")

**Returns:**
- `success`: Boolean indicating success
- `workspace`: Workspace queried
- `workspace_data`: Workspace information object

**Example Request:**
```json
{
  "workspace": "source"
}
```

**Example Response:**
```json
{
  "success": true,
  "workspace": "source",
  "workspace_data": {
    "gid": "1234567890",
    "name": "My Workspace",
    "email_domains": ["example.com"],
    "is_organization": true
  }
}
```

## Error Handling

The server returns structured error messages in JSON format when operations fail. Common errors include:

- **Authentication errors**: Missing or invalid PATs
- **Workspace errors**: Invalid workspace GIDs
- **API errors**: Asana API errors with status codes and messages
- **MCP errors**: Parquet MCP server connection or operation errors

**Example error response:**
```json
{
  "error": "Missing required configuration values: ASANA_SOURCE_PAT, SOURCE_WORKSPACE_GID",
  "type": "RuntimeError",
  "context": "Tool: import_asana_tasks"
}
```

## Security Notes

- API tokens are never logged or exposed in error messages
- Tokens can be stored in environment variables, `~/.config/asana-mcp/.env`, or 1Password
- All API requests use HTTPS
- Config directory `.env` file has restricted permissions (owner read/write only)
- Environment variables are the most secure method (not persisted to disk)
- All data operations go through parquet MCP server (automatic snapshots and audit logging)

## Troubleshooting

1. **Authentication Fails**
   - Verify PATs are correct and not expired
   - Get new PAT from: https://app.asana.com/0/my-apps
   - Ensure workspace GIDs are correct
   - Check 1Password integration is configured correctly (if using)

2. **Workspace Not Found**
   - Verify workspace GIDs are correct
   - Check PAT has access to workspaces
   - Ensure workspace is not archived or deleted

3. **Parquet MCP Connection Fails**
   - Verify parquet MCP server is configured in Cursor/Claude Desktop
   - Check `PARQUET_MCP_SERVER_PATH` environment variable
   - Ensure parquet server is accessible

4. **Tasks Not Syncing**
   - Check task has required fields (title)
   - Verify workspace permissions
   - Check sync_log field for error codes
   - Review Asana API rate limits

5. **Duplicate Tasks**
   - The server checks for duplicates by title before creating
   - If duplicates exist, it updates the existing task instead

## Notes

- The server uses the parquet MCP server for all data operations (never accesses parquet files directly)
- Webhook registration requires the webhook server to be running and accessible
- The webhook server (`asana_webhook_server.py`) remains a separate background service
- Three-way merge preserves changes on both sides when different properties are modified
- All date fields are returned as strings in ISO format
- The server runs in stdio mode for MCP communication
- Monitoring scripts (`monitor_asana_import.py`, `monitor_asana_export.py`) remain separate

## Sync Strategy

The bidirectional sync uses a three-way merge approach:

1. **Last Synced State**: Stores task state after last successful sync
2. **Three-Way Comparison**: Compares last_synced vs current_local vs current_asana
3. **Merge Logic**:
   - If only Asana changed: Use Asana value
   - If only Local changed: Use Local value
   - If both changed: Use newer timestamp (conflict resolution)
   - If neither changed: Keep existing value

This approach preserves changes on both sides when different properties are modified, without requiring per-property timestamps.

## Architecture

```
Asana MCP Server
├── Asana API (read/write tasks, comments, metadata)
├── Parquet MCP Server (via MCP client)
│   ├── tasks.parquet
│   ├── task_comments.parquet
│   ├── task_custom_fields.parquet
│   ├── task_dependencies.parquet
│   ├── task_stories.parquet
│   └── task_attachments.parquet
└── Automatic snapshots & audit logging (via parquet MCP)
```

## License

MIT

## Support

- [GitHub Issues](https://github.com/markmhendrickson/mcp-server-asana/issues)


"""
Asana task sync engine with three-way merge and parquet MCP integration.

Bidirectional sync between Asana workspaces and local parquet.
"""

import sys
from datetime import datetime, date
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

# Add current directory to path for imports
sys.path.insert(0, str(Path(__file__).parent))

from config import AsanaConfig
from client import AsanaClientWrapper
from parquet_client import ParquetMCPClient


class AsanaTaskSyncer:
    """Bidirectional sync between Asana workspaces and local parquet via MCP."""
    
    def __init__(
        self,
        config: AsanaConfig,
        parquet_client: ParquetMCPClient,
        sync_scope: str = "both",
        dry_run: bool = False
    ):
        self.config = config
        self.parquet_client = parquet_client
        self.sync_scope = sync_scope
        self.dry_run = dry_run
        
        self.source_client = AsanaClientWrapper.from_config_source(config)
        self.target_client = AsanaClientWrapper.from_config_target(config)
        
        # Cache for last synced state
        self._last_synced_state = {}
    
    async def sync(self) -> Dict[str, Any]:
        """
        Perform bidirectional sync.
        
        Returns: Sync statistics
        """
        stats = {
            'source_to_local': {'updated': 0, 'new': 0},
            'target_to_local': {'updated': 0, 'new': 0},
            'local_to_source': {'updated': 0, 'created': 0},
            'local_to_target': {'updated': 0, 'created': 0},
            'sync_scope': self.sync_scope,
        }
        
        try:
            do_source = self.sync_scope in ("both", "source")
            do_target = self.sync_scope in ("both", "target")
            
            # Load last synced state
            await self.load_last_synced_state()
            
            # Sync source workspace ↔ local
            if do_source:
                updated, new = await self.sync_workspace_to_local(
                    self.source_client,
                    self.config.source_workspace_gid,
                    'source'
                )
                stats['source_to_local'] = {'updated': updated, 'new': new}
            
            # Sync target workspace ↔ local
            if do_target:
                updated, new = await self.sync_workspace_to_local(
                    self.target_client,
                    self.config.target_workspace_gid,
                    'target'
                )
                stats['target_to_local'] = {'updated': updated, 'new': new}
            
            # Sync local → source workspace
            if do_source:
                updated, created = await self.sync_local_to_workspace(
                    self.source_client,
                    self.config.source_workspace_gid,
                    'source'
                )
                stats['local_to_source'] = {'updated': updated, 'created': created}
            
            # Sync local → target workspace
            if do_target:
                updated, created = await self.sync_local_to_workspace(
                    self.target_client,
                    self.config.target_workspace_gid,
                    'target'
                )
                stats['local_to_target'] = {'updated': updated, 'created': created}
            
            # Save last synced state after successful sync
            if not self.dry_run:
                await self.save_last_synced_state()
        
        except Exception as e:
            print(f"Error during sync: {e}", file=sys.stderr)
            raise
        
        return {
            "success": True,
            **stats
        }
    
    async def sync_workspace_to_local(
        self,
        client: AsanaClientWrapper,
        workspace_gid: str,
        workspace_name: str
    ) -> Tuple[int, int]:
        """Sync tasks from Asana workspace to local parquet."""
        # Fetch modified tasks from Asana
        tasks = await self.fetch_tasks_modified_since(client, workspace_gid, workspace_name)
        
        if not tasks:
            return 0, 0
        
        updated_count = 0
        new_count = 0
        
        for task_data in tasks:
            normalized = self.normalize_asana_task(task_data, workspace_name)
            task_id = normalized['task_id']
            
            # Check if task exists locally
            existing_tasks = await self.parquet_client.read_tasks(
                filters={"task_id": task_id},
                limit=1
            )
            
            if existing_tasks:
                # Three-way merge
                local_task = existing_tasks[0]
                last_synced = self._last_synced_state.get(task_id, {})
                
                merged = self.merge_task_properties(
                    last_synced=last_synced,
                    current_local=local_task,
                    current_asana=normalized
                )
                
                # Update local task with merged properties
                if not self.dry_run:
                    await self.parquet_client.update_tasks(
                        filters={"task_id": task_id},
                        updates=merged
                    )
                updated_count += 1
            else:
                # Add new task
                if not self.dry_run:
                    await self.parquet_client.add_task(normalized)
                new_count += 1
        
        return updated_count, new_count
    
    async def sync_local_to_workspace(
        self,
        client: AsanaClientWrapper,
        workspace_gid: str,
        workspace_name: str
    ) -> Tuple[int, int]:
        """Sync local tasks to Asana workspace."""
        # Fetch all Asana-backed tasks
        filters = {}
        if workspace_name == 'source':
            filters["asana_source_gid"] = {"$ne": None}
        else:
            filters["asana_target_gid"] = {"$ne": None}
        
        local_tasks = await self.parquet_client.read_tasks(filters=filters)
        
        if not local_tasks:
            return 0, 0
        
        updated_count = 0
        created_count = 0
        
        for task in local_tasks:
            task_id = task['task_id']
            task_gid = task.get('asana_target_gid' if workspace_name == 'target' else 'asana_source_gid')
            
            if task_gid:
                # Task exists - check if we need to update
                try:
                    # Fetch current Asana state
                    opts = {"opt_fields": "modified_at,name,notes,due_on,completed"}
                    asana_task = client._with_retry(client.tasks.get_task, task_gid, opts)
                    
                    # Three-way merge
                    last_synced = self._last_synced_state.get(task_id, {})
                    normalized_asana = self.normalize_asana_task(asana_task, workspace_name)
                    
                    merged = self.merge_task_properties(
                        last_synced=last_synced,
                        current_local=task,
                        current_asana=normalized_asana
                    )
                    
                    # Check if we need to update Asana
                    needs_update = False
                    for prop in ['title', 'description', 'due_date', 'status']:
                        if merged.get(prop) != normalized_asana.get(prop):
                            needs_update = True
                            break
                    
                    if needs_update and not self.dry_run:
                        # Update Asana task
                        update_data = {
                            "name": merged.get('title'),
                            "notes": merged.get('description'),
                            "due_on": str(merged.get('due_date')) if merged.get('due_date') else None,
                            "completed": merged.get('status') == 'completed'
                        }
                        update_data = {k: v for k, v in update_data.items() if v is not None}
                        
                        client._with_retry(
                            client.tasks.update_task,
                            {"data": update_data},
                            task_gid,
                            {}
                        )
                        updated_count += 1
                
                except Exception as e:
                    print(f"Warning: Could not sync task {task_gid}: {e}", file=sys.stderr)
            else:
                # Task doesn't exist in this workspace - create it
                if task.get('title'):
                    try:
                        task_data = {
                            "workspace": workspace_gid,
                            "name": task.get('title'),
                            "notes": task.get('description', ''),
                            "due_on": str(task.get('due_date')) if task.get('due_date') else None,
                            "completed": task.get('status') == 'completed'
                        }
                        task_data = {k: v for k, v in task_data.items() if v is not None}
                        
                        if not self.dry_run:
                            new_task = client._with_retry(
                                client.tasks.create_task,
                                {"data": task_data},
                                {}
                            )
                            created_count += 1
                            
                            # Update local with new GID
                            new_gid = new_task.get('gid')
                            gid_field = 'asana_target_gid' if workspace_name == 'target' else 'asana_source_gid'
                            await self.parquet_client.update_tasks(
                                filters={"task_id": task_id},
                                updates={gid_field: new_gid}
                            )
                    
                    except Exception as e:
                        print(f"Warning: Could not create task in {workspace_name}: {e}", file=sys.stderr)
        
        return updated_count, created_count
    
    async def fetch_tasks_modified_since(
        self,
        client: AsanaClientWrapper,
        workspace_gid: str,
        workspace_name: str,
        since: Optional[datetime] = None
    ) -> List[Dict]:
        """Fetch tasks modified since timestamp."""
        # For simplicity, fetch all incomplete tasks
        # In production, this should use the sync_state to track last sync timestamp
        opt_fields = [
            "gid", "name", "notes", "html_notes", "completed", "completed_at",
            "due_on", "start_on", "created_at", "modified_at",
            "assignee", "assignee.gid", "projects", "projects.name",
            "memberships", "memberships.section.name", "tags", "tags.name"
        ]
        
        opts = {
            "workspace": workspace_gid,
            "completed_since": "now" if not since else since.isoformat(),
            "opt_fields": ",".join(opt_fields)
        }
        
        tasks = list(client._with_retry(client.tasks.get_tasks, opts))
        return tasks
    
    def normalize_asana_task(self, task_data: Dict, workspace_name: str) -> Dict:
        """Normalize Asana task data."""
        gid = task_data.get('gid', '')
        title = task_data.get('name', '')
        notes = task_data.get('notes', '') or ''
        html_notes = task_data.get('html_notes')
        completed = task_data.get('completed', False)
        
        # Parse dates
        due_on = task_data.get('due_on')
        due_date = datetime.fromisoformat(due_on).date() if due_on else None
        
        start_on = task_data.get('start_on')
        start_date = datetime.fromisoformat(start_on).date() if start_on else None
        
        completed_at = task_data.get('completed_at')
        completed_date = datetime.fromisoformat(completed_at).date() if completed_at else None
        
        modified_at_str = task_data.get('modified_at')
        updated_at = datetime.fromisoformat(modified_at_str.replace('Z', '+00:00')) if modified_at_str else None
        
        # Extract project/section info
        projects = task_data.get('projects', [])
        project_names = [p.get('name') for p in projects if p.get('name')]
        
        memberships = task_data.get('memberships', [])
        section_names = []
        for m in memberships:
            section = m.get('section')
            if section and section.get('name'):
                section_names.append(section.get('name'))
        
        # Extract assignee
        assignee = task_data.get('assignee') or {}
        assignee_gid = assignee.get('gid') if assignee else None
        
        # Extract followers
        followers = task_data.get('followers', [])
        follower_gids = [f.get('gid') for f in followers if f.get('gid')]
        
        # Status
        status = 'completed' if completed else 'pending'
        
        return {
            'task_id': gid,
            'title': title,
            'description': notes,
            'description_html': html_notes,
            'status': status,
            'due_date': due_date,
            'start_date': start_date,
            'completed_date': completed_date,
            'updated_at': updated_at,
            'project_names': '|'.join(project_names) if project_names else None,
            'section_names': '|'.join(section_names) if section_names else None,
            'assignee_gid': assignee_gid,
            'followers_gids': '|'.join(follower_gids) if follower_gids else None,
        }
    
    def merge_task_properties(
        self,
        last_synced: Dict,
        current_local: Dict,
        current_asana: Dict
    ) -> Dict:
        """
        Three-way merge of task properties.
        
        Uses last synced state to determine which changes to preserve.
        """
        merged = current_local.copy()
        
        properties_to_merge = [
            'title', 'description', 'description_html', 'status',
            'due_date', 'start_date', 'completed_date',
            'project_names', 'section_names', 'assignee_gid', 'followers_gids'
        ]
        
        for prop in properties_to_merge:
            if prop not in current_asana:
                continue
            
            last_val = last_synced.get(prop) if last_synced else None
            local_val = current_local.get(prop)
            asana_val = current_asana.get(prop)
            
            # Normalize values for comparison
            def normalize_val(val):
                if pd.isna(val) or val is None:
                    return None
                if isinstance(val, str):
                    return val.strip() if val.strip() else None
                return val
            
            last_val_norm = normalize_val(last_val)
            local_val_norm = normalize_val(local_val)
            asana_val_norm = normalize_val(asana_val)
            
            # Three-way merge logic
            if last_val_norm == local_val_norm and last_val_norm != asana_val_norm:
                # Only Asana changed - use Asana
                merged[prop] = asana_val
            elif last_val_norm == asana_val_norm and last_val_norm != local_val_norm:
                # Only Local changed - use Local (keep current)
                pass
            elif last_val_norm != local_val_norm and last_val_norm != asana_val_norm:
                # Both changed - use newer timestamp
                local_updated = current_local.get('updated_at')
                asana_modified = current_asana.get('updated_at')
                
                if asana_modified and local_updated:
                    if asana_modified > local_updated:
                        merged[prop] = asana_val
                else:
                    merged[prop] = asana_val
        
        # Update timestamp to newer value
        local_updated = current_local.get('updated_at')
        asana_modified = current_asana.get('updated_at')
        
        if asana_modified and local_updated:
            merged['updated_at'] = max(asana_modified, local_updated)
        elif asana_modified:
            merged['updated_at'] = asana_modified
        
        return merged
    
    async def load_last_synced_state(self) -> None:
        """Load last synced state from parquet MCP."""
        # Simplified: Load all tasks and store in cache
        # In production, this should be persisted separately
        tasks = await self.parquet_client.read_tasks()
        
        for task in tasks:
            task_id = task.get('task_id')
            if task_id:
                self._last_synced_state[task_id] = task
    
    async def save_last_synced_state(self) -> None:
        """Save current state as last synced state."""
        # Reload tasks and update cache
        await self.load_last_synced_state()


"""
Asana task export engine with parquet MCP integration.

Exports local tasks to Asana workspace via the parquet MCP server.
"""

import sys
import uuid
from datetime import datetime, date
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd
import requests

# Add current directory to path for imports
sys.path.insert(0, str(Path(__file__).parent))

from config import AsanaConfig
from client import AsanaClientWrapper, TimeoutError
from parquet_client import ParquetMCPClient


class AsanaExporter:
    """Export local tasks to Asana workspace via MCP."""
    
    def __init__(
        self,
        config: AsanaConfig,
        parquet_client: ParquetMCPClient,
        workspace: str = "target"
    ):
        self.config = config
        self.parquet_client = parquet_client
        self.workspace = workspace
        
        if workspace == "source":
            self.client = AsanaClientWrapper.from_config_source(config)
            self.workspace_gid = config.source_workspace_gid
        else:
            self.client = AsanaClientWrapper.from_config_target(config)
            self.workspace_gid = config.target_workspace_gid
    
    async def export_tasks(
        self,
        limit: int = 10,
        sync_log_filter: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Export local tasks to Asana workspace.
        
        Returns: Statistics about the export
        """
        # Build filters for task selection
        filters = {
            "status": {"$in": ["pending", "in_progress", "blocked"]}
        }
        
        if sync_log_filter:
            filters["sync_log"] = sync_log_filter
        
        # Fetch tasks via parquet MCP
        tasks = await self.parquet_client.read_tasks(filters=filters, limit=limit)
        
        if not tasks:
            return {
                "success": True,
                "workspace": self.workspace,
                "processed": 0,
                "created": 0,
                "updated": 0,
                "failed": 0
            }
        
        # Sort by urgency and priority
        urgency_order = {"today": 0, "this_week": 1, "soon": 2, "backlog": 3}
        priority_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
        
        sorted_tasks = sorted(
            tasks,
            key=lambda t: (
                urgency_order.get(t.get('urgency', 'backlog'), 99),
                priority_order.get(t.get('priority', 'low'), 99)
            )
        )
        
        created_count = 0
        updated_count = 0
        failed_count = 0
        
        for task in sorted_tasks:
            try:
                task_id = task['task_id']
                title = task.get('title', 'Untitled Task')
                
                # Check if task already exists in target workspace
                existing_target_gid = task.get('asana_target_gid') if self.workspace == 'target' else task.get('asana_source_gid')
                
                # Check for duplicates by title
                if not existing_target_gid:
                    existing_target_gid = await self.find_duplicate_by_title(title)
                
                # Prepare task data
                task_data = {
                    "name": title,
                    "notes": task.get('description', ''),
                    "due_on": str(task.get('due_date')) if task.get('due_date') else None,
                    "completed": task.get('status') == 'completed'
                }
                
                # Remove None values
                task_data = {k: v for k, v in task_data.items() if v is not None}
                
                if existing_target_gid:
                    # Update existing task
                    self.client._with_retry(
                        self.client.tasks.update_task,
                        {"data": task_data},
                        existing_target_gid,
                        {}
                    )
                    updated_count += 1
                    
                    # Update sync log
                    await self.parquet_client.update_tasks(
                        filters={"task_id": task_id},
                        updates={
                            "sync_log": "exported",
                            "sync_datetime": datetime.now()
                        }
                    )
                else:
                    # Create new task
                    task_data["workspace"] = self.workspace_gid
                    
                    # Get assignee if available
                    if self.config.fallback_assignee_email:
                        assignee_gid = await self.get_assignee_gid()
                        if assignee_gid:
                            task_data["assignee"] = assignee_gid
                    
                    new_task = self.client._with_retry(
                        self.client.tasks.create_task,
                        {"data": task_data},
                        {}
                    )
                    created_count += 1
                    
                    new_gid = new_task.get('gid')
                    
                    # Update task with new GID and sync log
                    gid_field = 'asana_target_gid' if self.workspace == 'target' else 'asana_source_gid'
                    await self.parquet_client.update_tasks(
                        filters={"task_id": task_id},
                        updates={
                            gid_field: new_gid,
                            "sync_log": "exported",
                            "sync_datetime": datetime.now()
                        }
                    )
            
            except Exception as e:
                failed_count += 1
                print(f"Error exporting task {task.get('task_id')}: {e}", file=sys.stderr)
                
                # Update sync log with error
                try:
                    await self.parquet_client.update_tasks(
                        filters={"task_id": task['task_id']},
                        updates={
                            "sync_log": f"failed:{type(e).__name__}",
                            "sync_datetime": datetime.now()
                        }
                    )
                except:
                    pass
        
        return {
            "success": True,
            "workspace": self.workspace,
            "processed": len(sorted_tasks),
            "created": created_count,
            "updated": updated_count,
            "failed": failed_count
        }
    
    async def find_duplicate_by_title(self, title: str) -> Optional[str]:
        """Check for existing task with same title in workspace."""
        try:
            headers = {"Authorization": f"Bearer {self.client._pat}"}
            me_url = "https://app.asana.com/api/1.0/users/me"
            me_response = requests.get(me_url, headers=headers, params={"opt_fields": "gid"}, timeout=5)
            
            if me_response.status_code == 200:
                user_gid = me_response.json().get("data", {}).get("gid")
                
                # Search assigned tasks
                search_opts = {
                    "assignee": user_gid,
                    "workspace": self.workspace_gid,
                    "opt_fields": "gid,name",
                    "limit": 100
                }
                
                search_tasks = list(self.client._with_retry(
                    self.client.tasks.get_tasks,
                    search_opts
                ))
                
                # Check for exact title match
                for task in search_tasks:
                    if task.get('name') == title:
                        return task.get('gid')
        
        except Exception:
            pass
        
        return None
    
    async def get_assignee_gid(self) -> Optional[str]:
        """Get assignee GID from config."""
        try:
            headers = {"Authorization": f"Bearer {self.client._pat}"}
            url = "https://app.asana.com/api/1.0/users/me"
            params = {"opt_fields": "gid"}
            response = requests.get(url, headers=headers, params=params, timeout=30)
            
            if response.status_code == 200:
                return response.json().get("data", {}).get("gid")
        except Exception:
            pass
        
        return None


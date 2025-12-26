"""
Asana task import engine with parquet MCP integration.

Imports tasks from Asana workspace to local parquet via the parquet MCP server.
"""

import re
import sys
import uuid
from datetime import datetime, date
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

# Add current directory to path for imports
sys.path.insert(0, str(Path(__file__).parent))

from config import AsanaConfig
from client import AsanaClientWrapper
from parquet_client import ParquetMCPClient


class AsanaImporter:
    """Import tasks from Asana to local parquet via MCP."""
    
    def __init__(
        self,
        config: AsanaConfig,
        parquet_client: ParquetMCPClient,
        workspace: str = "source"
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
        
        # Domain classification keywords
        self.domain_keywords = {
            'finance': ['tax', 'fbar', 'portfolio', 'investment', 'bank', 'crypto', 'money', 
                       'payment', 'invoice', 'transfer', 'schwab', 'coinbase', 'kraken', 
                       'ibercaja', 'capital one', 'wealth', 'estate', 'notary', 'liability',
                       'insurance', 'audit', 'filing', 'cadastre', 'modelo', 'dividend'],
            'admin': ['utility', 'bill', 'subscription', 'document', 'certificate', 'form',
                     'registration', 'license', 'passport', 'visa', 'immigration', 'movistar',
                     'aigües', 'electricity', 'water', 'gas', 'internet', 'phone'],
            'health': ['workout', 'exercise', 'doctor', 'medical', 'health', 'fitness', 'gym',
                      'yoga', 'diet', 'nutrition', 'sleep', 'checkup', 'appointment', 'vet',
                      'therapy', 'mental', 'physical'],
            'work': ['startup', 'neotoma', 'project', 'meeting', 'presentation', 'deadline',
                    'contract', 'client', 'team', 'hire', 'product', 'launch', 'development'],
            'social': ['family', 'friend', 'gift', 'party', 'wedding', 'birthday', 'visit',
                      'call', 'dinner', 'lunch', 'trip', 'vacation', 'travel'],
        }
        
        # High-priority keywords
        self.high_priority_keywords = {
            'critical': ['tax filing', 'fbar', 'audit', 'legal deadline', 'notary', 'cadastre',
                        'modelo 900', 'lawsuit', 'eviction', 'emergency', 'urgent legal'],
            'high': ['tax', 'insurance', 'license renewal', 'visa', 'passport', 'certificate',
                    'bank transfer', 'portfolio rebalance', 'property', 'contract', 'compliance',
                    'government', 'registration', 'filing deadline'],
            'medium': ['payment', 'subscription', 'utility', 'repair', 'maintenance', 'appointment',
                      'schedule', 'organize', 'research', 'purchase'],
        }
    
    async def import_tasks(
        self,
        only_incomplete: bool = False,
        assignee_gid: Optional[str] = None,
        max_tasks: Optional[int] = None,
        recalculate: bool = False,
        include_archived: bool = True
    ) -> Dict[str, Any]:
        """
        Import tasks from Asana workspace.
        
        Returns: Statistics about the import
        """
        # Fetch tasks from Asana
        tasks = await self.fetch_tasks_from_asana(
            only_incomplete=only_incomplete,
            assignee_gid=assignee_gid,
            max_tasks=max_tasks,
            include_archived=include_archived
        )
        
        if not tasks:
            return {
                "success": True,
                "workspace": self.workspace,
                "fetched": 0,
                "updated": 0,
                "new": 0
            }
        
        # Normalize tasks
        normalized_tasks = []
        for task_data in tasks:
            normalized = self.normalize_asana_task(task_data, recalculate)
            normalized_tasks.append(normalized)
        
        # Merge with existing tasks via parquet MCP
        updated_count = 0
        new_count = 0
        
        for task in normalized_tasks:
            task_id = task['task_id']
            
            # Check if task exists
            existing_tasks = await self.parquet_client.read_tasks(
                filters={"task_id": task_id},
                limit=1
            )
            
            if existing_tasks:
                # Update existing task
                await self.parquet_client.update_tasks(
                    filters={"task_id": task_id},
                    updates=task
                )
                updated_count += 1
            else:
                # Add new task
                await self.parquet_client.add_task(task)
                new_count += 1
        
        return {
            "success": True,
            "workspace": self.workspace,
            "fetched": len(tasks),
            "updated": updated_count,
            "new": new_count
        }
    
    async def fetch_tasks_from_asana(
        self,
        only_incomplete: bool = False,
        assignee_gid: Optional[str] = None,
        max_tasks: Optional[int] = None,
        include_archived: bool = True
    ) -> List[Dict]:
        """Fetch tasks from Asana workspace."""
        tasks = []
        task_gids = set()
        
        opt_fields = [
            "gid", "name", "notes", "html_notes", "completed", "completed_at", "due_on",
            "start_on", "created_at", "modified_at", "assignee", "assignee.gid", "assignee.name",
            "projects", "projects.name", "memberships", "memberships.section.name",
            "assignee_section", "assignee_section.name", "tags", "tags.name",
            "permalink_url", "followers", "followers.gid", "followers.name"
        ]
        
        # Fetch from projects
        projects_opts = {
            "workspace": self.workspace_gid,
            "archived": False
        }
        
        projects = list(self.client._with_retry(
            self.client.projects.get_projects,
            projects_opts
        ))
        
        if include_archived:
            archived_projects_opts = {
                "workspace": self.workspace_gid,
                "archived": True
            }
            archived_projects = list(self.client._with_retry(
                self.client.projects.get_projects,
                archived_projects_opts
            ))
            projects.extend(archived_projects)
        
        # Fetch tasks from each project
        for project in projects:
            project_gid = project.get('gid')
            if not project_gid:
                continue
            
            tasks_opts = {
                "project": project_gid,
                "opt_fields": ",".join(opt_fields)
            }
            
            project_tasks = list(self.client._with_retry(
                self.client.tasks.get_tasks,
                tasks_opts
            ))
            
            for task_data in project_tasks:
                task_gid = task_data.get('gid')
                
                if task_gid in task_gids:
                    continue  # Deduplicate
                
                # Apply filters
                if only_incomplete and task_data.get('completed'):
                    continue
                
                if assignee_gid and task_data.get('assignee', {}).get('gid') != assignee_gid:
                    continue
                
                tasks.append(task_data)
                task_gids.add(task_gid)
                
                if max_tasks and len(tasks) >= max_tasks:
                    return tasks
        
        if max_tasks and len(tasks) >= max_tasks:
            return tasks
        
        # Fetch standalone tasks (assigned to user but not in projects)
        try:
            headers = {"Authorization": f"Bearer {self.client._pat}"}
            me_url = "https://app.asana.com/api/1.0/users/me"
            me_response = requests.get(me_url, headers=headers, params={"opt_fields": "gid"}, timeout=30)
            
            if me_response.status_code == 200:
                current_user_gid = me_response.json().get("data", {}).get("gid")
                
                if current_user_gid:
                    standalone_opts = {
                        "assignee": current_user_gid,
                        "workspace": self.workspace_gid,
                        "opt_fields": ",".join(opt_fields)
                    }
                    
                    standalone_tasks = list(self.client._with_retry(
                        self.client.tasks.get_tasks,
                        standalone_opts
                    ))
                    
                    for task_data in standalone_tasks:
                        task_gid = task_data.get('gid')
                        
                        if task_gid in task_gids:
                            continue  # Deduplicate
                        
                        # Only include tasks not in projects
                        if task_data.get('projects'):
                            continue
                        
                        # Apply filters
                        if only_incomplete and task_data.get('completed'):
                            continue
                        
                        if assignee_gid and task_data.get('assignee', {}).get('gid') != assignee_gid:
                            continue
                        
                        tasks.append(task_data)
                        task_gids.add(task_gid)
                        
                        if max_tasks and len(tasks) >= max_tasks:
                            return tasks
        except Exception as e:
            print(f"Warning: Could not fetch standalone tasks: {e}", file=sys.stderr)
        
        return tasks
    
    def normalize_asana_task(self, task_data: Dict, recalculate: bool = False) -> Dict:
        """Normalize Asana task to tasks schema."""
        gid = task_data.get('gid', '')
        title = task_data.get('name', '')
        notes = task_data.get('notes', '') or ''
        html_notes = task_data.get('html_notes')
        completed = task_data.get('completed', False)
        
        # Generate title from description if empty
        if not title or title.strip() == '':
            if notes and notes.strip():
                first_sentence = notes.split('.')[0].strip()
                title = first_sentence[:60] if first_sentence else 'Untitled Task'
            else:
                title = 'Untitled Task'
        
        # Parse dates
        due_on = task_data.get('due_on')
        due_date = datetime.fromisoformat(due_on).date() if due_on else None
        
        start_on = task_data.get('start_on')
        start_date = datetime.fromisoformat(start_on).date() if start_on else None
        
        completed_at = task_data.get('completed_at')
        completed_date = datetime.fromisoformat(completed_at).date() if completed_at else None
        
        created_at_str = task_data.get('created_at')
        created_at = datetime.fromisoformat(created_at_str.replace('Z', '+00:00')) if created_at_str else None
        
        modified_at_str = task_data.get('modified_at')
        updated_at = datetime.fromisoformat(modified_at_str.replace('Z', '+00:00')) if modified_at_str else None
        
        # Extract tags
        tags = [tag.get('name', '') for tag in task_data.get('tags', [])]
        
        # Extract project/section info
        projects = task_data.get('projects', [])
        project_names = [p.get('name') for p in projects if p.get('name')]
        
        # Project sections from memberships
        memberships = task_data.get('memberships', [])
        section_names = []
        for m in memberships:
            section = m.get('section')
            if section and section.get('name'):
                section_names.append(section.get('name'))
        
        # Extract assignee
        assignee = task_data.get('assignee') or {}
        assignee_gid = assignee.get('gid') if assignee else None
        assignee_name = assignee.get('name') if assignee else None
        
        # My Tasks section
        my_tasks_section_names = []
        assignee_section = task_data.get("assignee_section") or {}
        if assignee_section and assignee_section.get("name"):
            my_tasks_section_names.append(assignee_section.get("name"))
        
        # Extract followers
        followers = task_data.get('followers', [])
        follower_gids = [f.get('gid') for f in followers if f.get('gid')]
        
        # Permalink URL
        permalink_url = task_data.get('permalink_url')
        
        # Compute classifications
        domain = self._classify_domain(title, notes)
        urgency = self._compute_urgency(due_date, title, tags)
        priority = self._compute_priority(title, notes, tags)
        
        # Determine status
        if completed:
            status = 'completed'
        else:
            status = 'pending'
        
        return {
            'task_id': gid,
            'title': title,
            'description': notes,
            'description_html': html_notes,
            'domain': domain,
            'status': status,
            'priority': priority,
            'urgency': urgency,
            'due_date': due_date,
            'start_date': start_date,
            'completed_date': completed_date,
            'updated_at': updated_at,
            'project_names': '|'.join(project_names) if project_names else None,
            'section_names': '|'.join(section_names) if section_names else None,
            'my_tasks_section_names': '|'.join(my_tasks_section_names) if my_tasks_section_names else None,
            'assignee_gid': assignee_gid,
            'assignee_name': assignee_name,
            'followers_gids': '|'.join(follower_gids) if follower_gids else None,
            'permalink_url': permalink_url,
            'asana_source_gid': gid if self.workspace == 'source' else None,
            'asana_target_gid': gid if self.workspace == 'target' else None,
            'asana_workspace': self.workspace_gid,
            'import_date': date.today(),
            'import_source_file': f'asana_{self.workspace}_api'
        }
    
    def _classify_domain(self, title: str, notes: str) -> str:
        """Classify task domain based on keywords."""
        text = f"{title} {notes}".lower()
        
        for domain, keywords in self.domain_keywords.items():
            if any(keyword in text for keyword in keywords):
                return domain
        
        return 'other'
    
    def _compute_urgency(self, due_date: Optional[date], title: str, tags: List[str]) -> str:
        """Compute task urgency based on due date and keywords."""
        if not due_date:
            return 'backlog'
        
        today = date.today()
        days_until = (due_date - today).days
        
        if days_until < 0:
            return 'overdue'
        elif days_until == 0:
            return 'today'
        elif days_until <= 7:
            return 'this_week'
        elif days_until <= 30:
            return 'soon'
        else:
            return 'backlog'
    
    def _compute_priority(self, title: str, notes: str, tags: List[str]) -> str:
        """Compute task priority based on keywords."""
        text = f"{title} {notes}".lower()
        tag_text = " ".join(tags).lower()
        
        # Check for critical keywords
        for keyword in self.high_priority_keywords['critical']:
            if keyword in text or keyword in tag_text:
                return 'critical'
        
        # Check for high keywords
        for keyword in self.high_priority_keywords['high']:
            if keyword in text or keyword in tag_text:
                return 'high'
        
        # Check for medium keywords
        for keyword in self.high_priority_keywords['medium']:
            if keyword in text or keyword in tag_text:
                return 'medium'
        
        return 'low'
    
    async def import_comments(self, task_gids: List[str]) -> Dict[str, Any]:
        """Import comments for specific tasks."""
        comments_imported = 0
        
        for task_gid in task_gids:
            try:
                # Fetch comments from Asana
                stories = self.client._with_retry(
                    self.client.stories.get_stories_for_task,
                    task_gid,
                    {"opt_fields": "type,text,html_text,created_at,created_by,created_by.name"}
                )
                
                for story in stories:
                    if story.get('type') != 'comment':
                        continue
                    
                    comment_data = {
                        'comment_id': str(uuid.uuid4())[:16],
                        'task_id': task_gid,
                        'asana_story_gid': story.get('gid'),
                        'comment_text': story.get('text', ''),
                        'comment_html': story.get('html_text'),
                        'created_by_name': story.get('created_by', {}).get('name'),
                        'created_at': story.get('created_at'),
                        'asana_workspace': self.workspace_gid,
                        'import_date': date.today(),
                        'import_source_file': f'asana_{self.workspace}_api'
                    }
                    
                    # Upsert comment via parquet MCP
                    await self.parquet_client.upsert_comment(
                        filters={"asana_story_gid": story.get('gid')},
                        record=comment_data
                    )
                    comments_imported += 1
            
            except Exception as e:
                print(f"Warning: Could not import comments for task {task_gid}: {e}", file=sys.stderr)
        
        return {
            "success": True,
            "workspace": self.workspace,
            "task_count": len(task_gids),
            "comments_imported": comments_imported
        }
    
    async def import_metadata(self, task_gids: List[str], metadata_types: List[str]) -> Dict[str, Any]:
        """Import task metadata (custom fields, dependencies, stories)."""
        stats = {
            "custom_fields": 0,
            "dependencies": 0,
            "stories": 0
        }
        
        for task_gid in task_gids:
            try:
                # Fetch full task data
                opts = {
                    "opt_fields": "custom_fields,custom_fields.gid,custom_fields.name,custom_fields.type,"
                                  "custom_fields.text_value,custom_fields.number_value,custom_fields.enum_value,"
                                  "dependencies,dependencies.gid"
                }
                task_data = self.client._with_retry(self.client.tasks.get_task, task_gid, opts)
                
                # Import custom fields
                if "custom_fields" in metadata_types:
                    custom_fields = task_data.get('custom_fields', [])
                    for cf in custom_fields:
                        cf_data = {
                            'custom_field_id': str(uuid.uuid4())[:16],
                            'task_id': task_gid,
                            'asana_task_gid': task_gid,
                            'asana_custom_field_gid': cf.get('gid'),
                            'asana_workspace': self.workspace_gid,
                            'custom_field_name': cf.get('name'),
                            'custom_field_type': cf.get('type'),
                            'text_value': cf.get('text_value'),
                            'number_value': cf.get('number_value'),
                            'enum_value': cf.get('enum_value', {}).get('gid') if cf.get('enum_value') else None,
                            'import_date': date.today(),
                            'import_source_file': f'asana_{self.workspace}_api'
                        }
                        
                        await self.parquet_client.upsert_custom_field(
                            filters={"asana_custom_field_gid": cf.get('gid'), "asana_task_gid": task_gid},
                            record=cf_data
                        )
                        stats["custom_fields"] += 1
                
                # Import dependencies
                if "dependencies" in metadata_types:
                    dependencies = task_data.get('dependencies', [])
                    for dep in dependencies:
                        dep_data = {
                            'dependency_id': str(uuid.uuid4())[:16],
                            'task_id': task_gid,
                            'depends_on_task_id': dep.get('gid'),
                            'asana_workspace': self.workspace_gid,
                            'import_date': date.today(),
                            'import_source_file': f'asana_{self.workspace}_api'
                        }
                        
                        await self.parquet_client.upsert_dependency(
                            filters={"task_id": task_gid, "depends_on_task_id": dep.get('gid')},
                            record=dep_data
                        )
                        stats["dependencies"] += 1
                
                # Import stories
                if "stories" in metadata_types:
                    stories = self.client._with_retry(
                        self.client.stories.get_stories_for_task,
                        task_gid,
                        {"opt_fields": "type,text,html_text,created_at,created_by,created_by.name"}
                    )
                    
                    for story in stories:
                        story_data = {
                            'story_id': str(uuid.uuid4())[:16],
                            'task_id': task_gid,
                            'asana_story_gid': story.get('gid'),
                            'story_type': story.get('type'),
                            'story_text': story.get('text'),
                            'story_html': story.get('html_text'),
                            'created_by_name': story.get('created_by', {}).get('name'),
                            'created_at': story.get('created_at'),
                            'asana_workspace': self.workspace_gid,
                            'import_date': date.today(),
                            'import_source_file': f'asana_{self.workspace}_api'
                        }
                        
                        await self.parquet_client.upsert_story(
                            filters={"asana_story_gid": story.get('gid')},
                            record=story_data
                        )
                        stats["stories"] += 1
            
            except Exception as e:
                print(f"Warning: Could not import metadata for task {task_gid}: {e}", file=sys.stderr)
        
        return {
            "success": True,
            "workspace": self.workspace,
            "task_count": len(task_gids),
            **stats
        }


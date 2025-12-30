"""
Test workspace setup and teardown utilities.

Provides fixtures for managing test workspace configuration and cleanup.
"""

import os
from typing import Dict, Optional


class TestWorkspaceConfig:
    """Configuration for test workspaces."""
    
    def __init__(
        self,
        source_pat: Optional[str] = None,
        target_pat: Optional[str] = None,
        source_workspace_gid: Optional[str] = None,
        target_workspace_gid: Optional[str] = None,
    ):
        # Load from environment if not provided
        self.source_pat = source_pat or os.getenv("TEST_ASANA_SOURCE_PAT")
        self.target_pat = target_pat or os.getenv("TEST_ASANA_TARGET_PAT")
        self.source_workspace_gid = source_workspace_gid or os.getenv("TEST_SOURCE_WORKSPACE_GID")
        self.target_workspace_gid = target_workspace_gid or os.getenv("TEST_TARGET_WORKSPACE_GID")
    
    def is_configured(self) -> bool:
        """Check if test workspaces are configured."""
        return all([
            self.source_pat,
            self.target_pat,
            self.source_workspace_gid,
            self.target_workspace_gid,
        ])
    
    def to_env_dict(self) -> Dict[str, str]:
        """Convert to environment variables dict."""
        return {
            "ASANA_SOURCE_PAT": self.source_pat or "",
            "ASANA_TARGET_PAT": self.target_pat or "",
            "SOURCE_WORKSPACE_GID": self.source_workspace_gid or "",
            "TARGET_WORKSPACE_GID": self.target_workspace_gid or "",
        }


def get_test_workspace_config() -> TestWorkspaceConfig:
    """Get test workspace configuration."""
    return TestWorkspaceConfig()


def skip_if_no_test_workspaces():
    """Decorator to skip tests if test workspaces are not configured."""
    import pytest
    
    config = get_test_workspace_config()
    return pytest.mark.skipif(
        not config.is_configured(),
        reason="Test workspaces not configured. Set TEST_ASANA_SOURCE_PAT, TEST_ASANA_TARGET_PAT, "
               "TEST_SOURCE_WORKSPACE_GID, and TEST_TARGET_WORKSPACE_GID environment variables."
    )


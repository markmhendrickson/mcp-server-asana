"""
Pytest configuration and fixtures for Asana MCP tests.

Provides shared fixtures for test workspace configuration, AsanaConfig,
ParquetMCPClient, test data cleanup, and task generators.
"""

import asyncio
import os
import sys
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import AsyncMock, MagicMock

import pytest

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from config import AsanaConfig
from parquet_client import ParquetMCPClient
from tests.fixtures.test_tasks import (
    generate_all_permutations,
    generate_basic_task,
    generate_full_property_task,
)
from tests.fixtures.test_workspaces import (
    TestWorkspaceConfig,
    get_test_workspace_config,
)


# Test configuration
@pytest.fixture(scope="session")
def test_workspace_config() -> TestWorkspaceConfig:
    """Get test workspace configuration."""
    return get_test_workspace_config()


@pytest.fixture(scope="session")
def integration_tests_enabled(test_workspace_config: TestWorkspaceConfig) -> bool:
    """Check if integration tests are enabled."""
    return test_workspace_config.is_configured()


# AsanaConfig fixtures
@pytest.fixture
def mock_asana_config(test_workspace_config: TestWorkspaceConfig) -> AsanaConfig:
    """Create mock AsanaConfig for unit tests."""
    config = MagicMock(spec=AsanaConfig)
    config.source_pat = test_workspace_config.source_pat or "test_source_pat"
    config.target_pat = test_workspace_config.target_pat or "test_target_pat"
    config.source_workspace_gid = test_workspace_config.source_workspace_gid or "test_source_gid"
    config.target_workspace_gid = test_workspace_config.target_workspace_gid or "test_target_gid"
    config.fallback_assignee_email = None
    return config


@pytest.fixture
def real_asana_config(test_workspace_config: TestWorkspaceConfig) -> AsanaConfig:
    """Create real AsanaConfig for integration tests."""
    if not test_workspace_config.is_configured():
        pytest.skip("Test workspaces not configured")
    
    # Set environment variables
    for key, value in test_workspace_config.to_env_dict().items():
        os.environ[key] = value
    
    return AsanaConfig.from_env()


# ParquetMCPClient fixtures
@pytest.fixture
def mock_parquet_client() -> ParquetMCPClient:
    """Create mock ParquetMCPClient for unit tests."""
    client = AsyncMock(spec=ParquetMCPClient)
    
    # Mock common methods
    client.read_tasks = AsyncMock(return_value=[])
    client.add_task = AsyncMock(return_value={"success": True})
    client.update_tasks = AsyncMock(return_value={"success": True, "updated": 0})
    client.upsert_task = AsyncMock(return_value={"success": True, "action": "created"})
    
    client.read_comments = AsyncMock(return_value=[])
    client.upsert_comment = AsyncMock(return_value={"success": True})
    
    client.read_custom_fields = AsyncMock(return_value=[])
    client.upsert_custom_field = AsyncMock(return_value={"success": True})
    
    client.read_dependencies = AsyncMock(return_value=[])
    client.upsert_dependency = AsyncMock(return_value={"success": True})
    
    client.read_stories = AsyncMock(return_value=[])
    client.upsert_story = AsyncMock(return_value={"success": True})
    
    client.read_attachments = AsyncMock(return_value=[])
    client.upsert_attachment = AsyncMock(return_value={"success": True})
    
    return client


@pytest.fixture
def real_parquet_client() -> ParquetMCPClient:
    """Create real ParquetMCPClient for integration tests."""
    # Ensure PARQUET_DATA_DIR is set for tests
    if "PARQUET_DATA_DIR" not in os.environ:
        # Use test data directory
        test_data_dir = Path(__file__).parent / "test_data"
        test_data_dir.mkdir(exist_ok=True)
        os.environ["PARQUET_DATA_DIR"] = str(test_data_dir)
    
    return ParquetMCPClient()


# Task data fixtures
@pytest.fixture
def basic_task() -> Dict[str, Any]:
    """Generate a basic test task."""
    return generate_basic_task()


@pytest.fixture
def full_property_task() -> Dict[str, Any]:
    """Generate a task with all properties."""
    return generate_full_property_task()


@pytest.fixture
def all_task_permutations() -> List[Dict[str, Any]]:
    """Generate all task permutations for comprehensive testing."""
    return generate_all_permutations()


# Cleanup fixtures
@pytest.fixture(autouse=True)
async def cleanup_test_data(request):
    """Cleanup test data after each test."""
    # Setup: nothing to do before test
    yield
    
    # Teardown: cleanup after test
    # This will be implemented based on specific test needs
    # For now, just ensure we're in a clean state
    pass


# Event loop fixture for async tests
@pytest.fixture(scope="session")
def event_loop():
    """Create event loop for async tests."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


# Test markers
def pytest_configure(config):
    """Register custom markers."""
    config.addinivalue_line(
        "markers", "unit: Unit tests (no external dependencies)"
    )
    config.addinivalue_line(
        "markers", "integration: Integration tests (require test workspaces)"
    )
    config.addinivalue_line(
        "markers", "premium: Premium feature tests (may fail based on plan)"
    )
    config.addinivalue_line(
        "markers", "slow: Slow-running tests"
    )


# Collection hooks
def pytest_collection_modifyitems(config, items):
    """Modify test collection to skip integration tests if not configured."""
    if not get_test_workspace_config().is_configured():
        skip_integration = pytest.mark.skip(reason="Test workspaces not configured")
        for item in items:
            if "integration" in item.keywords:
                item.add_marker(skip_integration)


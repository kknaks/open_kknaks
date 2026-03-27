"""Shared test fixtures."""

import pytest

from open_kknaks.task import Task


@pytest.fixture
def sample_task() -> Task:
    """Basic Task object for testing."""
    return Task(prompt="test prompt", queue="default")

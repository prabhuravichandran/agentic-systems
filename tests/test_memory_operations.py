"""Tests for memory operations."""
import pytest
from src.memory_store import MemoryStore
import tempfile
import os

@pytest.fixture
def temp_db():
    """Create a temporary database for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield tmpdir

@pytest.fixture
def memory_store(temp_db):
    """Create a MemoryStore instance with temp database."""
    return MemoryStore(db_path=temp_db)

def test_save_memory(memory_store):
    """Test saving a memory."""
    content = "Test memory content"
    category = "test"
    memory_id = memory_store.save_memory(content, category)

    assert memory_id is not None
    assert isinstance(memory_id, str)

def test_recall_memory(memory_store):
    """Test recalling memories."""
    # Save some test memories
    memory_store.save_memory("Python is great for AI", "programming")
    memory_store.save_memory("ChromaDB uses embeddings", "database")

    # Recall relevant memories
    results = memory_store.recall_memory("Python programming", n_results=2)

    assert len(results) > 0
    assert "Python" in results[0]["content"]

def test_recall_memory_no_results(memory_store):
    """Test recalling when no memories match."""
    results = memory_store.recall_memory("nonexistent topic")
    assert len(results) == 0

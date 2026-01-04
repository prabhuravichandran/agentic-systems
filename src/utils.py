"""Utility functions for the MCP Memory Server."""
import os
from typing import Optional

def ensure_db_directory(db_path: str = "./chroma_db") -> None:
    """Ensure the ChromaDB directory exists."""
    os.makedirs(db_path, exist_ok=True)

def get_db_path() -> str:
    """Get the database path, defaulting to ./chroma_db."""
    return os.getenv("CHROMA_DB_PATH", "./chroma_db")

def format_memory_result(memories: list) -> str:
    """Format a list of memories into a readable string."""
    if not memories:
        return "No relevant memories found."

    formatted = []
    for memory in memories:
        formatted.append(
            f"[{memory['timestamp']}] ({memory['category']}): {memory['content']}"
        )
    return "\n".join(formatted)

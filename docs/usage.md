# MCP Memory Server Usage Guide

## Overview

The MCP Memory Server provides persistent, semantic memory capabilities to AI agents through the Model Context Protocol (MCP). This guide covers installation, configuration, and usage patterns.

## Installation

### Prerequisites
- Python 3.10+
- uv package manager (recommended) or pip

### Install from Source
```bash
# Clone the repository
git clone https://github.com/prabhuravichandran/agentic-systems.git
cd agentic-systems

# Install dependencies
uv pip install -e .

# Or with pip
pip install -e .
```

### Install from PyPI (Future)
```bash
# Coming in v0.2.0
pip install agentic-systems
```

## Configuration

### Claude Desktop Integration

Add the following to your `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "memory": {
      "command": "uv",
      "args": ["run", "python", "/absolute/path/to/agentic-systems/src/server.py"]
    }
  }
}
```

**Important:** Use absolute paths in the configuration.

### Alternative: Direct MCP Client

For development or custom integrations:

```python
import asyncio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

async def main():
    server_params = StdioServerParameters(
        command="uv",
        args=["run", "python", "src/server.py"]
    )

    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            # Use memory tools here
```

## Memory Operations

### Saving Memories

The server provides a `save_memory` tool that stores information with semantic search capabilities:

```python
# Example: Save a technical fact
await session.call_tool("save_memory", {
    "content": "Production database is hosted at 10.0.0.5:5432 with PostgreSQL 15",
    "metadata": {
        "category": "infrastructure",
        "tags": ["database", "production", "postgresql"],
        "importance": "high"
    }
})
```

**Metadata Fields:**
- `category`: String classification (e.g., "infrastructure", "credentials", "decisions")
- `tags`: Array of searchable tags
- `importance`: "low", "medium", "high" (affects retrieval priority)

### Recalling Memories

Use the `recall_memory` tool for semantic search:

```python
# Example: Find database information
results = await session.call_tool("recall_memory", {
    "query": "production database connection details",
    "limit": 5,
    "category_filter": "infrastructure"
})
```

**Query Parameters:**
- `query`: Natural language search query
- `limit`: Maximum results to return (default: 10)
- `category_filter`: Optional category filter
- `tag_filter`: Optional tag filter

## Usage Patterns

### Development Workflow Memory

```python
# During development
await save_memory(
    content="Decided to use FastAPI for the API layer due to async support and auto-docs",
    metadata={
        "category": "decisions",
        "tags": ["architecture", "api", "fastapi"],
        "date": "2024-01-15"
    }
)

# Later in the project
results = await recall_memory("API framework decision")
```

### Code Context Preservation

```python
# Save important code decisions
await save_memory(
    content="Using Pydantic v2 for data validation - provides better performance and type safety",
    metadata={
        "category": "decisions",
        "tags": ["dependencies", "validation", "pydantic"],
        "file": "models.py"
    }
)
```

### Session Continuity

```python
# End of session
await save_memory(
    content="Currently debugging authentication flow in auth.py lines 45-67",
    metadata={
        "category": "work-in-progress",
        "tags": ["debugging", "auth", "session-state"],
        "priority": "high"
    }
)

# Next session
context = await recall_memory("current debugging task")
```

## Best Practices

### Memory Organization
1. **Use Categories:** Group related information (infrastructure, decisions, credentials)
2. **Tag Strategically:** Use consistent tags for better searchability
3. **Importance Levels:** Mark critical information as "high" priority

### Query Optimization
1. **Natural Language:** Write queries as you would ask a colleague
2. **Context Filters:** Use category/tag filters to narrow results
3. **Iterative Refinement:** Start broad, then narrow with filters

### Privacy Considerations
1. **Local Only:** All data stays on your machine
2. **No Telemetry:** No data is sent to external services
3. **Backup Regularly:** The database is in `~/.agentic-systems/chroma.db`

## Troubleshooting

### Server Won't Start
- Verify Python 3.10+ is installed
- Check that all dependencies are installed
- Ensure the path in `claude_desktop_config.json` is absolute

### Memory Not Found
- Try broader search terms
- Check spelling and terminology
- Remove filters if using category/tag filters

### Performance Issues
- Large databases may slow initial queries
- Consider using category filters for better performance
- The database is optimized for semantic search

## Advanced Configuration

### Custom Database Location
Set the `AGENTIC_SYSTEMS_DB_PATH` environment variable:

```bash
export AGENTIC_SYSTEMS_DB_PATH="/custom/path/to/database"
uv run python src/server.py
```

### Logging
Enable debug logging:

```bash
export AGENTIC_SYSTEMS_LOG_LEVEL=DEBUG
uv run python src/server.py
```

### Database Management

The server includes utilities for database operations:

```python
from src.utils import cleanup_old_memories, export_memories

# Remove memories older than 90 days
cleanup_old_memories(days=90)

# Export all memories to JSON
export_memories("backup.json")
```

## API Reference

### Tools

#### save_memory
Stores a new memory with semantic embedding.

**Parameters:**
- `content` (string): The memory content to store
- `metadata` (object, optional): Additional metadata
  - `category` (string): Classification category
  - `tags` (array): Searchable tags
  - `importance` (string): "low", "medium", "high"

**Returns:** Success confirmation

#### recall_memory
Searches for memories using semantic similarity.

**Parameters:**
- `query` (string): Search query
- `limit` (number, optional): Max results (default: 10)
- `category_filter` (string, optional): Filter by category
- `tag_filter` (string, optional): Filter by tag

**Returns:** Array of matching memories with similarity scores

## Contributing

See CONTRIBUTING.md for development guidelines and testing instructions.
"""MCP Memory Server: Long-term memory for AI agents."""
from typing import Any
import chromadb
import datetime
import uuid
import logging

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize ChromaDB (Persistent mode)
client = chromadb.PersistentClient(path="./chroma_db")
collection = client.get_or_create_collection(
    name="agent_memories",
    metadata={"description": "Long-term memory for AI agents"}
)

logger.info("MCP Memory Server initialized with ChromaDB collection")

def save_memory(content: str, category: str = "general") -> str:
    """
    Saves a piece of information to long-term memory.

    Use this when the user asks you to 'remember' something or when you make
    a significant architectural decision.

    Args:
        content: The fact or information to remember.
        category: Tag (e.g., "architecture", "credentials", "preference").
    """
    doc_id = str(uuid.uuid4())
    timestamp = datetime.datetime.now().isoformat()

    collection.add(
        documents=[content],
        metadatas=[{"category": category, "timestamp": timestamp}],
        ids=[doc_id]
    )
    logger.info(f"Saved memory {doc_id} under category '{category}'")
    return f"Saved memory ID {doc_id} under '{category}'"

def recall_memory(query: str, n_results: int = 3) -> str:
    """
    Retrieves relevant information from memory based on a semantic query.
    Call this before answering complex questions to see if you have past context.

    Args:
        query: The question to ask memory (e.g., "What is the DB port?").
        n_results: Number of memories to retrieve (default: 3).
    """
    results = collection.query(
        query_texts=[query],
        n_results=n_results
    )

    if not results['documents'] or not results['documents'][0]:
        return "No relevant memories found."

    # Format results for the LLM
    formatted_memories = []
    docs = results['documents'][0]
    metas = results['metadatas'][0] if results['metadatas'] else []
    for doc, meta in zip(docs, metas):
        formatted_memories.append(f"[{meta['timestamp']}] ({meta['category']}): {doc}")

    return "\n".join(formatted_memories)

# For MCP integration (when MCP library is available)
try:
    from mcp.server.fastmcp import FastMCP

    # Initialize FastMCP server
    mcp = FastMCP("agentic-memory")

    @mcp.tool()
    def save_memory_tool(content: str, category: str = "general") -> str:
        return save_memory(content, category)

    @mcp.tool()
    def recall_memory_tool(query: str, n_results: int = 3) -> str:
        return recall_memory(query, n_results)

    if __name__ == "__main__":
        mcp.run()

except ImportError:
    logger.warning("MCP library not available. Running in standalone mode.")
    if __name__ == "__main__":
        print("MCP Memory Server initialized. Use save_memory() and recall_memory() functions.")
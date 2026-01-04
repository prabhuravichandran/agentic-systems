"""Memory store abstraction for ChromaDB operations."""
import chromadb
from typing import List, Dict, Any
import logging

logger = logging.getLogger(__name__)

class MemoryStore:
    """Handles all ChromaDB operations for agent memory."""

    def __init__(self, db_path: str = "./chroma_db"):
        self.client = chromadb.PersistentClient(path=db_path)
        self.collection = self.client.get_or_create_collection(
            name="agent_memories",
            metadata={"description": "Long-term memory for AI agents"}
        )
        logger.info("MemoryStore initialized")

    def save_memory(self, content: str, category: str = "general") -> str:
        """Save a memory to the vector database."""
        import uuid
        import datetime

        doc_id = str(uuid.uuid4())
        timestamp = datetime.datetime.now().isoformat()

        self.collection.add(
            documents=[content],
            metadatas=[{"category": category, "timestamp": timestamp}],
            ids=[doc_id]
        )
        return doc_id

    def recall_memory(self, query: str, n_results: int = 3) -> List[Dict[str, Any]]:
        """Retrieve relevant memories based on semantic similarity."""
        results = self.collection.query(
            query_texts=[query],
            n_results=n_results
        )

        memories = []
        if results['documents'] and results['documents'][0]:
            docs = results['documents'][0]
            ids = results['ids'][0] if results['ids'] else []
            metas = results['metadatas'][0] if results['metadatas'] else []

            for doc, doc_id, meta in zip(docs, ids, metas):
                memories.append({
                    "id": doc_id,
                    "content": doc,
                    "category": meta["category"],
                    "timestamp": meta["timestamp"]
                })

        return memories

# ADR 001: Selection of ChromaDB for Local Vector Storage

**Status:** Accepted  
**Date:** 2026-01-03  
**Context:**  
The MCP Memory Server requires a vector database to store and retrieve text embeddings based on semantic similarity. The solution must:
- Run locally without external dependencies (Docker, cloud services)
- Provide out-of-the-box embedding generation
- Support metadata filtering (by category, timestamp)
- Persist data across sessions

**Options Considered:**

### 1. Postgres with pgvector
**Pros:**
- Robust, production-grade SQL database
- Strong consistency guarantees
- Advanced query capabilities

**Cons:**
- Requires PostgreSQL installation/Docker container
- Complex setup for end users (not "zero-config")
- Overkill for single-user local workflow

### 2. FAISS (Facebook AI Similarity Search)
**Pros:**
- Extremely fast (C++ implementation)
- Used by Meta in production at scale
- Supports GPU acceleration

**Cons:**
- No built-in embedding generation (requires separate model)
- Manual index serialization/loading
- No metadata filtering without custom wrapper
- Steeper learning curve

### 3. ChromaDB (Chosen)
**Pros:**
- **Zero-config:** Runs as embedded library (like SQLite)
- **Built-in embeddings:** Includes sentence-transformers by default
- **Native metadata:** First-class support for filtering by category/tags
- **Pythonic API:** Simple `add()` and `query()` methods

**Cons:**
- SQLite-based persistence has single-writer limitation
- Performance degrades beyond ~1M vectors (acceptable for our use case)
- Less mature than Postgres/FAISS

**Decision:**  
We will use **ChromaDB** in persistent mode.

**Consequences:**
- ✅ **User Experience:** Users can `pip install` and run immediately
- ✅ **Developer Velocity:** Simple API accelerates development
- ⚠️ **Concurrency:** Single-writer lock acceptable for single-user agent workflow
- ⚠️ **Scale Ceiling:** May need to migrate to pgvector if users exceed 500k memories

**Mitigation:**
- Document the 500k memory soft limit in README
- Implement singleton pattern in `MemoryManager` to prevent write conflicts
- Add telemetry to detect if users approach scale limits
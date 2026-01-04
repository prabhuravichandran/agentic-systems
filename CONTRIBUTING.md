# Contributing to Agentic Systems

## Development Environment Setup

### Prerequisites
- Python 3.10+
- [uv](https://github.com/astral-sh/uv) (fast pip replacement)
- Git

### Installation
```bash
# Clone and install
git clone https://github.com/prabhuravichandran/agentic-systems.git
cd agentic-systems
uv pip install -e ".[dev]"

# Run tests
pytest tests/ -v

# Run type checking
mypy src/
```
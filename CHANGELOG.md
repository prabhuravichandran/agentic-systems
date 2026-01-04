# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Initial project structure and documentation

### Changed
- Updated CI badge format in README.md
- Added demo placeholder section to README.md

### Fixed
- Removed unused imports from server.py and memory_store.py
- Fixed hatchling build configuration for wheel packaging

## [0.1.0] - 2024-01-15

### Added
- **MCP Memory Server**: Complete Model Context Protocol server implementation
- **Semantic Memory Storage**: ChromaDB-backed vector database for persistent memory
- **Memory Tools**: `save_memory` and `recall_memory` MCP tools
- **Metadata Support**: Category, tags, and importance level tagging for memories
- **Privacy-First Design**: 100% local execution with no external data transmission
- **Comprehensive Testing**: Full test suite with pytest and type checking
- **CI/CD Pipeline**: GitHub Actions with multi-version Python testing
- **Documentation**: Architecture diagrams, usage guides, and development setup
- **Modern Python Packaging**: Support for both pip and uv package managers

### Technical Features
- **Vector Embeddings**: Automatic text embedding for semantic search
- **SQLite Backend**: Embedded ChromaDB with SQLite for zero-configuration setup
- **Async Support**: FastAPI-based async server implementation
- **Type Safety**: Full mypy type checking and modern Python typing
- **Code Quality**: Ruff linting with automated formatting
- **Logging**: Structured logging with configurable levels

### Developer Experience
- **Hot Reload**: Development server with automatic reloading
- **Testing Framework**: Comprehensive unit tests with coverage reporting
- **Linting & Formatting**: Automated code quality checks
- **Git Hooks**: Pre-commit hooks for quality assurance
- **Documentation**: Sphinx-ready documentation structure

### Infrastructure
- **Container Ready**: Docker support for deployment
- **Cross-Platform**: macOS, Linux, and Windows support
- **Dependency Management**: Modern Python dependency management with uv
- **Build System**: Hatchling-based packaging with custom wheel configuration

### Known Limitations
- Single-user design (multi-user support planned for v0.2.0)
- Memory size limited by local storage
- No backup/restore functionality yet
- Basic semantic search (advanced RAG planned for future versions)

---

## Version History

### Pre-1.0.0 (Development)
- Project initialization and architecture design
- Core MCP protocol implementation
- ChromaDB integration and memory storage abstraction
- Testing framework setup
- CI/CD pipeline configuration
- Documentation framework

---

## Contributing to Changelog

When contributing to this project, please:
1. Add changes to the "Unreleased" section above
2. Follow the [Keep a Changelog](https://keepachangelog.com/en/1.0.0/) format
3. Use conventional commit messages for automatic changelog generation
4. Move changes to a version section when releasing

### Types of Changes
- **Added** for new features
- **Changed** for changes in existing functionality
- **Deprecated** for soon-to-be removed features
- **Removed** for now removed features
- **Fixed** for any bug fixes
- **Security** in case of vulnerabilities
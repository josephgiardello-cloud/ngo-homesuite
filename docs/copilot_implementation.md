# HomeSuite Copilot Implementation

This update adds a practical, local-first copilot foundation with RAG and tool execution.

## What is now implemented

1. Local RAG index over project files
- Module: `ngo_homesuite/ai/rag_index.py`
- Indexes Python, SQL, YAML, and docs files.
- Optional ChromaDB + Ollama embeddings when dependencies are installed.
- Fallback keyword index works without optional dependencies.

2. Tool calling foundation
- Module: `ngo_homesuite/ai/copilot_tools.py`
- Built-in tools:
  - `list_recent_donations`
  - `search_donors`
  - `organization_financial_summary`
  - `generate_report`
- Viewer role can chat read-only; action tools are only executed for admin/staff when explicitly requested.

3. Copilot service
- Module: `ngo_homesuite/ai/copilot_service.py`
- Combines:
  - PII redaction
  - Retrieval context injection
  - Tool calling loop with Ollama tool schema support
  - Optional web check (disabled by default)

4. New API endpoints
- `POST /ai/copilot/chat`
- `POST /ai/copilot/reindex` (admin-only)
- Implemented in: `ngo_homesuite/web/ai_routes.py`

5. Audit and compliance alignment
- Copilot interactions and reindex actions append events via `ngo_homesuite/db/audit_log.py`.
- PII redaction runs before retrieval/tool flow.

6. CLI reindex command
- `homesuite reindex`
- Module: `cli/ngohs_cli/copilot_index.py`

## Environment variables

- `COPILOT_ENABLED` (default `True`)
- `COPILOT_INDEX_DIR` (default `data/copilot_index`)
- `COPILOT_RAG_K` (default `6`)
- `COPILOT_ALLOW_WEB_TOOLS` (default `False`)
- `OLLAMA_EMBED_MODEL` (default `nomic-embed-text`)

## Recommended next increments

1. Add streaming endpoint for copilot responses (`/ai/copilot/stream`).
2. Add scheduled reindex job and incremental indexing.
3. Add richer action tools (backup trigger, export jobs, integrity checks with async status).
4. Add result citation rendering in web chat UI.

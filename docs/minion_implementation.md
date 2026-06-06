# HomeSuite Minion Implementation

This update adds a practical, local-first minion foundation with RAG and tool execution.

## What is now implemented

1. Local RAG index over project files
- Module: `ngo_homesuite/ai/rag_index.py`
- Indexes Python, SQL, YAML, and docs files.
- Optional ChromaDB + Ollama embeddings when dependencies are installed.
- Fallback keyword index works without optional dependencies.

2. Tool calling foundation
- Module: `ngo_homesuite/ai/minion_tools.py`
- Built-in tools:
  - `list_recent_donations`
  - `search_donors`
  - `organization_financial_summary`
  - `generate_report`
- Viewer role can chat read-only; action tools are only executed for admin/staff when explicitly requested.

3. Minion service
- Module: `ngo_homesuite/ai/minion_service.py`
- Combines:
  - PII redaction
  - Retrieval context injection
  - Tool calling loop with Ollama tool schema support
  - Optional web check (disabled by default)

4. New API endpoints
- `POST /ai/minion/chat`
- `POST /ai/minion/reindex` (admin-only)
- Implemented in: `ngo_homesuite/web/ai_routes.py`

5. Audit and compliance alignment
- Minion interactions and reindex actions append events via `ngo_homesuite/db/audit_log.py`.
- PII redaction runs before retrieval/tool flow.

6. CLI reindex command
- `homesuite reindex`
- Module: `cli/ngohs_cli/minion_index.py`

## Environment variables

- `MINION_ENABLED` (default `True`)
- `MINION_INDEX_DIR` (default `data/minion_index`)
- `MINION_RAG_K` (default `6`)
- `MINION_ALLOW_WEB_TOOLS` (default `False`)
- `OLLAMA_EMBED_MODEL` (default `nomic-embed-text`)

## Recommended next increments

1. Add streaming endpoint for minion responses (`/ai/minion/stream`).
2. Add scheduled reindex job and incremental indexing.
3. Add richer action tools (backup trigger, export jobs, integrity checks with async status).
4. Add result citation rendering in web chat UI.


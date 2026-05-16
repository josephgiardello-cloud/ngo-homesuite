from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from ngo_homesuite.config import get_runtime_settings
from ngo_homesuite.ai.pii_redact import redact_pii


@dataclass
class RAGChunk:
    source: str
    text: str


class LocalRAGIndex:
    """Local-first retrieval index.

    Uses ChromaDB + Ollama embeddings when available. Falls back to a lightweight
    JSONL keyword index when optional dependencies are missing.
    """

    def __init__(self, index_dir: str, embed_model: str = "nomic-embed-text") -> None:
        self.index_dir = Path(index_dir)
        self.index_dir.mkdir(parents=True, exist_ok=True)
        self.embed_model = embed_model
        self._mode = "keyword"
        self._keyword_path = self.index_dir / "chunks.jsonl"
        self._chroma_collection = None

        try:
            import chromadb  # noqa: F401
            from langchain_ollama import OllamaEmbeddings  # noqa: F401
            from langchain_community.vectorstores import Chroma  # noqa: F401

            self._mode = "chroma"
            self._init_chroma()
        except Exception:
            self._mode = "keyword"

    def _init_chroma(self) -> None:
        from langchain_community.vectorstores import Chroma
        from langchain_ollama import OllamaEmbeddings

        embeddings = OllamaEmbeddings(model=self.embed_model)
        self._chroma_collection = Chroma(
            collection_name="ngo_homesuite_copilot",
            persist_directory=str(self.index_dir / "chroma"),
            embedding_function=embeddings,
        )

    def _iter_project_files(self, root: Path) -> Iterable[Path]:
        allowed_suffixes = {".py", ".md", ".txt", ".sql", ".yaml", ".yml"}
        ignored_parts = {".git", ".venv", "__pycache__", ".pytest_cache", "node_modules"}

        for path in root.rglob("*"):
            if not path.is_file():
                continue
            if path.suffix.lower() not in allowed_suffixes:
                continue
            if any(part in ignored_parts for part in path.parts):
                continue
            yield path

    def _chunk_text(self, text: str, chunk_size: int = 1200, overlap: int = 200) -> list[str]:
        text = (text or "").strip()
        if not text:
            return []
        if len(text) <= chunk_size:
            return [text]

        chunks: list[str] = []
        start = 0
        while start < len(text):
            end = min(len(text), start + chunk_size)
            chunks.append(text[start:end])
            if end >= len(text):
                break
            start = max(0, end - overlap)
        return chunks

    def _read_text(self, path: Path) -> str:
        try:
            return path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            return ""

    def build(self, root_dir: str, user_summary_texts: list[str] | None = None) -> int:
        root = Path(root_dir)
        chunks: list[RAGChunk] = []

        for file_path in self._iter_project_files(root):
            raw = self._read_text(file_path)
            if not raw.strip():
                continue
            rel = str(file_path.relative_to(root)).replace("\\", "/")
            for part in self._chunk_text(raw):
                chunks.append(RAGChunk(source=rel, text=part))

        for i, summary in enumerate(user_summary_texts or []):
            redacted, _ = redact_pii(summary or "")
            if not redacted.strip():
                continue
            for part in self._chunk_text(redacted):
                chunks.append(RAGChunk(source=f"user_summary:{i}", text=part))

        if self._mode == "chroma" and self._chroma_collection is not None:
            # Recreate collection by removing persistence directory for deterministic rebuilds.
            persist_dir = self.index_dir / "chroma"
            if persist_dir.exists():
                for child in persist_dir.glob("**/*"):
                    if child.is_file():
                        try:
                            child.unlink()
                        except Exception:
                            pass
            self._init_chroma()

            from langchain_core.documents import Document

            docs = [Document(page_content=c.text, metadata={"source": c.source}) for c in chunks]
            if docs:
                self._chroma_collection.add_documents(docs)
            return len(docs)

        with self._keyword_path.open("w", encoding="utf-8") as fh:
            for chunk in chunks:
                fh.write(json.dumps({"source": chunk.source, "text": chunk.text}, ensure_ascii=False) + "\n")
        return len(chunks)

    def retrieve(self, query: str, k: int = 6) -> list[dict]:
        query = (query or "").strip()
        if not query:
            return []

        if self._mode == "chroma" and self._chroma_collection is not None:
            docs = self._chroma_collection.similarity_search(query, k=max(1, k))
            return [{"source": d.metadata.get("source", "unknown"), "text": d.page_content} for d in docs]

        if not self._keyword_path.exists():
            return []

        tokens = {t for t in query.lower().split() if len(t) > 2}
        scored: list[tuple[int, dict]] = []

        with self._keyword_path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    item = json.loads(line)
                except json.JSONDecodeError:
                    continue
                text = str(item.get("text", ""))
                hay = text.lower()
                score = sum(1 for t in tokens if t in hay)
                if score > 0:
                    scored.append((score, {"source": item.get("source", "unknown"), "text": text}))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [item for _, item in scored[: max(1, k)]]


def default_index_dir() -> str:
    settings = get_runtime_settings()
    return settings.copilot_index_dir

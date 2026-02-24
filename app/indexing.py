from __future__ import annotations
from sqlalchemy import text as sql_text
from sqlalchemy.engine import Connection
from app.logger import get_logger

log = get_logger("indexing")

def chunk_text(text: str, max_chars: int = 1000) -> list[str]:
    t = text.strip()
    if not t:
        return []
    chunks: list[str] = []
    start = 0
    while start < len(t):
        end = min(len(t), start + max_chars)
        chunks.append(t[start:end])
        start = end
    return chunks

def fts_rebuild(conn: Connection, doc_id: int, kind: str, text: str) -> None:
    conn.execute(sql_text("DELETE FROM chunks_fts WHERE doc_id = :doc_id AND kind = :kind"), {"doc_id": doc_id, "kind": kind})
    rows = chunk_text(text)
    for i, c in enumerate(rows):
        conn.execute(
            sql_text("INSERT INTO chunks_fts(doc_id, kind, chunk_id, content) VALUES (:doc_id, :kind, :chunk_id, :content)"),
            {"doc_id": doc_id, "kind": kind, "chunk_id": str(i), "content": c},
        )
    log.info("FTS rebuilt doc=%s kind=%s chunks=%s", doc_id, kind, len(rows))

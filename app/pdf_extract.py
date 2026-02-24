from __future__ import annotations
import fitz

def extract_text_from_pdf(path: str) -> str:
    doc = fitz.open(path)
    parts: list[str] = []
    for i, page in enumerate(doc, start=1):
        txt = page.get_text("text") or ""
        parts.append(txt.strip())
    doc.close()
    return "\n\n".join([p for p in parts if p]).strip()

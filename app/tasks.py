from __future__ import annotations
from datetime import datetime
import os
from pathlib import Path

from app.db import db_session
from app.models import Document, Version
from app.logger import get_logger
from app.pdf_extract import extract_text_from_pdf
from app.indexing import fts_rebuild
from app.config import settings
from app.llm import call_llm_tex
from app.prompting import build_user_prompt
from app.tex_utils import extract_body, make_full_tex, escape_tex
from app.tex_convert import tex_to_text
from app.latex import compile_tex_to_pdf, LatexCompileError

log = get_logger("tasks")


def _editor_active(doc: Document) -> bool:
    if not doc.editor_open:
        return False
    if not doc.editor_heartbeat_at:
        return False
    age = (datetime.utcnow() - doc.editor_heartbeat_at).total_seconds()
    return age < 120


def _generated_pdf_path(doc_id: int, kind: str) -> str:
    Path(settings.generated_dir).mkdir(parents=True, exist_ok=True)
    return str(Path(settings.generated_dir) / f"doc_{doc_id}_{kind}.pdf")

def _delete_version_files(v: Version) -> None:
    try:
        if v.pdf_path and os.path.exists(v.pdf_path):
            os.remove(v.pdf_path)
    except Exception:
        pass

def process_pdf_task(doc_id: int) -> None:
    with db_session() as s:
        doc = s.get(Document, doc_id)
        if not doc:
            return
        doc.status = "processing"
        doc.last_error = None
        doc.updated_at = datetime.utcnow()

    try:
        with db_session() as s:
            doc = s.get(Document, doc_id)
            if not doc:
                return
            text = extract_text_from_pdf(doc.original_path)
            doc.extracted_text = text
            doc.status = "ready"
            doc.updated_at = datetime.utcnow()
            fts_rebuild(s.connection(), doc_id, "original", text)
            log.info("process_pdf_task done doc=%s chars=%s", doc_id, len(text))
    except Exception as e:
        with db_session() as s:
            doc = s.get(Document, doc_id)
            if doc:
                doc.status = "error"
                doc.last_error = str(e)
                doc.updated_at = datetime.utcnow()
        log.exception("process_pdf_task error doc=%s", doc_id)

def transform_tex_task(
    doc_id: int,
    base_kind: str,
    toc_indexes: bool,
    structure: bool,
    spelling: bool,
    extra: str,
    user_id: int,
) -> None:
    with db_session() as s:
        doc = s.get(Document, doc_id)
        if not doc:
            return
        doc.status = "processing"
        doc.last_error = None
        doc.updated_at = datetime.utcnow()

    try:
        with db_session() as s:
            doc = s.get(Document, doc_id)
            if not doc:
                return

            is_tex = base_kind in ("draft", "saved")
            input_payload = ""

            if base_kind == "original":
                input_payload = doc.extracted_text or ""
                if not input_payload:
                    input_payload = extract_text_from_pdf(doc.original_path)
                    doc.extracted_text = input_payload
            else:
                v = s.query(Version).filter(Version.doc_id == doc_id, Version.kind == base_kind).first()
                if v:
                    input_payload = v.tex_source
                else:
                    is_tex = False
                    input_payload = doc.extracted_text or ""
                    if not input_payload:
                        input_payload = extract_text_from_pdf(doc.original_path)
                        doc.extracted_text = input_payload

            system_prompt = (
                "Ты опытный редактор документов. Твоя задача — улучшить текст согласно требованиям и вернуть LaTeX документ. "
                "Сохраняй смысл и содержание текста. Не добавляй выдуманных фактов. "
                "Возвращай только LaTeX."
            )
            user_prompt = build_user_prompt(
                input_payload,
                is_tex=is_tex,
                toc_indexes=toc_indexes,
                structure=structure,
                spelling=spelling,
                extra=extra,
            )

        tex_raw, model_used = call_llm_tex(system_prompt, user_prompt)
        body = extract_body(tex_raw)
        full_tex = make_full_tex(body, toc=toc_indexes)

        out_pdf = _generated_pdf_path(doc_id, "draft")
        try:
            compile_tex_to_pdf(full_tex, out_pdf, toc=toc_indexes)
        except LatexCompileError as ce:
            repair_system = (
                "Исправь LaTeX так, чтобы он компилировался. "
                "Не меняй смысл текста. Верни только исправленный LaTeX документ."
            )
            repair_user = (
                "ОШИБКА КОМПИЛЯЦИИ:\n"
                + str(ce)
                + "\n\nТЕКУЩИЙ LaTeX:\n<<<\n"
                + full_tex
                + "\n>>>\n"
            )
            tex_fixed, _ = call_llm_tex(repair_system, repair_user)
            body2 = extract_body(tex_fixed)
            full_tex = make_full_tex(body2, toc=toc_indexes)
            compile_tex_to_pdf(full_tex, out_pdf, toc=toc_indexes)

        with db_session() as s:
            doc = s.get(Document, doc_id)
            if not doc:
                return

            if not _editor_active(doc):
                doc.status = "ready"
                doc.updated_at = datetime.utcnow()
                log.info("transform_tex_task discarded (editor closed) doc=%s model=%s", doc_id, model_used)
                return

            plain = tex_to_text(full_tex)
            v = s.query(Version).filter(Version.doc_id == doc_id, Version.kind == "draft").first()
            if v is None:
                v = Version(
                    doc_id=doc_id,
                    kind="draft",
                    tex_source=full_tex,
                    pdf_path=out_pdf,
                    plain_text=plain,
                    created_at=datetime.utcnow(),
                    updated_at=datetime.utcnow(),
                )
                s.add(v)
            else:
                v.tex_source = full_tex
                v.pdf_path = out_pdf
                v.plain_text = plain
                v.updated_at = datetime.utcnow()

            fts_rebuild(s.connection(), doc_id, "draft", plain)

            doc.status = "ready"
            doc.updated_at = datetime.utcnow()
            doc.last_error = None

        log.info("transform_tex_task done doc=%s model=%s", doc_id, model_used)

    except Exception as e:
        with db_session() as s:
            doc = s.get(Document, doc_id)
            if doc:
                doc.status = "error"
                doc.last_error = str(e)
                doc.updated_at = datetime.utcnow()
        log.exception("transform_tex_task error doc=%s", doc_id)
        raise


def _text_to_tex_body_no_change(text: str) -> str:
    """Convert plain text to a simple TeX body without changing words/punctuation.

    We only normalize whitespace:
    - paragraph split by blank lines
    - within paragraphs, join wrapped lines with spaces
    - detect simple bullet/numbered lists and map to itemize/enumerate
    """
    import re

    text = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    blocks = [b for b in re.split(r"\n\s*\n+", text) if b.strip()]
    parts: list[str] = []

    bullet_re = re.compile(r"^\s*([\-\u2022\*])\s+(.+)$")
    num_re = re.compile(r"^\s*(\d+)[\.)]\s+(.+)$")

    for b in blocks:
        lines = [ln.rstrip() for ln in b.split("\n") if ln.strip()]
        if not lines:
            continue

        bullet_matches = [bullet_re.match(ln) for ln in lines]
        num_matches = [num_re.match(ln) for ln in lines]

        if all(m is not None for m in bullet_matches) and len(lines) >= 2:
            items = [escape_tex(m.group(2).strip()) for m in bullet_matches if m]
            body = "\n".join(["\\begin{itemize}"] + [f"\\item {it}" for it in items] + ["\\end{itemize}"])
            parts.append(body)
            continue

        if all(m is not None for m in num_matches) and len(lines) >= 2:
            items = [escape_tex(m.group(2).strip()) for m in num_matches if m]
            body = "\n".join(["\\begin{enumerate}"] + [f"\\item {it}" for it in items] + ["\\end{enumerate}"])
            parts.append(body)
            continue

        joined = " ".join(lines)
        joined = re.sub(r"[ \t]+", " ", joined).strip()
        parts.append(escape_tex(joined))

    return "\n\n".join(parts).strip()


def normalize_original_task(doc_id: int, user_id: int) -> None:
    """Create/update draft from original extracted text without calling LLM."""
    with db_session() as s:
        doc = s.get(Document, doc_id)
        if not doc:
            return
        doc.status = "processing"
        doc.last_error = None
        doc.updated_at = datetime.utcnow()

    try:
        with db_session() as s:
            doc = s.get(Document, doc_id)
            if not doc:
                return
            if not _editor_active(doc):
                doc.status = "ready"
                doc.updated_at = datetime.utcnow()
                return

            text = (doc.extracted_text or "").strip()
            if not text:
                text = extract_text_from_pdf(doc.original_path)
                doc.extracted_text = text

        body = _text_to_tex_body_no_change(text)
        full_tex = make_full_tex(body, toc=False)

        out_pdf = _generated_pdf_path(doc_id, "draft")
        compile_tex_to_pdf(full_tex, out_pdf, toc=False)

        with db_session() as s:
            doc = s.get(Document, doc_id)
            if not doc:
                return
            if not _editor_active(doc):
                doc.status = "ready"
                doc.updated_at = datetime.utcnow()
                return

            plain = tex_to_text(full_tex)
            v = s.query(Version).filter(Version.doc_id == doc_id, Version.kind == "draft").first()
            if v is None:
                v = Version(
                    doc_id=doc_id,
                    kind="draft",
                    tex_source=full_tex,
                    pdf_path=out_pdf,
                    plain_text=plain,
                    created_at=datetime.utcnow(),
                    updated_at=datetime.utcnow(),
                )
                s.add(v)
            else:
                v.tex_source = full_tex
                v.pdf_path = out_pdf
                v.plain_text = plain
                v.updated_at = datetime.utcnow()

            fts_rebuild(s.connection(), doc_id, "draft", plain)
            doc.status = "ready"
            doc.updated_at = datetime.utcnow()
            doc.last_error = None

        log.info("normalize_original_task done doc=%s", doc_id)

    except Exception as e:
        with db_session() as s:
            doc = s.get(Document, doc_id)
            if doc:
                doc.status = "error"
                doc.last_error = str(e)[:2000]
                doc.updated_at = datetime.utcnow()
        log.exception("normalize_original_task error doc=%s", doc_id)
        raise

def promote_draft_to_saved(doc_id: int) -> None:
    with db_session() as s:
        doc = s.get(Document, doc_id)
        if not doc:
            return
        draft = s.query(Version).filter(Version.doc_id == doc_id, Version.kind == "draft").first()
        if not draft:
            return

        old_saved = s.query(Version).filter(Version.doc_id == doc_id, Version.kind == "saved").first()
        if old_saved:
            _delete_version_files(old_saved)
            s.delete(old_saved)
            s.flush()

        saved_pdf = _generated_pdf_path(doc_id, "saved")
        try:
            Path(saved_pdf).parent.mkdir(parents=True, exist_ok=True)
            if os.path.exists(draft.pdf_path):
                import shutil
                shutil.copyfile(draft.pdf_path, saved_pdf)
        except Exception:
            pass

        draft.kind = "saved"
        draft.pdf_path = saved_pdf
        draft.updated_at = datetime.utcnow()

        try:
            if os.path.exists(_generated_pdf_path(doc_id, "draft")):
                os.remove(_generated_pdf_path(doc_id, "draft"))
        except Exception:
            pass

        fts_rebuild(s.connection(), doc_id, "saved", draft.plain_text)


        doc.status = "ready"
        doc.updated_at = datetime.utcnow()
        doc.last_error = None

def discard_draft(doc_id: int) -> None:
    with db_session() as s:
        doc = s.get(Document, doc_id)
        if not doc:
            return
        draft = s.query(Version).filter(Version.doc_id == doc_id, Version.kind == "draft").first()
        if draft:
            _delete_version_files(draft)
            s.delete(draft)
        try:
            p = _generated_pdf_path(doc_id, "draft")
            if os.path.exists(p):
                os.remove(p)
        except Exception:
            pass
        from sqlalchemy import text as sql_text
        s.connection().execute(sql_text("DELETE FROM chunks_fts WHERE doc_id = :doc_id AND kind = :kind"), {"doc_id": doc_id, "kind": "draft"})

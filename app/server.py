from __future__ import annotations
from datetime import datetime
import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request, UploadFile, Form
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse, Response, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from sqlalchemy import text as sql_text

from app.config import settings
from app.logger import get_logger
from app.auth import hash_password, verify_password, login_session, logout_session, require_user
from app.db import db_session
from app.models import User, Document, Version, DocShare
from app.access import can_access_doc, is_owner
from app.queueing import enqueue
from app import tasks
from app.tex_convert import tex_to_text, tex_to_markdown

log = get_logger("server")

EDITOR_STALE_SECONDS = 120

templates = Jinja2Templates(directory="templates")

def _access_role(s, user_id: int, doc_id: int) -> tuple[Document | None, str]:
    """Return (doc, role) where role is: not_found | owner | shared | no_access."""
    doc = s.get(Document, doc_id)
    if not doc:
        return None, "not_found"
    if doc.owner_id == user_id:
        return doc, "owner"
    shared = s.query(DocShare).filter(DocShare.doc_id == doc_id, DocShare.user_id == user_id).first()
    if shared:
        return doc, "shared"
    return doc, "no_access"

def _now() -> datetime:
    return datetime.utcnow()

def _pdf_url(doc_id: int, v: str, has_saved: bool, has_draft: bool) -> str:
    if v == "saved" and has_saved:
        return f"/file/generated/{doc_id}_saved.pdf"
    if v == "draft" and has_draft:
        return f"/file/generated/{doc_id}_draft.pdf"
    return f"/file/original/{doc_id}.pdf"

def _effective_view(v: str, has_saved: bool, has_draft: bool) -> str:
    """Return a safe view kind that actually exists."""
    v = (v or "original").lower()
    if v == "saved" and has_saved:
        return "saved"
    if v == "draft" and has_draft:
        return "draft"
    return "original"

def create_app() -> FastAPI:
    _app = FastAPI()
    _app.add_middleware(SessionMiddleware, secret_key=settings.secret_key)
    if os.path.isdir("static"):
        _app.mount("/static", StaticFiles(directory="static"), name="static")
    else:
        os.makedirs("static", exist_ok=True)
        _app.mount("/static", StaticFiles(directory="static"), name="static")

    @_app.exception_handler(PermissionError)
    async def _perm_handler(request: Request, exc: PermissionError):
        if str(exc) == "not_authenticated":
            return RedirectResponse("/login", status_code=303)
        return PlainTextResponse("Forbidden", status_code=403)

    @_app.get("/", response_class=HTMLResponse)
    def root(request: Request):
        user_id = request.session.get("user_id")
        return RedirectResponse("/app" if user_id else "/login", status_code=303)

    @_app.get("/login", response_class=HTMLResponse)
    def login_page(request: Request):
        return templates.TemplateResponse("login.html", {"request": request, "error": None})

    @_app.post("/login")
    def login_action(request: Request, username: str = Form(...), password: str = Form(...)):
        with db_session() as s:
            user = s.query(User).filter(User.username == username).first()
            if not user or not verify_password(password, user.password_hash):
                return templates.TemplateResponse("login.html", {"request": request, "error": "Неверный логин или пароль"})
            login_session(request, user)
            log.info("User login: %s", username)
            return RedirectResponse("/app", status_code=303)

    @_app.get("/register", response_class=HTMLResponse)
    def register_page(request: Request):
        return templates.TemplateResponse("register.html", {"request": request, "error": None})

    @_app.post("/register")
    def register_action(request: Request, username: str = Form(...), password: str = Form(...)):
        username = username.strip()
        if not username or not password:
            return templates.TemplateResponse("register.html", {"request": request, "error": "Введите логин и пароль"})
        with db_session() as s:
            exists = s.query(User).filter(User.username == username).first()
            if exists:
                return templates.TemplateResponse("register.html", {"request": request, "error": "Пользователь уже существует"})
            u = User(username=username, password_hash=hash_password(password))
            s.add(u)
            s.flush()
            log.info("User registered: %s", username)
        return RedirectResponse("/login", status_code=303)

    @_app.post("/logout")
    def logout_action(request: Request):
        logout_session(request)
        return RedirectResponse("/login", status_code=303)

    @_app.get("/app", response_class=HTMLResponse)
    def dashboard(request: Request, q: str | None = None):
        user = require_user(request)
        with db_session() as s:
            my_docs = s.query(Document).filter(Document.owner_id == user.id).order_by(Document.updated_at.desc()).all()
            shared_docs = (
                s.query(Document)
                .join(DocShare, DocShare.doc_id == Document.id)
                .filter(DocShare.user_id == user.id)
                .order_by(Document.updated_at.desc())
                .all()
            )

            allowed_ids = {d.id for d in my_docs} | {d.id for d in shared_docs}
            stale = s.query(Document).filter(Document.id.in_(allowed_ids), Document.editor_open == True).all()
            now = _now()
            for d in stale:
                if d.editor_heartbeat_at is None:
                    continue
                age = (now - d.editor_heartbeat_at).total_seconds()
                if age > EDITOR_STALE_SECONDS:
                    d.editor_open = False
                    d.updated_at = now
                    tasks.discard_draft(d.id)

            results: list[dict[str, Any]] = []
            if q:
                sql = (
                    "SELECT doc_id, kind, snippet(chunks_fts, 3, '[', ']', '…', 10) AS snip "
                    "FROM chunks_fts WHERE chunks_fts MATCH :q LIMIT 30"
                )
                rows = s.execute(sql_text(sql), {"q": q}).fetchall()
                allowed_ids = {d.id for d in my_docs} | {d.id for d in shared_docs}
                for r in rows:
                    if int(r.doc_id) not in allowed_ids:
                        continue
                    doc = s.get(Document, int(r.doc_id))
                    if not doc:
                        continue
                    results.append({"doc": doc, "kind": r.kind, "snippet": r.snip})

        return templates.TemplateResponse(
            "dashboard.html",
            {"request": request, "user": user, "my_docs": my_docs, "shared_docs": shared_docs, "q": q or "", "results": results},
        )

    @_app.post("/upload")
    async def upload(request: Request, file: UploadFile):
        user = require_user(request)
        if not file.filename.lower().endswith(".pdf"):
            return PlainTextResponse("Only PDF supported", status_code=400)

        with db_session() as s:
            doc = Document(
                owner_id=user.id,
                filename=file.filename,
                size=0,
                original_path="",
                status="queued",
                last_error=None,
                editor_open=False,
                created_at=_now(),
                updated_at=_now(),
            )
            s.add(doc)
            s.flush()
            doc_id = doc.id

        orig_path = str(Path(settings.original_dir) / f"{doc_id}.pdf")
        data = await file.read()
        Path(orig_path).parent.mkdir(parents=True, exist_ok=True)
        Path(orig_path).write_bytes(data)

        with db_session() as s:
            doc = s.get(Document, doc_id)
            if doc:
                doc.original_path = orig_path
                doc.size = len(data)
                doc.updated_at = _now()
                doc.status = "queued"

        enqueue(tasks.process_pdf_task, doc_id)
        log.info("Uploaded PDF doc=%s user=%s name=%s bytes=%s", doc_id, user.username, file.filename, len(data))
        return RedirectResponse(f"/doc/{doc_id}", status_code=303)

    def _get_versions(s, doc_id: int) -> tuple[Version | None, Version | None]:
        saved = s.query(Version).filter(Version.doc_id == doc_id, Version.kind == "saved").first()
        draft = s.query(Version).filter(Version.doc_id == doc_id, Version.kind == "draft").first()
        return saved, draft

    @_app.get("/doc/{doc_id}", response_class=HTMLResponse)
    def doc_view(request: Request, doc_id: int, v: str | None = None):
        user = require_user(request)
        with db_session() as s:
            doc, role = _access_role(s, user.id, doc_id)
            if role == "not_found":
                return PlainTextResponse("Not found", status_code=404)
            if role == "no_access":
                return templates.TemplateResponse(
                    "no_access.html",
                    {"request": request, "user": user, "doc_id": doc_id},
                    status_code=403,
                )
            doc.editor_open = True
            doc.editor_heartbeat_at = _now()
            doc.updated_at = _now()
            saved, draft = _get_versions(s, doc_id)
            has_saved = saved is not None
            has_draft = draft is not None
            saved_pdf_ready = bool(saved and saved.pdf_path and os.path.exists(saved.pdf_path))
            draft_pdf_ready = bool(draft and draft.pdf_path and os.path.exists(draft.pdf_path))

            shares = (
                s.query(User.username)
                .join(DocShare, DocShare.user_id == User.id)
                .filter(DocShare.doc_id == doc_id)
                .order_by(User.username.asc())
                .all()
            )
            share_usernames = [u[0] for u in shares]

            owner_username = s.query(User.username).filter(User.id == doc.owner_id).scalar() or str(doc.owner_id)


        req_v = (v or "").lower().strip()
        if req_v not in ("", "original", "saved", "draft"):
            req_v = ""

        if req_v == "":
            if doc.status in ("queued", "processing") or has_draft:
                effective_v = "draft"
            elif has_saved:
                effective_v = "saved"
            else:
                effective_v = "original"
        elif req_v == "saved":
            if has_saved:
                effective_v = "saved"
            elif doc.status in ("queued", "processing") or has_draft:
                effective_v = "draft"
            else:
                effective_v = "original"
        elif req_v == "draft":
            if doc.status in ("queued", "processing") or has_draft:
                effective_v = "draft"
            elif has_saved:
                effective_v = "saved"
            else:
                effective_v = "original"
        else:
            effective_v = "original"

        if effective_v != (req_v or ""):
            return RedirectResponse(f"/doc/{doc_id}?v={effective_v}", status_code=303)

        if effective_v == "saved" and not saved_pdf_ready:
            pdf_url = f"/file/original/{doc_id}.pdf"
        elif effective_v == "draft" and not draft_pdf_ready:
            pdf_url = f"/file/original/{doc_id}.pdf"
        else:
            pdf_url = _pdf_url(doc_id, effective_v, has_saved, has_draft)
        return templates.TemplateResponse(
            "doc.html",
            {
                "request": request,
                "user": user,
                "doc": doc,
                "v": effective_v,
                "has_saved": has_saved,
                "has_draft": has_draft,
                "saved_pdf_ready": saved_pdf_ready,
                "draft_pdf_ready": draft_pdf_ready,
                "pdf_url": pdf_url,
                "status": doc.status,
                "last_error": doc.last_error or "",
                "shares": share_usernames,
                "is_owner": (doc.owner_id == user.id),
                "owner_username": owner_username,

            },
        )

    @_app.get("/api/doc/{doc_id}/status")
    def api_status(request: Request, doc_id: int):
        user = require_user(request)
        with db_session() as s:
            doc, role = _access_role(s, user.id, doc_id)
            if role == "not_found":
                return {"error": "not_found"}
            if role == "no_access":
                return {"error": "no_access"}
            doc.editor_open = True
            doc.editor_heartbeat_at = _now()
            saved = s.query(Version).filter(Version.doc_id == doc_id, Version.kind == "saved").first()
            draft = s.query(Version).filter(Version.doc_id == doc_id, Version.kind == "draft").first()
            return {
                "status": doc.status,
                "last_error": doc.last_error,
                "updated_at": doc.updated_at.isoformat(),
                "has_saved": bool(saved),
                "has_draft": bool(draft),
                "saved_pdf_ready": bool(saved and saved.pdf_path and os.path.exists(saved.pdf_path)),
                "draft_pdf_ready": bool(draft and draft.pdf_path and os.path.exists(draft.pdf_path)),
                "draft_updated_at": draft.updated_at.isoformat() if draft else None,
            }


    @_app.get("/api/doc/{doc_id}/search")
    def api_search(request: Request, doc_id: int, v: str = "saved", q: str = ""):
        """Simple substring search over the selected version's plain text.

        v: original | saved | draft (effective view kind)
        """
        user = require_user(request)
        q = (q or "").strip()
        if not q:
            return {"query": "", "results": []}

        with db_session() as s:
            doc, role = _access_role(s, user.id, doc_id)
            if role == "not_found":
                return {"error": "not_found"}
            if role == "no_access":
                return {"error": "no_access"}

            v_eff = (v or "original").lower()
            if v_eff not in ("original", "saved", "draft"):
                v_eff = "original"

            text = ""
            if v_eff == "original":
                text = doc.extracted_text or ""
                if not text:
                    try:
                        import fitz
                        pdf = fitz.open(doc.original_path)
                        parts = []
                        for page in pdf:
                            parts.append(page.get_text() or "")
                        pdf.close()
                        text = "\n".join(parts).strip()
                        if text:
                            doc.extracted_text = text
                            doc.updated_at = _now()
                            s.commit()
                    except Exception:
                        text = ""
            else:
                ver = s.query(Version).filter(Version.doc_id == doc_id, Version.kind == v_eff).first()
                if ver:
                    text = (ver.plain_text or "").strip()
                    if not text and ver.tex_source:
                        try:
                            text = tex_to_text(ver.tex_source)
                        except Exception:
                            text = ""

        if not text:
            return {"query": q, "results": []}

        needle = q.lower()
        low = text.lower()
        res = []
        start = 0
        limit = 40
        while True:
            idx = low.find(needle, start)
            if idx == -1:
                break
            left = max(0, idx - 80)
            right = min(len(text), idx + len(q) + 80)
            snippet = text[left:right].replace("\n", " ")
            res.append({"pos": idx, "snippet": snippet})
            start = idx + max(1, len(q))
            if len(res) >= limit:
                break
        return {"query": q, "results": res}

    @_app.post("/doc/{doc_id}/apply")
    def apply_changes(
        request: Request,
        doc_id: int,
        base_kind: str = Form("original"),
        toc_indexes: bool = Form(False),
        structure: bool = Form(False),
        spelling: bool = Form(False),
        extra: str = Form(""),
    ):
        user = require_user(request)
        doc0 = can_access_doc(user.id, doc_id)
        if not doc0:
            return PlainTextResponse("Not found", status_code=404)

        base_kind = (base_kind or "original").lower()
        if base_kind not in ("original", "saved", "draft"):
            base_kind = "original"

        with db_session() as s:
            if base_kind == "saved":
                if not s.query(Version).filter(Version.doc_id == doc_id, Version.kind == "saved").first():
                    base_kind = "original"
            if base_kind == "draft":
                if not s.query(Version).filter(Version.doc_id == doc_id, Version.kind == "draft").first():
                    base_kind = "original"

        with db_session() as s:
            d = s.get(Document, doc_id)
            if d:
                d.status = "queued"
                d.last_error = None
                d.editor_open = True
                d.editor_heartbeat_at = _now()
                d.updated_at = _now()

        enqueue(tasks.transform_tex_task, doc_id, base_kind, toc_indexes, structure, spelling, extra, user.id)
        return RedirectResponse(f"/doc/{doc_id}?v=draft", status_code=303)

    @_app.post("/doc/{doc_id}/normalize_original")
    def normalize_original(request: Request, doc_id: int):
        """Create a draft from the original PDF without using LLM.

        Takes extracted text and converts it to TeX deterministically (no changes to words/punctuation).
        Result appears in the "draft" tab.
        """
        user = require_user(request)
        doc0 = can_access_doc(user.id, doc_id)
        if not doc0:
            return PlainTextResponse("Not found", status_code=404)

        with db_session() as s:
            d = s.get(Document, doc_id)
            if d:
                d.status = "queued"
                d.last_error = None
                d.editor_open = True
                d.editor_heartbeat_at = _now()
                d.updated_at = _now()

        enqueue(tasks.normalize_original_task, doc_id, user.id)
        return RedirectResponse(f"/doc/{doc_id}?v=draft", status_code=303)

    @_app.post("/doc/{doc_id}/save")
    def save_changes(request: Request, doc_id: int):
        user = require_user(request)
        doc0 = can_access_doc(user.id, doc_id)
        if not doc0:
            return PlainTextResponse("Not found", status_code=404)
        try:
            with db_session() as s:
                doc = s.get(Document, doc_id)
                draft = s.query(Version).filter(Version.doc_id == doc_id, Version.kind == "draft").first()
                if not doc or not draft:
                    return RedirectResponse(f"/doc/{doc_id}?v=saved", status_code=303)
                if doc.status != "ready" or not (draft.pdf_path and os.path.exists(draft.pdf_path)):
                    return RedirectResponse(f"/doc/{doc_id}?v=draft", status_code=303)

            tasks.promote_draft_to_saved(doc_id)
            return RedirectResponse(f"/doc/{doc_id}?v=saved", status_code=303)
        except Exception as e:
            log.exception("save_changes error doc=%s user=%s", doc_id, user.username)
            with db_session() as s:
                doc = s.get(Document, doc_id)
                if doc:
                    doc.last_error = str(e)
                    doc.status = "error"
                    doc.updated_at = _now()
            return RedirectResponse(f"/doc/{doc_id}?v=draft", status_code=303)

    @_app.post("/doc/{doc_id}/cancel")
    def cancel_changes(request: Request, doc_id: int):
        user = require_user(request)
        doc0 = can_access_doc(user.id, doc_id)
        if not doc0:
            return PlainTextResponse("Not found", status_code=404)
        tasks.discard_draft(doc_id)
        return RedirectResponse(f"/doc/{doc_id}?v=saved", status_code=303)

    @_app.post("/doc/{doc_id}/clear_error")
    def clear_error(request: Request, doc_id: int):
        """Hide a sticky error banner (last_error) for a document.

        We keep errors persisted for visibility, but users need a way to dismiss them.
        If the document has an existing viewable version PDF, we also restore status=ready.
        """
        user = require_user(request)
        doc0 = can_access_doc(user.id, doc_id)
        if not doc0:
            return PlainTextResponse("Not found", status_code=404)

        with db_session() as s:
            doc = s.get(Document, doc_id)
            if not doc:
                return RedirectResponse("/app", status_code=303)

            ready = False
            saved = s.query(Version).filter(Version.doc_id == doc_id, Version.kind == "saved").first()
            draft = s.query(Version).filter(Version.doc_id == doc_id, Version.kind == "draft").first()
            for v in (draft, saved):
                if v and v.pdf_path and os.path.exists(v.pdf_path):
                    ready = True
                    break
            if os.path.exists(doc.original_path):
                ready = True

            doc.last_error = None
            doc.status = "ready" if ready else "queued"
            doc.updated_at = _now()

        v = request.query_params.get("v") or "saved"
        return RedirectResponse(f"/doc/{doc_id}?v={v}", status_code=303)

    @_app.post("/doc/{doc_id}/close")
    def close_editor(request: Request, doc_id: int):
        user = require_user(request)
        doc0 = can_access_doc(user.id, doc_id)
        if not doc0:
            return PlainTextResponse("Not found", status_code=404)

        with db_session() as s:
            d = s.get(Document, doc_id)
            if d:
                d.editor_open = False
                d.editor_heartbeat_at = None
                d.updated_at = _now()

        tasks.discard_draft(doc_id)
        return PlainTextResponse("ok")


    @_app.post("/doc/{doc_id}/close_page")
    def close_editor_page(request: Request, doc_id: int):
        user = require_user(request)
        doc0 = can_access_doc(user.id, doc_id)
        if not doc0:
            return PlainTextResponse("Not found", status_code=404)

        with db_session() as s:
            d = s.get(Document, doc_id)
            if d:
                d.editor_open = False
                d.editor_heartbeat_at = None
                d.updated_at = _now()

        tasks.discard_draft(doc_id)
        return RedirectResponse("/app", status_code=303)

    @_app.post("/doc/{doc_id}/share/add")
    def share_add(request: Request, doc_id: int, username: str = Form(...)):
        user = require_user(request)
        doc0 = can_access_doc(user.id, doc_id)
        if not doc0:
            return PlainTextResponse("Not found", status_code=404)
        if not is_owner(user.id, doc0):
            return PlainTextResponse("Forbidden", status_code=403)

        username = username.strip()
        with db_session() as s:
            target = s.query(User).filter(User.username == username).first()
            if not target:
                return RedirectResponse(f"/doc/{doc_id}?v=draft", status_code=303)
            exists = s.query(DocShare).filter(DocShare.doc_id == doc_id, DocShare.user_id == target.id).first()
            if not exists and target.id != user.id:
                s.add(DocShare(doc_id=doc_id, user_id=target.id, created_at=_now()))
        return RedirectResponse(f"/doc/{doc_id}?v=draft", status_code=303)

    @_app.post("/doc/{doc_id}/share/remove")
    def share_remove(request: Request, doc_id: int, username: str = Form(...)):
        user = require_user(request)
        doc0 = can_access_doc(user.id, doc_id)
        if not doc0:
            return PlainTextResponse("Not found", status_code=404)
        if not is_owner(user.id, doc0):
            return PlainTextResponse("Forbidden", status_code=403)

        username = username.strip()
        with db_session() as s:
            target = s.query(User).filter(User.username == username).first()
            if target:
                share = s.query(DocShare).filter(DocShare.doc_id == doc_id, DocShare.user_id == target.id).first()
                if share:
                    s.delete(share)
        return RedirectResponse(f"/doc/{doc_id}?v=draft", status_code=303)

    @_app.post("/doc/{doc_id}/delete_me")
    def delete_me(request: Request, doc_id: int):
        user = require_user(request)
        doc0 = can_access_doc(user.id, doc_id)
        if not doc0:
            return PlainTextResponse("Not found", status_code=404)

        with db_session() as s:
            d = s.get(Document, doc_id)
            if not d:
                return RedirectResponse("/app", status_code=303)
            if d.owner_id == user.id:
                s.delete(d)
                try:
                    op = str(Path(settings.original_dir) / f"{doc_id}.pdf")
                    if os.path.exists(op):
                        os.remove(op)
                except Exception:
                    pass
                for k in ("draft", "saved"):
                    try:
                        gp = str(Path(settings.generated_dir) / f"doc_{doc_id}_{k}.pdf")
                        if os.path.exists(gp):
                            os.remove(gp)
                    except Exception:
                        pass
            else:
                share = s.query(DocShare).filter(DocShare.doc_id == doc_id, DocShare.user_id == user.id).first()
                if share:
                    s.delete(share)
        return RedirectResponse("/app", status_code=303)

    @_app.get("/file/original/{doc_id}.pdf")
    def file_original(request: Request, doc_id: int):
        user = require_user(request)
        doc0 = can_access_doc(user.id, doc_id)
        if not doc0:
            return PlainTextResponse("Not found", status_code=404)

        path = str(Path(settings.original_dir) / f"{doc_id}.pdf")
        if not os.path.exists(path):
            return PlainTextResponse("Not found", status_code=404)

        resp = FileResponse(path, media_type="application/pdf")
        resp.headers["Content-Disposition"] = f'inline; filename="{doc0.filename}"'
        resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        return resp

    @_app.get("/file/generated/{name}.pdf")
    def file_generated(request: Request, name: str):
        user = require_user(request)
        if "_" not in name:
            return PlainTextResponse("Not found", status_code=404)
        doc_id_str, kind = name.split("_", 1)
        if kind not in ("draft", "saved"):
            return PlainTextResponse("Not found", status_code=404)
        try:
            doc_id = int(doc_id_str)
        except Exception:
            return PlainTextResponse("Not found", status_code=404)

        doc0 = can_access_doc(user.id, doc_id)
        if not doc0:
            return PlainTextResponse("Not found", status_code=404)

        with db_session() as s:
            v = s.query(Version).filter(Version.doc_id == doc_id, Version.kind == kind).first()
            if not v or not os.path.exists(v.pdf_path):
                return RedirectResponse(f"/file/original/{doc_id}.pdf", status_code=302)
            path = v.pdf_path

        resp = FileResponse(path, media_type="application/pdf")
        resp.headers["Content-Disposition"] = f'inline; filename="{doc0.filename}"'
        resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        return resp

    @_app.get("/download/{doc_id}/{kind}/{fmt}")
    def download(request: Request, doc_id: int, kind: str, fmt: str):
        user = require_user(request)
        doc0 = can_access_doc(user.id, doc_id)
        if not doc0:
            return PlainTextResponse("Not found", status_code=404)

        fmt = fmt.lower()
        kind = kind.lower()

        if kind == "original":
            if fmt != "pdf":
                return PlainTextResponse("Original supports only PDF", status_code=400)
            path = str(Path(settings.original_dir) / f"{doc_id}.pdf")
            resp = FileResponse(path, media_type="application/pdf")
            resp.headers["Content-Disposition"] = f'attachment; filename="{Path(doc0.filename).stem}_original.pdf"'
            return resp

        if kind not in ("draft", "saved"):
            return PlainTextResponse("Bad kind", status_code=400)

        with db_session() as s:
            v = s.query(Version).filter(Version.doc_id == doc_id, Version.kind == kind).first()
            if not v:
                return PlainTextResponse("Version not found", status_code=404)
            tex = v.tex_source
            pdf_path = v.pdf_path

        if fmt == "pdf":
            resp = FileResponse(pdf_path, media_type="application/pdf")
            resp.headers["Content-Disposition"] = f'attachment; filename="{Path(doc0.filename).stem}_{kind}.pdf"'
            return resp
        if fmt == "tex":
            return Response(content=tex, media_type="application/x-tex; charset=utf-8",
                            headers={"Content-Disposition": f'attachment; filename="{Path(doc0.filename).stem}_{kind}.tex"'})
        if fmt == "txt":
            txt = tex_to_text(tex)
            return Response(content=txt, media_type="text/plain; charset=utf-8",
                            headers={"Content-Disposition": f'attachment; filename="{Path(doc0.filename).stem}_{kind}.txt"'})
        if fmt == "md":
            md = tex_to_markdown(tex)
            return Response(content=md, media_type="text/markdown; charset=utf-8",
                            headers={"Content-Disposition": f'attachment; filename="{Path(doc0.filename).stem}_{kind}.md"'})
        return PlainTextResponse("Bad format", status_code=400)

    return _app

app = create_app()

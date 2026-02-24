from __future__ import annotations
from sqlalchemy import or_
from app.db import db_session
from app.models import Document, DocShare

def can_access_doc(user_id: int, doc_id: int) -> Document | None:
    with db_session() as s:
        doc = (
            s.query(Document)
            .outerjoin(DocShare, DocShare.doc_id == Document.id)
            .filter(Document.id == doc_id)
            .filter(or_(Document.owner_id == user_id, DocShare.user_id == user_id))
            .first()
        )
        return doc

def is_owner(user_id: int, doc: Document) -> bool:
    return doc.owner_id == user_id

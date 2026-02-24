import os
from contextlib import contextmanager
from sqlalchemy import create_engine, event, text as sql_text
from sqlalchemy.orm import sessionmaker, Session
from app.config import settings
from app.logger import get_logger

log = get_logger("db")

os.makedirs(settings.storage_dir, exist_ok=True)
os.makedirs(settings.original_dir, exist_ok=True)
os.makedirs(settings.generated_dir, exist_ok=True)
os.makedirs(settings.tmp_dir, exist_ok=True)

engine = create_engine(
    settings.database_url,
    connect_args={"check_same_thread": False} if settings.database_url.startswith("sqlite") else {},
    future=True,
)

@event.listens_for(engine, "connect")
def _set_sqlite_pragma(dbapi_connection, connection_record):
    try:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL;")
        cursor.execute("PRAGMA synchronous=NORMAL;")
        cursor.execute("PRAGMA busy_timeout=5000;")
        cursor.execute("PRAGMA foreign_keys=ON;")
        cursor.close()
    except Exception:
        pass

SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, future=True)

@contextmanager
def db_session() -> Session:
    s = SessionLocal()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()

def init_db() -> None:
    from app.models import Base
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(sql_text(
            "CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(doc_id UNINDEXED, kind UNINDEXED, chunk_id UNINDEXED, content);"
        ))
    log.info("DB initialized (tables + FTS).")

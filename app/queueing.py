from __future__ import annotations
import redis
from rq import Queue
from app.config import settings
from app.logger import get_logger

log = get_logger("queue")

def get_queue() -> Queue:
    conn = redis.from_url(settings.redis_url)
    return Queue(settings.rq_queue, connection=conn)

def enqueue(func, *args, **kwargs) -> str:
    q = get_queue()
    job = q.enqueue(func, *args, **kwargs)
    log.info("Enqueued job %s for %s args=%s kwargs=%s", job.id, getattr(func, "__name__", str(func)), args, kwargs)
    return str(job.id)

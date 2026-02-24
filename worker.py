import os
import redis
from rq import Worker, Queue
from app.logger import get_logger
from app.db import init_db

log = get_logger("worker")

def main() -> None:
    init_db()
    redis_url = os.getenv("REDIS_URL", "redis://redis:6379/0")
    queue_name = os.getenv("RQ_QUEUE", "pdf")
    conn = redis.from_url(redis_url)
    q = Queue(queue_name, connection=conn)
    log.info("Worker starting. Redis=%s queue=%s", redis_url, queue_name)
    w = Worker([q], connection=conn)
    w.work(with_scheduler=False)

if __name__ == "__main__":
    main()

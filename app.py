import os
import uvicorn
from app.server import create_app
from app.logger import get_logger
from app.db import init_db

log = get_logger("entry")

def main() -> None:
    init_db()
    app = create_app()
    reload = os.getenv("RELOAD", "0") == "1"
    host = "0.0.0.0"
    port = 8000
    log.info("Starting web server reload=%s", reload)
    uvicorn.run("app.server:app", host=host, port=port, reload=reload, log_level="info")

if __name__ == "__main__":
    main()

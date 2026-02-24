import os
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()

@dataclass(frozen=True)
class Settings:
    secret_key: str = os.getenv("SECRET_KEY", "change-me-please")
    database_url: str = os.getenv("DATABASE_URL", "sqlite:///./storage/app.db")

    storage_dir: str = os.getenv("STORAGE_DIR", "./storage")
    original_dir: str = os.getenv("ORIGINAL_DIR", "./storage/original")
    generated_dir: str = os.getenv("GENERATED_DIR", "./storage/generated")
    tmp_dir: str = os.getenv("TMP_DIR", "./storage/tmp")

    redis_url: str = os.getenv("REDIS_URL", "redis://redis:6379/0")
    rq_queue: str = os.getenv("RQ_QUEUE", "pdf")

    llm_provider: str = os.getenv("LLM_PROVIDER", "openrouter")
    openrouter_api_key: str = os.getenv("OPENROUTER_API_KEY", "")
    openrouter_model: str = os.getenv("OPENROUTER_MODEL", "auto")
    openrouter_base_url: str = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
    openrouter_referer: str = os.getenv("OPENROUTER_REFERER", "http://localhost:8000")
    openrouter_title: str = os.getenv("OPENROUTER_TITLE", "Personal PDF Engine")

    latex_engine: str = os.getenv("LATEX_ENGINE", "lualatex")
    latex_max_runs: int = int(os.getenv("LATEX_MAX_RUNS", "2"))

settings = Settings()

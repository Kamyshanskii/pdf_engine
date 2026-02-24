from __future__ import annotations
import os
import time
import requests
from typing import Any
from app.config import settings
from app.logger import get_logger

log = get_logger("llm")

_MODELS_CACHE: tuple[float, list[dict[str, Any]]] | None = None

def _get_models() -> list[dict[str, Any]]:
    global _MODELS_CACHE
    now = time.time()
    if _MODELS_CACHE and now - _MODELS_CACHE[0] < 300:
        return _MODELS_CACHE[1]

    url = settings.openrouter_base_url.rstrip("/") + "/models"
    r = requests.get(url, timeout=30)
    r.raise_for_status()
    data = r.json().get("data", [])
    _MODELS_CACHE = (now, data)
    return data

def _endpoints_count(m: dict[str, Any]) -> int:
    eps = m.get("endpoints")
    if isinstance(eps, list):
        return len(eps)
    return 1

def pick_models(prefer: list[str] | None = None, limit: int = 8) -> list[str]:
    models = _get_models()
    ids = [m.get("id") for m in models if isinstance(m.get("id"), str)]
    prefer = prefer or [
        "deepseek/deepseek-chat",
        "deepseek/deepseek-r1",
        "openai/gpt-4o-mini",
        "google/gemini",
        "anthropic/claude",
    ]

    scored: list[tuple[int, str]] = []
    for m in models:
        mid = m.get("id")
        if not isinstance(mid, str):
            continue
        if _endpoints_count(m) <= 0:
            continue
        score = 0
        for i, p in enumerate(prefer):
            if mid.startswith(p):
                score = 1000 - i * 10
                break
        if m.get("top_provider"):
            score += 3
        scored.append((score, mid))

    scored.sort(reverse=True)
    chosen = []
    seen = set()
    for _, mid in scored:
        if mid in seen:
            continue
        chosen.append(mid)
        seen.add(mid)
        if len(chosen) >= limit:
            break
    if not chosen and ids:
        chosen = ids[:limit]
    return chosen

def _post_chat(model_id: str, messages: list[dict[str, str]], temperature: float = 0.2) -> str:
    url = settings.openrouter_base_url.rstrip("/") + "/chat/completions"
    headers = {
        "Authorization": f"Bearer {settings.openrouter_api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": settings.openrouter_referer,
        "X-Title": settings.openrouter_title,
    }
    payload = {
        "model": model_id,
        "messages": messages,
        "temperature": temperature,
    }
    r = requests.post(url, headers=headers, json=payload, timeout=120)
    if not r.ok:
        raise RuntimeError(f"OpenRouter error {r.status_code}: {r.text[:2000]}")
    j = r.json()
    try:
        return j["choices"][0]["message"]["content"]
    except Exception:
        raise RuntimeError("OpenRouter response parse error: " + str(j)[:500])

def call_llm_tex(system_prompt: str, user_prompt: str) -> tuple[str, str]:
    provider = settings.llm_provider.lower().strip()
    if provider == "none":
        raise RuntimeError("LLM_PROVIDER=none (disabled)")

    if provider != "openrouter":
        raise RuntimeError(f"Unsupported LLM_PROVIDER: {provider}")

    if not settings.openrouter_api_key:
        raise RuntimeError("OPENROUTER_API_KEY is empty")

    requested = settings.openrouter_model.strip()
    if requested and requested.lower() != "auto":
        model_candidates = [requested]
    else:
        model_candidates = pick_models()

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]

    last_err = ""
    for mid in model_candidates[:6]:
        try:
            out = _post_chat(mid, messages, temperature=0.2)
            log.info("LLM ok model=%s chars=%s", mid, len(out))
            return out, mid
        except Exception as e:
            last_err = str(e)
            if "No endpoints found" in last_err or '"code":404' in last_err:
                log.warning("LLM model failed (trying next) model=%s err=%s", mid, last_err[:180])
                continue
            log.warning("LLM call failed model=%s err=%s", mid, last_err[:180])
            continue

    raise RuntimeError(last_err or "LLM failed")

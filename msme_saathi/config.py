"""Centralized environment & OpenRouter model configuration for MSME-Incentive Saathi.

Automatically loads the gitignored repository-root `.env` file (without overriding
explicitly exported shell or test environment variables).
"""

from __future__ import annotations

import os
from pathlib import Path

try:
    from dotenv import load_dotenv as _dotenv_load
except ImportError:  # pragma: no cover - optional fallback if dotenv is absent
    _dotenv_load = None  # type: ignore[assignment]

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ENV_PATH = REPO_ROOT / ".env"

DEFAULT_OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_EMBEDDING_MODEL = "qwen/qwen3-embedding-8b"
DEFAULT_LLM_MODEL = "openai/gpt-4.1-mini"
DEFAULT_CHROMA_PERSIST_DIR = REPO_ROOT / "data" / "chroma"

_ENV_LOADED = False


def load_env(env_path: str | Path | None = None, *, force: bool = False) -> bool:
    """Load `.env` into `os.environ` without overriding already-set variables."""
    global _ENV_LOADED
    if _ENV_LOADED and not force and env_path is None:
        return False

    target = Path(env_path) if env_path is not None else DEFAULT_ENV_PATH
    loaded = False
    if target.exists():
        if _dotenv_load is not None:
            loaded = bool(_dotenv_load(dotenv_path=target, override=False))
        else:
            for raw_line in target.read_text(encoding="utf-8").splitlines():
                line = raw_line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
            loaded = True

    if env_path is None:
        _ENV_LOADED = True
    return loaded


def get_openrouter_api_key() -> str | None:
    """Return `OPENROUTER_API_KEY` after ensuring `.env` is loaded."""
    load_env()
    key = os.getenv("OPENROUTER_API_KEY", "").strip()
    if not key or key == "your-openrouter-api-key-here":
        return None
    return key


def get_openrouter_base_url() -> str:
    """Return the OpenRouter OpenAI-compatible base URL."""
    load_env()
    return os.getenv("OPENROUTER_BASE_URL", DEFAULT_OPENROUTER_BASE_URL).strip().rstrip("/")


def get_embedding_model() -> str:
    """Return configured OpenRouter embedding model (default: `qwen/qwen3-embedding-8b`)."""
    load_env()
    return os.getenv("MSME_EMBEDDING_MODEL", DEFAULT_EMBEDDING_MODEL).strip() or DEFAULT_EMBEDDING_MODEL


def get_llm_model() -> str:
    """Return configured default OpenRouter chat model (default: `openai/gpt-4.1-mini`)."""
    load_env()
    return os.getenv("MSME_LLM_MODEL", DEFAULT_LLM_MODEL).strip() or DEFAULT_LLM_MODEL


def get_rewriter_model() -> str:
    """Return configured OpenRouter query-rewriter model (default: `openai/gpt-4.1-mini`)."""
    load_env()
    return os.getenv("MSME_REWRITER_MODEL") or get_llm_model()


def use_openrouter_embeddings() -> bool:
    """Return True when OpenRouter embeddings are enabled and an API key is configured."""
    load_env()
    flag = os.getenv("MSME_USE_OPENROUTER_EMBEDDINGS")
    if flag is not None:
        normalized = flag.strip().lower()
        if normalized in {"0", "false", "no", "off"}:
            return False
        if normalized in {"1", "true", "yes", "on"}:
            return get_openrouter_api_key() is not None
    return get_openrouter_api_key() is not None


def get_chroma_persist_dir() -> Path | None:
    """Return persistent ChromaDB directory path, or `None` for ephemeral in-memory mode."""
    load_env()
    raw = os.getenv("MSME_CHROMA_PERSIST_DIR")
    if raw is None:
        return DEFAULT_CHROMA_PERSIST_DIR
    cleaned = raw.strip()
    if not cleaned or cleaned.lower() in {"none", "ephemeral", ":memory:"}:
        return None
    path = Path(cleaned)
    return path if path.is_absolute() else (REPO_ROOT / path)

"""Runtime configuration loaded from environment variables."""

from __future__ import annotations

import logging
import os
from functools import lru_cache

from dotenv import load_dotenv

# Load backend/.env regardless of process cwd.
_BACKEND_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
load_dotenv(os.path.join(_BACKEND_ROOT, ".env"))
load_dotenv()  # also allow repo-root .env

logger = logging.getLogger("stock_agent")


@lru_cache(maxsize=1)
def get_settings() -> dict:
    # Prefer classic service_role; fall back to new sb_secret_* server keys.
    secret = (
        os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()
        or os.getenv("SUPABASE_SECRET_KEY", "").strip()
    )
    publishable = (
        os.getenv("SUPABASE_ANON_KEY", "").strip()
        or os.getenv("SUPABASE_PUBLISHABLE_KEY", "").strip()
    )
    settings = {
        "supabase_url": os.getenv("SUPABASE_URL", "").strip().rstrip("/"),
        "supabase_service_role_key": secret,
        "supabase_publishable_key": publishable,
        "log_level": os.getenv("LOG_LEVEL", "INFO").upper(),
        "host": os.getenv("HOST", "0.0.0.0"),
        "port": int(os.getenv("PORT", "8000")),
    }
    return settings


def configure_logging() -> None:
    level = getattr(logging, get_settings()["log_level"], logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )


def require_supabase_config() -> tuple[str, str]:
    settings = get_settings()
    url = settings["supabase_url"]
    key = settings["supabase_service_role_key"]
    if not url or url.endswith("YOUR_PROJECT_REF.supabase.co"):
        raise RuntimeError(
            "Missing SUPABASE_URL. In Supabase → Project Settings → Data API, "
            "copy the Project URL (https://xxxx.supabase.co) into backend/.env."
        )
    if not key:
        raise RuntimeError(
            "Missing SUPABASE_SECRET_KEY (or SUPABASE_SERVICE_ROLE_KEY). "
            "Use the server secret key — never the publishable key — in backend/.env."
        )
    return url, key

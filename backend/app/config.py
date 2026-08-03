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
        "public_api_base_url": os.getenv(
            "PUBLIC_API_BASE_URL", "http://127.0.0.1:8000"
        )
        .strip()
        .rstrip("/"),
        "log_level": os.getenv("LOG_LEVEL", "INFO").upper(),
        "host": os.getenv("HOST", "0.0.0.0"),
        "port": int(os.getenv("PORT", "8000")),
        # Abuse protection (in-process; per API instance)
        "rate_limit_subscribe_per_min": int(
            os.getenv("RATE_LIMIT_SUBSCRIBE_PER_MIN", "10")
        ),
        "rate_limit_snapshot_per_min": int(
            os.getenv("RATE_LIMIT_SNAPSHOT_PER_MIN", "30")
        ),
        "rate_limit_unsubscribe_per_min": int(
            os.getenv("RATE_LIMIT_UNSUBSCRIBE_PER_MIN", "30")
        ),
        "rate_limit_window_seconds": float(
            os.getenv("RATE_LIMIT_WINDOW_SECONDS", "60")
        ),
        "max_request_body_bytes": int(
            os.getenv("MAX_REQUEST_BODY_BYTES", "65536")
        ),
        "trust_proxy": os.getenv("TRUST_PROXY", "").lower() in {"1", "true", "yes"},
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

"""
CLI entrypoint for due-email dispatch (hybrid B).

Prefer HTTP wake-ups in production:
  POST /api/internal/dispatch-due  (header X-Dispatch-Secret)

This script remains for local/CI:
  python backend/worker/cron_dispatch.py
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from dotenv import load_dotenv

load_dotenv(_BACKEND_ROOT / ".env")

from app.services.dispatch import run_due_dispatch

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("stock_agent.cron")


def main() -> int:
    try:
        result = run_due_dispatch()
    except RuntimeError as exc:
        msg = str(exc)
        if os.getenv("GITHUB_ACTIONS"):
            print(f"::error::Supabase config error: {msg}", flush=True)
        logger.error("Failed to run dispatch-due: %s", msg)
        return 1
    except Exception:
        logger.exception("Failed to run dispatch-due")
        return 1

    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())

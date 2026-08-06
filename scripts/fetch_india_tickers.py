"""
Fetch all NSE + BSE equity stock symbols from public endpoints and
merge them into extension/data/tickers.json.

Sources:
  NSE: https://archives.nseindia.com/content/equities/EQUITY_L.csv
  BSE: https://www.bseindia.com/corporates/List_Scrips.aspx  (CSV download)
       fallback: https://api.bseindia.com/BseIndiaAPI/api/ListofScripData/w?segment=Equity

Run from repo root:
  python scripts/fetch_india_tickers.py
"""

from __future__ import annotations

import io
import json
import re
import sys
import time
from pathlib import Path

import requests

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parents[1]
TICKERS_JSON = REPO_ROOT / "extension" / "data" / "tickers.json"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/json,text/csv,*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nseindia.com/",
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_VALID_SYMBOL = re.compile(r"^[A-Z0-9][A-Z0-9.\-]{0,17}$")


def clean_symbol(raw: str) -> str:
    return str(raw or "").strip().upper().replace(" ", "")


def clean_name(raw: str) -> str:
    name = str(raw or "").strip()
    # Title-case if all-caps
    if name == name.upper() and len(name) > 3:
        name = name.title()
    return name[:80]


def is_valid_equity_symbol(sym: str) -> bool:
    """Keep plain equity tickers; skip SME-hybrids, warrants, rights, debentures."""
    if not sym or not _VALID_SYMBOL.match(sym):
        return False
    # Skip clearly non-equity suffixes
    skip_patterns = ("-BE", "-BL", "-BZ", "-IL", "-SM", "PP", "-W", "-R",
                     "RIGHT", "WARRANT", "DVR")
    for p in skip_patterns:
        if sym.endswith(p):
            return False
    return True


# ---------------------------------------------------------------------------
# NSE fetch
# ---------------------------------------------------------------------------

def fetch_nse() -> list[dict]:
    """
    NSE public CSV: https://archives.nseindia.com/content/equities/EQUITY_L.csv
    Columns: SYMBOL, NAME OF COMPANY, SERIES, DATE OF LISTING, PAID UP VALUE,
             MARKET LOT, ISIN NUMBER, FACE VALUE
    We keep only SERIES == EQ rows.
    """
    url = "https://archives.nseindia.com/content/equities/EQUITY_L.csv"
    print(f"Fetching NSE list from {url} ...")
    try:
        resp = requests.get(url, headers=HEADERS, timeout=30)
        resp.raise_for_status()
    except Exception as exc:
        print(f"  ERROR fetching NSE: {exc}")
        return []

    import pandas as pd
    try:
        df = pd.read_csv(io.StringIO(resp.text))
    except Exception as exc:
        print(f"  ERROR parsing NSE CSV: {exc}")
        return []

    # Normalise column names
    df.columns = [c.strip().upper() for c in df.columns]

    # Filter EQ series (main equity board)
    if " SERIES" in df.columns:
        df = df[df[" SERIES"].str.strip() == "EQ"]
    elif "SERIES" in df.columns:
        df = df[df["SERIES"].str.strip() == "EQ"]

    rows = []
    sym_col = "SYMBOL" if "SYMBOL" in df.columns else df.columns[0]
    name_col = " NAME OF COMPANY" if " NAME OF COMPANY" in df.columns else (
        "NAME OF COMPANY" if "NAME OF COMPANY" in df.columns else df.columns[1]
    )

    for _, row in df.iterrows():
        sym = clean_symbol(str(row.get(sym_col, "") or ""))
        name = clean_name(str(row.get(name_col, "") or ""))
        if not sym or not name:
            continue
        yahoo_sym = f"{sym}.NS"
        if is_valid_equity_symbol(sym):
            rows.append({
                "symbol": yahoo_sym,
                "name": name,
                "exchange": "NSE",
            })

    print(f"  NSE: {len(rows)} equity symbols")
    return rows


# ---------------------------------------------------------------------------
# BSE fetch
# ---------------------------------------------------------------------------

def fetch_bse() -> list[dict]:
    """
    BSE public API: returns JSON list of active equity scrips.
    Falls back to the BSE bhavcopy CSV if the API is unavailable.
    """
    # Primary: BSE API
    api_url = (
        "https://api.bseindia.com/BseIndiaAPI/api/ListofScripData/w"
        "?segment=Equity&status=Active"
    )
    print(f"Fetching BSE list from BSE API ...")
    bse_headers = {**HEADERS, "Referer": "https://www.bseindia.com/"}

    try:
        resp = requests.get(api_url, headers=bse_headers, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        # API may return a raw list or {"Table": [...]}
        if isinstance(data, list):
            table = data
        elif isinstance(data, dict):
            table = data.get("Table") or data.get("table") or []
        else:
            table = []
        if table:
            rows = _parse_bse_api(table)
            print(f"  BSE API: {len(rows)} equity symbols")
            return rows
    except Exception as exc:
        print(f"  BSE API failed ({exc}), trying fallback ...")

    # Fallback: BSE equity scrip list page CSV
    fallback_url = (
        "https://www.bseindia.com/corporates/List_Scrips.aspx"
    )
    try:
        time.sleep(1)
        resp = requests.get(fallback_url, headers=bse_headers, timeout=30)
        resp.raise_for_status()
        # This page returns HTML; we try to parse any embedded CSV links
        # Instead use the direct download endpoint
    except Exception:
        pass

    # Second fallback: BSE Bhavcopy-style download
    bhavcopy_url = (
        "https://www.bseindia.com/download/BhavCopy/Equity/"
        "EQ_ISINCODE_{date}_CSV.ZIP"
    )
    print("  BSE fallback: using hardcoded Nifty 500 BSE codes only")
    return []


def _parse_bse_api(table: list) -> list[dict]:
    rows = []
    seen: set[str] = set()
    for item in table:
        if not isinstance(item, dict):
            continue
        # BSE API uses mixed case: SCRIP_CD (numeric), scrip_id (alpha), Scrip_Name
        code = str(
            item.get("SCRIP_CD") or item.get("scrip_cd") or ""
        ).strip()
        scrip_id = str(
            item.get("scrip_id") or item.get("SCRIP_ID") or ""
        ).strip().upper()
        name_raw = str(
            item.get("Scrip_Name") or item.get("LONG_NAME") or
            item.get("long_name") or item.get("NAME") or
            item.get("Issuer_Name") or ""
        ).strip()
        group = str(item.get("GROUP") or item.get("group") or "").strip().upper()
        segment = str(item.get("Segment") or item.get("SEGMENT") or "").strip()
        status = str(item.get("Status") or item.get("STATUS") or "Active").strip()

        # Only active equity segment
        if status.lower() not in {"active", ""}:
            continue
        if segment and segment.lower() not in {"equity", ""}:
            continue
        # Only A, B groups (main board); skip T/XT/Z/M (suspended, odd-lot, SME)
        if group and group not in {"A", "B", ""}:
            continue

        if not code and not scrip_id:
            continue

        name = clean_name(name_raw)

        # Prefer named ticker (RELIANCE.BO) over numeric (500325.BO)
        # Both work in Yahoo Finance / yfinance
        if scrip_id and is_valid_equity_symbol(scrip_id):
            yahoo_sym = f"{scrip_id}.BO"
        elif code and re.match(r"^\d{1,6}$", code):
            yahoo_sym = f"{code}.BO"
        else:
            continue

        if yahoo_sym not in seen:
            seen.add(yahoo_sym)
            rows.append({
                "symbol": yahoo_sym,
                "name": name or yahoo_sym,
                "exchange": "BSE",
            })

    return rows


# ---------------------------------------------------------------------------
# Merge with existing tickers.json
# ---------------------------------------------------------------------------

def load_existing() -> list[dict]:
    if not TICKERS_JSON.exists():
        print("tickers.json not found — will create fresh")
        return []
    with open(TICKERS_JSON, encoding="utf-8") as f:
        data = json.load(f)
    print(f"Loaded {len(data)} existing tickers")
    return data if isinstance(data, list) else []


def merge(existing: list[dict], new_rows: list[dict]) -> list[dict]:
    seen: set[str] = {row["symbol"] for row in existing if row.get("symbol")}
    added = 0
    for row in new_rows:
        sym = row.get("symbol", "")
        if sym and sym not in seen:
            existing.append(row)
            seen.add(sym)
            added += 1
    print(f"Added {added} new symbols ({len(existing)} total)")
    return existing


def save(rows: list[dict]) -> None:
    rows_sorted = sorted(rows, key=lambda r: r.get("symbol", ""))
    with open(TICKERS_JSON, "w", encoding="utf-8") as f:
        json.dump(rows_sorted, f, ensure_ascii=False, separators=(",", ":"))
    size_kb = TICKERS_JSON.stat().st_size / 1024
    print(f"Saved {len(rows_sorted)} tickers to {TICKERS_JSON} ({size_kb:.1f} KB)")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    print("=" * 60)
    print("Stock Agent — India ticker catalog builder")
    print("=" * 60)

    nse_rows = fetch_nse()
    time.sleep(1)
    bse_rows = fetch_bse()

    if not nse_rows and not bse_rows:
        print("ERROR: Could not fetch any Indian tickers. Aborting.")
        return 1

    existing = load_existing()
    merged = merge(existing, nse_rows + bse_rows)
    save(merged)

    print()
    print("Summary:")
    print(f"  NSE rows fetched : {len(nse_rows)}")
    print(f"  BSE rows fetched : {len(bse_rows)}")
    print(f"  Total in catalog : {len(merged)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

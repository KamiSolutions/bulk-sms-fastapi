"""Spend log: one CSV row per campaign, so credit use can be totalled over time.

BulkSMS's API reports cost in credits only (creditCost per message, credits.balance on
the profile). It never returns a money amount, and number registration or monthly
number fees are billed on the account, not through the API. To see an estimated money
figure, set SPEND_CREDIT_PRICE to what one credit costs you (and SPEND_CURRENCY).
"""

from __future__ import annotations

import csv
import os
import threading
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from .client import SendResult
from .recipients import Recipient

DEFAULT_PATH = Path(__file__).resolve().parents[1] / "spend_log.csv"
COUNTRIES = ("US", "CA")
FIELDS = [
    "timestamp_utc",
    "campaign_id",
    "source",
    "sent",
    "failed",
    "credits_used",
    "us_sent",
    "ca_sent",
    "us_credits",
    "ca_credits",
    "balance_after",
    "aborted",
]

_lock = threading.Lock()


def log_path() -> Path:
    """SPEND_LOG_PATH if set, else spend_log.csv. A relative path is taken from the project
    folder, so the CLI and the service write the same file wherever they are started from."""
    return DEFAULT_PATH.parent / os.environ.get("SPEND_LOG_PATH", DEFAULT_PATH.name)


def record(
    campaign_id: str,
    source: str,
    result: SendResult,
    recipients: Iterable[Recipient],
    balance_after: float | None = None,
    path: Path | None = None,
    now: datetime | None = None,
) -> dict:
    """Append one row for a finished campaign and return it."""
    country_of = {r.phone: r.country for r in recipients}
    row = {
        "timestamp_utc": (now or datetime.now(timezone.utc)).isoformat(timespec="seconds"),
        "campaign_id": campaign_id,
        "source": source,
        "sent": result.sent,
        "failed": result.failed,
        "credits_used": _num(result.credits),
        "balance_after": "" if balance_after is None else _num(balance_after),
        "aborted": result.aborted or "",
    }
    for c in COUNTRIES:
        ok = [o for o in result.outcomes if o.ok and country_of.get(o.phone) == c]
        row[f"{c.lower()}_sent"] = len(ok)
        row[f"{c.lower()}_credits"] = _num(sum(o.credit_cost or 0 for o in ok))

    path = path or log_path()
    with _lock:
        path.parent.mkdir(parents=True, exist_ok=True)
        new = not path.exists() or path.stat().st_size == 0
        with path.open("a", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=FIELDS)
            if new:
                w.writeheader()
            w.writerow(row)
    return row


def read(path: Path | None = None) -> list[dict]:
    path = path or log_path()
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def totals(path: Path | None = None, month: str | None = None) -> dict:
    """All-time totals plus one entry per month (YYYY-MM, UTC). `month` limits both to that month."""
    rows = [r for r in read(path) if not month or r["timestamp_utc"].startswith(month)]
    by_month: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_month[r["timestamp_utc"][:7]].append(r)

    price = _float(os.environ.get("SPEND_CREDIT_PRICE"))
    out = {
        "log_file": str(path or log_path()),
        "all_time": _sum(rows, price),
        "by_month": {m: _sum(rs, price) for m, rs in sorted(by_month.items())},
        "latest_balance": next((_float(r["balance_after"]) for r in reversed(rows) if r.get("balance_after")), None),
        "cost_note": "BulkSMS reports credits only. Number registration and monthly number fees are billed "
        "on your BulkSMS account and are not included.",
    }
    if price is not None:
        out["credit_price"] = price
        out["currency"] = os.environ.get("SPEND_CURRENCY", "")
    return out


def _sum(rows: list[dict], price: float | None) -> dict:
    t = {
        "campaigns": len(rows),
        "sent": sum(int(r["sent"] or 0) for r in rows),
        "failed": sum(int(r["failed"] or 0) for r in rows),
        "credits_used": _num(sum(_float(r["credits_used"]) or 0 for r in rows)),
    }
    for c in COUNTRIES:
        k = c.lower()
        t[f"{k}_sent"] = sum(int(r.get(f"{k}_sent") or 0) for r in rows)
        t[f"{k}_credits"] = _num(sum(_float(r.get(f"{k}_credits")) or 0 for r in rows))
    if price is not None:
        t["estimated_cost"] = round(t["credits_used"] * price, 2)
    return t


def _float(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _num(x: float) -> float | int:
    """Credits are usually whole numbers; keep them that way in the CSV and JSON."""
    x = round(float(x), 4)
    return int(x) if x.is_integer() else x

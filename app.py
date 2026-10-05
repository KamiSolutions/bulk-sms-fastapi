"""FastAPI service: upload a CSV of US/Canadian numbers and send an SMS to each via BulkSMS.com.

Run:  uvicorn app:app --host 0.0.0.0 --port 8000
Docs: http://localhost:8000/docs
"""

from __future__ import annotations

import os
import secrets
import threading
import uuid
from datetime import datetime, timezone
from typing import Annotated

from fastapi import BackgroundTasks, Depends, FastAPI, File, Form, Header, HTTPException, UploadFile

from bulksms import BulkSMSClient, load_recipients, load_suppression_list
from bulksms.client import API_URL, estimate_parts
from bulksms.recipients import RecipientReport

MAX_UPLOAD_BYTES = int(os.environ.get("MAX_UPLOAD_BYTES", 5 * 1024 * 1024))

app = FastAPI(title="Bulk SMS (US & Canada)", version="1.0.0")

# Campaign status lives in memory: fine for one process. Use Redis or a database
# if you run several workers or need history to survive a restart.
_campaigns: dict[str, dict] = {}
_lock = threading.Lock()


def require_api_key(x_api_key: Annotated[str | None, Header()] = None) -> None:
    expected = os.environ.get("APP_API_KEY")
    if not expected:
        raise HTTPException(503, "APP_API_KEY is not configured on the server")
    if not x_api_key or not secrets.compare_digest(x_api_key, expected):
        raise HTTPException(401, "Missing or wrong X-API-Key header")


def make_client() -> BulkSMSClient:
    """Separate function so tests can swap in a fake BulkSMS API."""
    return BulkSMSClient()


async def _read_upload(upload: UploadFile) -> bytes:
    data = await upload.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, f"File larger than {MAX_UPLOAD_BYTES} bytes")
    return data


async def _parse(
    file: UploadFile,
    message: str | None,
    footer: str | None,
    phone_column: str | None,
    countries: str,
    suppress: UploadFile | None,
) -> RecipientReport:
    suppressed = load_suppression_list(await _read_upload(suppress)) if suppress else None
    allowed = frozenset(c.strip().upper() for c in countries.split(",") if c.strip())
    if not allowed <= {"US", "CA"}:
        raise HTTPException(422, "countries may only contain US and CA")
    try:
        return load_recipients(
            await _read_upload(file),
            template=message,
            phone_column=phone_column,
            allowed_countries=allowed,
            suppressed=suppressed,
            footer=footer,
        )
    except (ValueError, UnicodeDecodeError) as e:
        raise HTTPException(422, str(e))


def _preview(report: RecipientReport) -> dict:
    return {
        **report.summary(),
        "estimated_sms_parts": sum(estimate_parts(r.body)[1] for r in report.recipients),
        "rejected_rows": [vars(r) for r in report.rejected[:200]],
        "sample": [
            {"phone": r.phone, "country": r.country, "body": r.body, "encoding": estimate_parts(r.body)[0], "parts": estimate_parts(r.body)[1]}
            for r in report.recipients[:5]
        ],
    }


# Shared form fields for both endpoints.
CsvFile = Annotated[UploadFile, File(description="CSV with a phone column (phone, mobile, number, to ...)")]
Message = Annotated[str | None, Form(description="Template, {column} is replaced from each row. Optional if the CSV has a message column.")]
Footer = Annotated[str | None, Form(description='Appended to each message, e.g. "Reply STOP to opt out"')]
PhoneColumn = Annotated[str | None, Form()]
Countries = Annotated[str, Form()]
Suppress = Annotated[UploadFile | None, File(description="Opted-out numbers to skip")]


@app.get("/health")
def health() -> dict:
    return {"ok": True, "bulksms_api": (os.environ.get("BULKSMS_API_URL") or API_URL).rstrip("/")}


@app.post("/campaigns/preview", dependencies=[Depends(require_api_key)])
async def preview_campaign(
    file: CsvFile,
    message: Message = None,
    footer: Footer = None,
    phone_column: PhoneColumn = None,
    countries: Countries = "US,CA",
    suppress: Suppress = None,
) -> dict:
    """Validate the CSV and show what would be sent. Sends nothing."""
    report = await _parse(file, message, footer, phone_column, countries, suppress)
    return _preview(report)


@app.post("/campaigns", status_code=202, dependencies=[Depends(require_api_key)])
async def create_campaign(
    background: BackgroundTasks,
    file: CsvFile,
    message: Message = None,
    footer: Footer = None,
    phone_column: PhoneColumn = None,
    countries: Countries = "US,CA",
    suppress: Suppress = None,
) -> dict:
    """Validate the CSV, then send in the background. Poll GET /campaigns/{id} for progress."""
    report = await _parse(file, message, footer, phone_column, countries, suppress)
    if not report.recipients:
        raise HTTPException(422, {"detail": "No valid recipients", **_preview(report)})
    client = make_client()  # fail fast here if credentials are missing
    campaign_id = uuid.uuid4().hex
    with _lock:
        _campaigns[campaign_id] = {
            "id": campaign_id,
            "state": "queued",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "batches_done": 0,
            "batches_total": None,
            "preview": _preview(report),
            "result": None,
        }
    background.add_task(_run_campaign, campaign_id, client, report)
    return {"id": campaign_id, "state": "queued", **report.summary()}


@app.get("/campaigns/{campaign_id}", dependencies=[Depends(require_api_key)])
def get_campaign(campaign_id: str) -> dict:
    campaign = _campaigns.get(campaign_id)
    if not campaign:
        raise HTTPException(404, "Unknown campaign")
    return campaign


def _run_campaign(campaign_id: str, client: BulkSMSClient, report: RecipientReport) -> None:
    def progress(done: int, total: int) -> None:
        with _lock:
            _campaigns[campaign_id].update(batches_done=done, batches_total=total)

    with _lock:
        _campaigns[campaign_id]["state"] = "sending"
    try:
        with client:
            result = client.send(report.recipients, on_batch=progress)
    except Exception as e:  # keep the error visible to whoever polls the campaign
        with _lock:
            _campaigns[campaign_id].update(state="error", result={"error": str(e)})
        return
    with _lock:
        _campaigns[campaign_id].update(
            state="done" if not result.aborted else "aborted",
            result={**result.summary(), "messages": [vars(o) for o in result.outcomes]},
        )

#!/usr/bin/env python3
"""A local stand-in for api.bulksms.com, for trying the Bulk SMS service without
real credentials or real messages. Nothing leaves your machine.

Run:   python mock_bulksms_server.py            (listens on http://127.0.0.1:8001)
Then point the sender at it with BULKSMS_API_URL=http://127.0.0.1:8001/v1 (see .env.mock).

It behaves like the parts of the BulkSMS JSON API this project uses:
  GET  /v1/profile    account name and credit balance
  POST /v1/messages   accepts a batch, returns one entry per message
  GET  /v1/messages   what the mock has "sent" so far (newest first)
  POST /mock/reset    clear the outbox and restore credits

Built-in test cases:
  - token id "bad"                     -> 401 Unauthorized (see how a wrong token stops the run)
  - a number ending in 0666            -> that message comes back FAILED (BLOCKED)
  - MOCK_FAIL_FIRST=2                  -> the first 2 batches get a 503, so you can watch the retries
  - MOCK_CREDITS=5                     -> run out of credit part way through (403)
  - a repeated deduplication-id        -> the earlier response is returned, nothing is sent twice
"""

from __future__ import annotations

import base64
import os
import threading
import uuid
from datetime import datetime, timezone

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from bulksms.client import estimate_parts

START_CREDITS = float(os.environ.get("MOCK_CREDITS", 1000))
FAIL_SUFFIX = "0666"

app = FastAPI(title="Mock BulkSMS API", version="1.0.0")

_lock = threading.Lock()
_state: dict = {}


def _reset() -> None:
    _state.update(
        credits=START_CREDITS,
        outbox=[],
        dedup={},
        fail_first=int(os.environ.get("MOCK_FAIL_FIRST", 0)),
    )


_reset()


def _problem(status: int, title: str, detail: str = "") -> JSONResponse:
    return JSONResponse(
        {"type": "https://developer.bulksms.com/json/v1/errors", "title": title, "status": status, "detail": detail},
        status_code=status,
        media_type="application/problem+json",
    )


def _token_id(request: Request) -> str | None:
    header = request.headers.get("authorization", "")
    if not header.lower().startswith("basic "):
        return None
    try:
        user, _, secret = base64.b64decode(header[6:]).decode().partition(":")
    except ValueError:
        return None
    return user if user and secret else None


@app.middleware("http")
async def basic_auth(request: Request, call_next):
    if request.url.path.startswith("/v1/"):
        token = _token_id(request)
        if token is None:
            return _problem(401, "Unauthorized", "Missing HTTP Basic credentials")
        if token == "bad":
            return _problem(401, "Unauthorized", "The token id or secret is wrong (mock: token id 'bad')")
    return await call_next(request)


@app.exception_handler(404)
async def not_found(request: Request, exc):
    # Most likely someone sent a request for the Bulk SMS service (app.py) to the mock by mistake.
    return JSONResponse(
        {"detail": f"Not Found: this is the mock BulkSMS API, which has no {request.url.path}. "
                   "The Bulk SMS service is started separately with: uvicorn app:app --port 8010 --env-file .env.mock"},
        status_code=404,
    )


@app.get("/v1/profile")
def profile() -> dict:
    return {
        "id": "mock-1",
        "username": "mock-account",
        "credits": {"balance": _state["credits"], "isTransferAllowed": False},
    }


@app.post("/v1/messages", status_code=201)
async def send_messages(request: Request):
    dedup_id = request.query_params.get("deduplication-id")
    with _lock:
        if dedup_id and dedup_id in _state["dedup"]:
            print(f"[mock] repeat of batch {dedup_id[:8]}: returning the earlier response, nothing sent again")
            return JSONResponse(_state["dedup"][dedup_id], status_code=201)
        if _state["fail_first"] > 0:
            _state["fail_first"] -= 1
            print(f"[mock] simulating 503 for this batch ({_state['fail_first']} more to come)")
            return JSONResponse({"title": "Service Unavailable"}, status_code=503, headers={"Retry-After": "1"})

    try:
        messages = await request.json()
    except ValueError:
        return _problem(400, "Bad Request", "Body is not JSON")
    if isinstance(messages, dict):
        messages = [messages]
    if not isinstance(messages, list) or not messages:
        return _problem(400, "Bad Request", "Expected a message object or a list of them")

    max_parts = int(request.query_params.get("longMessageMaxParts", 3))
    now = datetime.now(timezone.utc).isoformat()
    out = []
    with _lock:
        for m in messages:
            to, body = str(m.get("to", "")), str(m.get("body", ""))
            if not to or not body:
                return _problem(400, "Bad Request", "Each message needs 'to' and 'body'")
            encoding, parts = estimate_parts(body)
            if parts > max_parts:
                return _problem(400, "Bad Request", f"Message to {to} needs {parts} parts, longMessageMaxParts is {max_parts}")
            failed = to.endswith(FAIL_SUFFIX)
            cost = 0.0 if failed else float(parts)
            entry = {
                "id": uuid.uuid4().hex[:12],
                "type": "SENT",
                "from": m.get("from", "mock-default"),
                "to": to.lstrip("+"),
                "body": body,
                "encoding": "UNICODE" if encoding == "UNICODE" else "TEXT",
                "protocolId": 0,
                "messageClass": 2,
                "numberOfParts": parts,
                "creditCost": cost,
                "submission": {"id": dedup_id or "", "date": now},
                "status": {"id": "FAILED.BLOCKED", "type": "FAILED", "subtype": "BLOCKED"}
                if failed
                else {"id": "ACCEPTED.null", "type": "ACCEPTED", "subtype": None},
                "relatedSentMessageId": None,
                "userSuppliedId": m.get("userSuppliedId"),
                "routingGroup": m.get("routingGroup", "STANDARD"),
            }
            out.append(entry)
        needed = sum(e["creditCost"] for e in out)
        if _state["credits"] < needed:
            return _problem(403, "Insufficient Credits", f"Balance {_state['credits']:g}, this batch needs {needed:g} (mock)")
        _state["credits"] -= needed
        _state["outbox"].extend(out)
        if dedup_id:
            _state["dedup"][dedup_id] = out

    for e in out:
        mark = "FAILED " if e["status"]["type"] == "FAILED" else "ok     "
        print(f"[mock] {mark} +{e['to']}  ({e['numberOfParts']} part, {e['encoding']}): {e['body']}")
    print(f"[mock] batch of {len(out)} done, credits left {_state['credits']:g}")
    return out


@app.get("/v1/messages")
def list_messages(limit: int = 100) -> list[dict]:
    return list(reversed(_state["outbox"]))[:limit]


@app.post("/mock/reset")
def reset() -> dict:
    with _lock:
        _reset()
    return {"ok": True, "credits": _state["credits"]}


if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("MOCK_PORT", 8001))
    print(f"Mock BulkSMS API on http://127.0.0.1:{port}/v1  (no real SMS is ever sent)")
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")

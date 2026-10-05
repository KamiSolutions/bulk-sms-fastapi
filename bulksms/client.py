"""Thin client for the BulkSMS.com JSON REST API (https://www.bulksms.com/developer/json/v1/)."""

from __future__ import annotations

import logging
import os
import time
import uuid
from dataclasses import dataclass, field
from typing import Callable, Iterable

import httpx

from .recipients import Recipient

log = logging.getLogger("bulksms")

API_URL = "https://api.bulksms.com/v1"
ROUTING_GROUPS = ("ECONOMY", "STANDARD", "PREMIUM")

# GSM 03.38 basic + extension characters. Anything else forces UCS-2 (Unicode).
_GSM7 = set(
    "@£$¥èéùìòÇ\nØø\rÅåΔ_ΦΓΛΩΠΨΣΘΞÆæßÉ !\"#¤%&'()*+,-./0123456789:;<=>?"
    "¡ABCDEFGHIJKLMNOPQRSTUVWXYZÄÖÑÜ§¿abcdefghijklmnopqrstuvwxyzäöñüà"
)
_GSM7_EXT = set("^{}\\[~]|€\f")


def estimate_parts(body: str) -> tuple[str, int]:
    """Return (encoding, number of SMS parts) the way carriers bill it."""
    if all(c in _GSM7 or c in _GSM7_EXT for c in body):
        length = sum(2 if c in _GSM7_EXT else 1 for c in body)
        return "GSM7", 1 if length <= 160 else -(-length // 153)
    length = sum(2 if ord(c) > 0xFFFF else 1 for c in body)  # UTF-16 code units
    return "UNICODE", 1 if length <= 70 else -(-length // 67)


class BulkSMSError(RuntimeError):
    def __init__(self, status: int, title: str, detail: str = ""):
        super().__init__(f"BulkSMS {status}: {title}{' - ' + detail if detail else ''}")
        self.status = status
        self.title = title
        self.detail = detail


@dataclass
class MessageOutcome:
    phone: str
    row: int
    ok: bool
    message_id: str | None = None
    status: str | None = None
    error: str | None = None
    credit_cost: float | None = None


@dataclass
class SendResult:
    outcomes: list[MessageOutcome] = field(default_factory=list)
    aborted: str | None = None  # set when sending stopped early (e.g. bad credentials)

    @property
    def sent(self) -> int:
        return sum(1 for o in self.outcomes if o.ok)

    @property
    def failed(self) -> int:
        return sum(1 for o in self.outcomes if not o.ok)

    @property
    def credits(self) -> float:
        return sum(o.credit_cost or 0 for o in self.outcomes)

    def summary(self) -> dict:
        return {"sent": self.sent, "failed": self.failed, "credits_used": self.credits, "aborted": self.aborted}


class BulkSMSClient:
    """Send messages in batches. Credentials are a BulkSMS API token (id + secret),
    created under Settings > API Tokens in the BulkSMS web app."""

    def __init__(
        self,
        token_id: str | None = None,
        token_secret: str | None = None,
        sender: str | None = None,
        routing_group: str | None = None,
        base_url: str | None = None,
        batch_size: int = 100,
        max_retries: int = 4,
        long_message_max_parts: int = 3,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.token_id = token_id or os.environ.get("BULKSMS_TOKEN_ID", "")
        self.token_secret = token_secret or os.environ.get("BULKSMS_TOKEN_SECRET", "")
        if not self.token_id or not self.token_secret:
            raise ValueError("Set BULKSMS_TOKEN_ID and BULKSMS_TOKEN_SECRET")
        self.sender = sender if sender is not None else os.environ.get("BULKSMS_FROM") or None
        self.routing_group = (routing_group or os.environ.get("BULKSMS_ROUTING_GROUP") or "STANDARD").upper()
        if self.routing_group not in ROUTING_GROUPS:
            raise ValueError(f"routing_group must be one of {ROUTING_GROUPS}")
        self.batch_size = batch_size
        self.max_retries = max_retries
        self.long_message_max_parts = long_message_max_parts
        self._sleep = sleep
        self._http = httpx.Client(
            base_url=base_url or os.environ.get("BULKSMS_API_URL", API_URL),
            auth=(self.token_id, self.token_secret),
            timeout=httpx.Timeout(30.0, connect=10.0),
            headers={"User-Agent": "glowhire-bulksms/1.0"},
            transport=transport,
        )

    def close(self) -> None:
        self._http.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # -- account -----------------------------------------------------------

    def profile(self) -> dict:
        """GET /profile: confirms credentials and shows the credit balance."""
        resp = self._http.get("/profile")
        self._raise_for_status(resp)
        return resp.json()

    # -- sending -----------------------------------------------------------

    def _payload(self, r: Recipient) -> dict:
        msg = {"to": r.phone, "body": r.body, "routingGroup": self.routing_group, "userSuppliedId": f"row-{r.row}"}
        if self.sender:
            msg["from"] = self.sender
        return msg

    def send(
        self,
        recipients: Iterable[Recipient],
        on_batch: Callable[[int, int], None] | None = None,
    ) -> SendResult:
        recipients = list(recipients)
        result = SendResult()
        batches = [recipients[i : i + self.batch_size] for i in range(0, len(recipients), self.batch_size)]
        for n, batch in enumerate(batches, start=1):
            if result.aborted:
                result.outcomes += [MessageOutcome(r.phone, r.row, False, error="not sent: " + result.aborted) for r in batch]
                continue
            try:
                data = self._post_batch([self._payload(r) for r in batch])
            except BulkSMSError as e:
                if e.status in (401, 403):
                    # Bad credentials or no credit: every later batch would fail too.
                    result.aborted = str(e)
                result.outcomes += [MessageOutcome(r.phone, r.row, False, error=str(e)) for r in batch]
            except httpx.HTTPError as e:
                # Gave up after retries. The batch may or may not have been accepted;
                # report it as failed and let the operator check the BulkSMS console.
                result.outcomes += [MessageOutcome(r.phone, r.row, False, error=f"network error: {e}") for r in batch]
            else:
                result.outcomes += self._match(batch, data)
            if on_batch:
                on_batch(n, len(batches))
        return result

    def _match(self, batch: list[Recipient], data: list[dict]) -> list[MessageOutcome]:
        by_row = {}
        for m in data:
            uid = m.get("userSuppliedId") or ""
            if uid.startswith("row-"):
                by_row[int(uid[4:])] = m
        outcomes = []
        for i, r in enumerate(batch):
            m = by_row.get(r.row) or (data[i] if i < len(data) else None)
            if m is None:
                outcomes.append(MessageOutcome(r.phone, r.row, False, error="no response entry"))
                continue
            status = (m.get("status") or {}).get("type")
            outcomes.append(
                MessageOutcome(
                    phone=r.phone,
                    row=r.row,
                    ok=status not in ("FAILED",),
                    message_id=m.get("id"),
                    status=status,
                    error=(m.get("status") or {}).get("subtype") if status == "FAILED" else None,
                    credit_cost=m.get("creditCost"),
                )
            )
        return outcomes

    def _post_batch(self, messages: list[dict]) -> list[dict]:
        # The same deduplication-id on every retry makes BulkSMS ignore a repeat
        # of a batch it already accepted, so a timeout never double-sends.
        params = {
            "deduplication-id": str(uuid.uuid4()),
            "auto-unicode": "true",
            "longMessageMaxParts": str(self.long_message_max_parts),
        }
        attempt = 0
        while True:
            try:
                resp = self._http.post("/messages", params=params, json=messages)
            except httpx.TransportError as e:
                if attempt >= self.max_retries:
                    raise
                delay = 2 ** (attempt + 1)
                log.warning("network error %s, retrying in %ss", e, delay)
            else:
                if resp.status_code in (429, 500, 502, 503, 504) and attempt < self.max_retries:
                    delay = _retry_after(resp) or 2 ** (attempt + 1)
                    log.warning("BulkSMS %s, retrying in %ss", resp.status_code, delay)
                else:
                    self._raise_for_status(resp)
                    return resp.json()
            attempt += 1
            self._sleep(delay)

    @staticmethod
    def _raise_for_status(resp: httpx.Response) -> None:
        if resp.status_code < 400:
            return
        try:
            problem = resp.json()
            title, detail = problem.get("title", resp.reason_phrase), problem.get("detail", "")
        except ValueError:
            title, detail = resp.reason_phrase, resp.text[:200]
        raise BulkSMSError(resp.status_code, title, detail)


def _retry_after(resp: httpx.Response) -> float | None:
    try:
        return min(float(resp.headers["Retry-After"]), 60.0)
    except (KeyError, ValueError):
        return None

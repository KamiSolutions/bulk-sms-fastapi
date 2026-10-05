"""Read recipients from a CSV file and render each person's message."""

from __future__ import annotations

import csv
import io
import string
from dataclasses import dataclass, field

from .numbers import InvalidNumber, normalize_nanp

# Header names accepted for the phone column (case-insensitive).
PHONE_COLUMNS = ("phone", "phone_number", "mobile", "cell", "number", "to", "msisdn")
# Optional per-row message column that overrides the shared template.
MESSAGE_COLUMNS = ("message", "body", "text")


@dataclass
class Recipient:
    row: int  # 1-based data row (header excluded)
    phone: str  # E.164
    country: str
    body: str
    fields: dict[str, str] = field(default_factory=dict)


@dataclass
class Rejected:
    row: int
    value: str
    reason: str


@dataclass
class RecipientReport:
    recipients: list[Recipient]
    rejected: list[Rejected]
    duplicates: int

    def summary(self) -> dict:
        by_country: dict[str, int] = {}
        for r in self.recipients:
            by_country[r.country] = by_country.get(r.country, 0) + 1
        return {
            "valid": len(self.recipients),
            "rejected": len(self.rejected),
            "duplicates_removed": self.duplicates,
            "by_country": by_country,
        }


class _Blank(dict):
    """Leave unknown {placeholders} empty instead of raising KeyError."""

    def __missing__(self, key):
        return ""


def render(template: str, fields: dict[str, str]) -> str:
    return string.Formatter().vformat(template, (), _Blank(fields)).strip()


def template_placeholders(template: str) -> set[str]:
    return {name for _, name, _, _ in string.Formatter().parse(template) if name}


def _find_column(headers: list[str], candidates: tuple[str, ...]) -> str | None:
    lowered = {h.strip().lower(): h for h in headers if h}
    for c in candidates:
        if c in lowered:
            return lowered[c]
    return None


def load_recipients(
    data: str | bytes,
    template: str | None = None,
    phone_column: str | None = None,
    allowed_countries: frozenset[str] = frozenset({"US", "CA"}),
    dedupe: bool = True,
    suppressed: set[str] | None = None,
    footer: str | None = None,
) -> RecipientReport:
    """Parse CSV text. Every row needs a phone; the body comes from `template`
    (with {column} placeholders filled from that row) or from a message column.

    `suppressed` is a set of E.164 numbers that opted out (they are skipped);
    `footer` is appended to every message, e.g. "Reply STOP to opt out"."""
    if isinstance(data, bytes):
        data = data.decode("utf-8-sig")  # tolerate Excel's BOM
    elif data.startswith("﻿"):
        data = data[1:]

    sample = data[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    reader = csv.DictReader(io.StringIO(data), dialect=dialect)
    headers = reader.fieldnames or []

    phone_col = phone_column or _find_column(headers, PHONE_COLUMNS)
    if not phone_col or phone_col not in headers:
        raise ValueError(
            f"CSV needs a phone column (one of: {', '.join(PHONE_COLUMNS)}); found headers: {headers}"
        )
    message_col = _find_column(headers, MESSAGE_COLUMNS)
    if not template and not message_col:
        raise ValueError("Provide a message template or a 'message' column in the CSV")

    recipients: list[Recipient] = []
    rejected: list[Rejected] = []
    seen: set[str] = set()
    duplicates = 0

    for i, row in enumerate(reader, start=1):
        fields = {(k or "").strip(): (v or "").strip() for k, v in row.items() if k}
        raw = fields.get(phone_col.strip(), "")
        if not raw and not any(fields.values()):
            continue  # skip blank lines
        try:
            number = normalize_nanp(raw, allowed_countries)
        except InvalidNumber as e:
            rejected.append(Rejected(row=i, value=raw, reason=str(e)))
            continue
        if dedupe and number.e164 in seen:
            duplicates += 1
            continue
        seen.add(number.e164)
        if suppressed and number.e164 in suppressed:
            rejected.append(Rejected(row=i, value=raw, reason="opted out"))
            continue

        row_message = fields.get(message_col.strip(), "") if message_col else ""
        body = render(row_message or template or "", fields)
        if body and footer:
            body = f"{body} {footer.strip()}"
        if not body:
            rejected.append(Rejected(row=i, value=raw, reason="empty message"))
            continue
        recipients.append(Recipient(row=i, phone=number.e164, country=number.country, body=body, fields=fields))

    return RecipientReport(recipients=recipients, rejected=rejected, duplicates=duplicates)


def load_suppression_list(data: str | bytes) -> set[str]:
    """Numbers that must never be messaged (one per line, or a CSV with a phone column)."""
    if isinstance(data, bytes):
        data = data.decode("utf-8-sig")
    out: set[str] = set()
    for line in data.splitlines():
        for cell in line.replace(";", ",").split(","):
            try:
                out.add(normalize_nanp(cell).e164)
            except InvalidNumber:
                pass
    return out

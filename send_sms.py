#!/usr/bin/env python3
"""Send a bulk SMS to US/Canadian numbers from a CSV, via BulkSMS.com.

Examples
  # Check the file and preview messages; nothing is sent
  python send_sms.py recipients.csv -m "Hi {first_name}, your interview is on {date}." --dry-run

  # Send for real (asks for confirmation unless --yes)
  python send_sms.py recipients.csv -m "Hi {first_name} ..." --footer "Reply STOP to opt out"
"""

from __future__ import annotations

import argparse
import csv
import logging
import os
import sys
from pathlib import Path

from bulksms import BulkSMSClient, BulkSMSError, load_recipients, load_suppression_list
from bulksms.client import estimate_parts


def _load_dotenv(path: Path) -> None:
    """Minimal .env reader so the script has no extra dependency."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def main(argv: list[str] | None = None) -> int:
    _load_dotenv(Path(__file__).with_name(".env"))
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("csv", type=Path, help="CSV with a phone column (phone, mobile, number, to ...)")
    p.add_argument("-m", "--message", help="Message template; {column} is replaced from each row")
    p.add_argument("--message-file", type=Path, help="Read the template from a file instead")
    p.add_argument("--phone-column", help="Name of the phone column if it isn't detected")
    p.add_argument("--from", dest="sender", help="Sender number (default: BULKSMS_FROM)")
    p.add_argument("--routing-group", choices=["ECONOMY", "STANDARD", "PREMIUM"], help="Default: BULKSMS_ROUTING_GROUP or STANDARD")
    p.add_argument("--countries", default="US,CA", help="Allowed countries (default: US,CA)")
    p.add_argument("--footer", help='Appended to every message, e.g. "Reply STOP to opt out"')
    p.add_argument("--suppress", type=Path, help="File of opted-out numbers to skip")
    p.add_argument("--batch-size", type=int, default=100)
    p.add_argument("--report", type=Path, help="Write per-recipient results to this CSV")
    p.add_argument("--dry-run", action="store_true", help="Validate and preview only; send nothing")
    p.add_argument("-y", "--yes", action="store_true", help="Don't ask for confirmation")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING, format="%(levelname)s %(message)s")

    template = args.message
    if args.message_file:
        template = args.message_file.read_text().strip()
    suppressed = load_suppression_list(args.suppress.read_bytes()) if args.suppress else None
    countries = frozenset(c.strip().upper() for c in args.countries.split(",") if c.strip())

    try:
        report = load_recipients(
            args.csv.read_bytes(),
            template=template,
            phone_column=args.phone_column,
            allowed_countries=countries,
            suppressed=suppressed,
            footer=args.footer,
        )
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    s = report.summary()
    parts = sum(estimate_parts(r.body)[1] for r in report.recipients)
    print(f"Recipients: {s['valid']} valid {s['by_country']}, {s['rejected']} rejected, {s['duplicates_removed']} duplicates removed")
    print(f"Estimated SMS parts: {parts}")
    for rej in report.rejected[:20]:
        print(f"  row {rej.row}: {rej.value!r} skipped ({rej.reason})")
    if len(report.rejected) > 20:
        print(f"  ... and {len(report.rejected) - 20} more")
    for r in report.recipients[:3]:
        enc, n = estimate_parts(r.body)
        print(f"\n  To {r.phone} ({r.country}, {n} part{'s' if n > 1 else ''}, {enc}):\n  {r.body}")

    if not report.recipients:
        print("\nNothing to send.")
        return 1
    if args.dry_run:
        print("\nDry run: nothing was sent.")
        return 0
    if not args.yes:
        if input(f"\nSend {s['valid']} messages now? [y/N] ").strip().lower() not in ("y", "yes"):
            print("Cancelled.")
            return 1

    try:
        client = BulkSMSClient(sender=args.sender, routing_group=args.routing_group, batch_size=args.batch_size)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    with client:
        try:
            profile = client.profile()
            print(f"Account {profile.get('username')}: {profile.get('credits', {}).get('balance')} credits available")
        except BulkSMSError as e:
            print(f"error: could not log in to BulkSMS: {e}", file=sys.stderr)
            return 2
        result = client.send(report.recipients, on_batch=lambda n, total: print(f"  batch {n}/{total} submitted"))

    print(f"\nSent {result.sent}, failed {result.failed}, credits used {result.credits:g}")
    if result.aborted:
        print(f"Stopped early: {result.aborted}", file=sys.stderr)
    if args.report:
        with args.report.open("w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["row", "phone", "ok", "message_id", "status", "error", "credit_cost"])
            for o in result.outcomes:
                w.writerow([o.row, o.phone, o.ok, o.message_id, o.status, o.error, o.credit_cost])
            for rej in report.rejected:
                w.writerow([rej.row, rej.value, False, "", "REJECTED", rej.reason, ""])
        print(f"Report written to {args.report}")
    return 0 if result.failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

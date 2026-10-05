#!/usr/bin/env python3
"""Show SMS spend totals from the spend log that send_sms.py and the service write.

Examples
  python spend_report.py                       # all time, with a line per month
  python spend_report.py --month 2026-10       # one month only
  python spend_report.py --env-file .env.mock  # the log written by mock runs
  python spend_report.py --json                # machine-readable

BulkSMS reports credits, not money. Set SPEND_CREDIT_PRICE (what one credit costs you)
and SPEND_CURRENCY in .env to also see an estimated cost.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from bulksms import spend
from send_sms import _load_dotenv


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--month", help="Only this month, as YYYY-MM (UTC)")
    p.add_argument("--json", action="store_true", help="Print JSON instead of a table")
    p.add_argument("--env-file", type=Path, default=Path(__file__).with_name(".env"),
                   help="Settings file to read for SPEND_LOG_PATH and SPEND_CREDIT_PRICE (default: .env)")
    args = p.parse_args(argv)
    if args.month and not re.fullmatch(r"\d{4}-\d{2}", args.month):
        print("error: --month must look like 2026-10", file=sys.stderr)
        return 2
    _load_dotenv(args.env_file)

    t = spend.totals(month=args.month)
    if args.json:
        print(json.dumps(t, indent=2))
        return 0
    if not t["all_time"]["campaigns"]:
        print(f"No campaigns logged{' for ' + args.month if args.month else ''} in {t['log_file']}")
        return 0

    cost = "credit_price" in t
    header = f"{'Month':<9} {'Campaigns':>9} {'Sent':>7} {'Failed':>7} {'US sent':>8} {'CA sent':>8} {'Credits':>9}"
    if cost:
        header += f" {'Est. cost':>11}"
    print(f"Spend log: {t['log_file']}\n")
    print(header)
    print("-" * len(header))
    for label, s in [*t["by_month"].items(), ("Total", t["all_time"])]:
        if label == "Total":
            print("-" * len(header))
        line = (f"{label:<9} {s['campaigns']:>9} {s['sent']:>7} {s['failed']:>7} "
                f"{s['us_sent']:>8} {s['ca_sent']:>8} {s['credits_used']:>9g}")
        if cost:
            line += f" {s['estimated_cost']:>11.2f}"
        print(line)
    if cost:
        print(f"\nEstimated cost uses {t['credit_price']:g} {t['currency']} per credit (SPEND_CREDIT_PRICE).".replace("  ", " "))
    if t["latest_balance"] is not None:
        print(f"Credit balance after the last campaign: {t['latest_balance']:g}")
    print(t["cost_note"])
    return 0


if __name__ == "__main__":
    sys.exit(main())

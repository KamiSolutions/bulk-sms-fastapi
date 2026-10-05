# Bulk SMS to the US and Canada: Technical Summary

**Prepared by:** GlowHire
**Date:** 5 October 2026
**Status:** Code complete and tested against a mock API. Live sending is blocked until a US/Canada sender number is approved on our BulkSMS.com account.

---

## 1. Summary

We built a Python tool that sends bulk SMS to US and Canadian mobile numbers through our existing BulkSMS.com account. Recipients are loaded from a CSV file. It comes in two forms that share one core library:

- a **command-line script** for one-off campaigns, and
- a **FastAPI web service** with a CSV upload endpoint, so other systems or a web form can trigger sends.

The software side is done. The open item is regulatory: US and Canadian carriers do not accept alphanumeric sender names (e.g. "GlowHire"), so we need a registered US/Canadian sending number. A South African company can get one through BulkSMS without a US entity, but approval takes roughly 2 to 8 weeks.

## 2. What was built

| Component | Purpose |
|---|---|
| `bulksms/numbers.py` | Normalises phone numbers to E.164 (`+14165550123`) and classifies each as US or CA by area code |
| `bulksms/recipients.py` | CSV loader, message templating, opt-out list, footer, deduplication |
| `bulksms/client.py` | BulkSMS JSON REST API client (batching, retries, idempotency) |
| `send_sms.py` | Command-line interface |
| `app.py` | FastAPI service |
| `tests/` | 34 automated tests against a fake BulkSMS API (all passing; no real messages sent) |

**Stack:** Python 3.10+, `httpx`, `fastapi`, `uvicorn`, `python-multipart`, `pytest`. No database.

**API:** BulkSMS JSON REST API v1, `POST /v1/messages`, HTTP Basic auth using an API token.

## 3. How it works

### Input
- Any CSV with a phone column. The column is detected automatically (`phone`, `mobile`, `cell`, `number`, `to`, `msisdn`, etc.) or can be named explicitly.
- Comma, semicolon and tab separators and Excel UTF-8 BOM are handled.
- The message is a template; any CSV column can be a placeholder, e.g. `Hi {first_name}, your interview is on {date}.` A `message` column overrides the template per row.

### Validation (before anything is sent)
Every number is cleaned to E.164. Rows are rejected, with the reason reported, if they are:
- not +1 numbers, or not 10 digits;
- +1 numbers outside the US/Canada (Caribbean and US territories such as 876, 809, 787), which bill as international;
- toll-free (800, 888 ...) or N11 numbers;
- duplicates, or on the opt-out (suppression) list.

### Sending
- Messages are sent in batches of 100 per API request.
- Each batch carries a `deduplication-id` that is reused on retry, so a timeout followed by a retry cannot double-send.
- `429`, `5xx` and network errors are retried up to 4 times with backoff, honouring `Retry-After`.
- `401`/`403` (bad token, no credit, sender not allowed) stops the run immediately; unsent rows are reported.
- Unicode is enabled automatically for emoji or non-GSM characters. This drops a single SMS from 160 to 70 characters, so the preview shows estimated message parts (cost) before sending. Messages are capped at 3 parts.

## 4. How to run it

### Setup
```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in values
```

| Variable | Meaning |
|---|---|
| `BULKSMS_TOKEN_ID`, `BULKSMS_TOKEN_SECRET` | API token (BulkSMS web app > Settings > API Tokens) |
| `BULKSMS_FROM` | Approved sending number in E.164, e.g. `+18885550100` |
| `BULKSMS_ROUTING_GROUP` | `ECONOMY`, `STANDARD` (default) or `PREMIUM` |
| `APP_API_KEY` | Shared secret clients send as `X-API-Key` to the FastAPI service |

### Command line
```bash
# Validate and preview only; sends nothing
python send_sms.py recipients.csv -m "Hi {first_name}, your interview is on {date}." \
    --footer "Reply STOP to opt out" --dry-run

# Send, skipping opted-out numbers, and write a per-number report
python send_sms.py recipients.csv --message-file msg.txt --footer "Reply STOP to opt out" \
    --suppress optouts.csv --report results.csv
```
The script asks for confirmation before sending (`-y` skips it). Exit code is 0 only if every message was accepted.

### FastAPI service
```bash
uvicorn app:app --host 0.0.0.0 --port 8000 --env-file .env
# Interactive API docs at http://localhost:8000/docs
```

| Endpoint | Purpose |
|---|---|
| `POST /campaigns/preview` | Upload CSV; returns valid/rejected counts, sample messages, estimated SMS parts. Sends nothing. |
| `POST /campaigns` | Upload CSV and send. Returns `202` with a campaign id; sending runs in the background. |
| `GET /campaigns/{id}` | Progress, then per-number results (BulkSMS message id, status, credits). |
| `GET /health` | Liveness check. |

All campaign endpoints require the `X-API-Key` header. Uploads are capped at 5 MB.

### Tests
```bash
pytest -q
```

### Known limitations (v1)
- Campaign status is held in memory: lost on restart, single worker process only. Replace with Redis or a database before scaling out.
- STOP replies are not yet processed automatically. They arrive in BulkSMS as inbound messages; for now they must be added to the suppression file manually. A webhook handler is the natural next step.
- No scheduling. Quiet-hours compliance (see section 5) is the operator's responsibility.

## 5. Sender number and compliance

### Why we need a number
- **No alphanumeric sender IDs** in the US (or South Africa), per BulkSMS: carriers reject them.
- **Unregistered US traffic is blocked.** All commercial (A2P) SMS to the US must come from a number registered with The Campaign Registry (10DLC), a verified toll-free number, or a short code.

### Getting one through BulkSMS (recommended first route)
We do **not** need a US company or EIN. We apply inside our existing BulkSMS account (Settings > Sender IDs > request an incoming number), and BulkSMS registers our brand and campaign with the US registry on our behalf.

What BulkSMS asks for:
- legal company name and brand name (must match our website);
- business ID: for a non-US entity, CIPC company registration number plus VAT number;
- company type;
- use case and sample messages that include our brand name and "Reply STOP to opt out", and whether messages contain links or phone numbers;
- proof of consent: how and where recipients opted in (screenshots of the opt-in form preferred);
- a working website showing the brand, opt-in and privacy policy. Reviewers check this closely.

### Timelines and cost

| Item | Timeline | Cost |
|---|---|---|
| US 10DLC number (BulkSMS) | 16 to 21 days after payment | Shown in-account; not public |
| Canada toll-free number (BulkSMS) | 1 to 8 weeks verification | USD 24.99 one-off registration, from USD 1.50/month (6 or 12 months upfront) |
| Canada 10DLC (BulkSMS) | On request | On request |
| Per-message cost | n/a | BulkSMS credits per message part; varies by routing group |

Canada has no mandatory registry like the US, but Rogers, Bell and Telus filter unregistered A2P traffic heavily, so a registered or verified number is still needed in practice.

### Legal requirements

**Canada (CASL):**
- prior consent: express, or implied (limited to about 2 years, e.g. after a transaction);
- identify the business and give contact details in every message (a link is acceptable);
- a working unsubscribe (STOP) in every message;
- keep records of how and when each person consented;
- penalties up to CAD 10 million per violation for businesses.

**United States (TCPA / CTIA guidelines):**
- prior opt-in consent before the first message;
- honour STOP immediately;
- identify the sender in the message.

**Operational rules for both:**
- send only between roughly 8am and 9pm in the recipient's local time (some US states are stricter; the two countries span many time zones);
- avoid SHAFT content (sex, hate, alcohol, firearms, tobacco/cannabis);
- do not use public URL shorteners (bit.ly etc.); use our own domain for links.

The tool supports compliance with `--footer "Reply STOP to opt out"` and the `--suppress` opt-out list, but consent collection and record-keeping happen outside the tool.

### Fallback providers
If BulkSMS cannot approve us, Twilio, Telnyx, Bandwidth, Sinch and Vonage all rent US toll-free numbers to foreign businesses. Since February 2026 toll-free verification requires a business registration number and issuing country, and non-US/Canadian registration numbers are accepted. Approval is typically days to a few weeks. A toll-free number can text both the US and Canada, though Canadian carriers may filter toll-free more aggressively than registered long codes. Switching provider would mean replacing `bulksms/client.py`; the CSV, validation, CLI and API layers stay the same.

Options ruled out:
- **Sole-proprietor 10DLC:** needs a US/Canadian mobile for verification; register as the company instead.
- **Short codes:** thousands of USD and months of lead time; not justified for this volume.

## 6. Open questions for BulkSMS support

1. Do you sell **US toll-free numbers** to our account, or only 10DLC? What is the price of each?
2. What is the **monthly cost and per-message rate** for a US 10DLC number on our account?
3. Can one number send to **both US and Canada**, or do we need one per country?
4. What is your **approval rate / experience with South African companies** applying with CIPC + VAT numbers?
5. Are there **registry or carrier fees** (TCR brand/campaign vetting) on top of your fees?
6. How are **inbound STOP replies** delivered to us (webhook, API polling), and does BulkSMS block opted-out numbers automatically at carrier level?
7. Is there a **throughput limit** (messages per second/day) for our registered number?

## 7. Recommended next steps

1. Send the questions above to BulkSMS support before paying for a number.
2. Make sure the website shows the brand, an SMS opt-in form and a privacy policy, since it is part of the review.
3. Apply for the US number (and Canada toll-free) in the BulkSMS account.
4. While approval is pending: add an inbound-STOP webhook and persistent campaign storage, and run a dry-run with a real recipient list.
5. Once approved: set `BULKSMS_FROM`, send a small test campaign to internal US/Canadian numbers, then go live.
6. If BulkSMS declines or stalls past 8 weeks, move to a toll-free number from Twilio or Telnyx.

## Sources
- BulkSMS JSON API: https://www.bulksms.com/developer/json/v1/
- BulkSMS USA getting started: https://www.bulksms.com/hello/usa/getting-started.htm
- BulkSMS US number applications (10DLC): https://www.bulksms.com/products/usa-incoming-numbers.htm
- BulkSMS Canada: https://www.bulksms.com/hello/canada/getting-started.htm
- BulkSMS CASL guide: https://www.bulksms.com/resources/regulations/canadian-requirements-for-sending-commercial-sms-messages.htm
- BulkSMS FAQ: https://www.bulksms.com/support/frequently-asked-questions.htm
- Bandwidth toll-free verification BRN requirements (Feb 2026): https://www.bandwidth.com/support/en/articles/13145815-updated-toll-free-verification-brn-field-requirements-on-february-17-2026
- Twilio BRN changelog: https://www.twilio.com/en-us/changelog/business-registration-numbers-required-for-toll-free-messaging-p

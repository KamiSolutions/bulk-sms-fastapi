# Bulk SMS to the US and Canada (BulkSMS.com)

Two ways to send the same campaign, sharing one core package (`bulksms/`):

- **`send_sms.py`**: a command-line script for one-off sends from a CSV.
- **`app.py`**: a FastAPI service with a CSV upload endpoint, for sending from another system or a web form.

Both use the [BulkSMS JSON REST API](https://www.bulksms.com/developer/json/v1/) (`POST /v1/messages`, HTTP Basic auth with an API token).

## Setup

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # then fill in the values
```

| Variable | What it is |
|---|---|
| `BULKSMS_TOKEN_ID`, `BULKSMS_TOKEN_SECRET` | API token from the BulkSMS web app, Settings > API Tokens |
| `BULKSMS_FROM` | The sending number in E.164, e.g. `+18885550100` (see "US and Canada rules" below) |
| `BULKSMS_ROUTING_GROUP` | `ECONOMY`, `STANDARD` (default) or `PREMIUM` |
| `APP_API_KEY` | Shared secret clients send as `X-API-Key` to the FastAPI service |

The CLI reads `.env` itself (or another file with `--env-file`). For the service, export the variables or use `uvicorn --env-file .env`.

## The CSV

Any CSV with a phone column works. The column is found automatically if it's called `phone`, `phone_number`, `mobile`, `cell`, `number`, `to` or `msisdn` (otherwise pass `--phone-column` / `phone_column`). Comma, semicolon and tab separators, and Excel's UTF-8 BOM, are handled.

```csv
first_name,phone,date
Ava,(416) 555-0123,Tuesday
Liam,+1 212 555 0147,Wednesday
```

The message template can use any column as a placeholder: `Hi {first_name}, your interview is on {date}.` A `message` column in the CSV overrides the template for that row.

Every number is cleaned to E.164 (`+14165550123`). Accepted forms include `4165550123`, `(416) 555-0123`, `1-416-555-0123`, `+1 416 555 0123`, `011 1 416…` and numbers with an extension. Each row is classed as US or CA by area code. These are skipped, with the reason shown:

- numbers that aren't +1, or don't have 10 digits
- +1 numbers outside the US and Canada (Jamaica 876, Dominican Republic 809, Puerto Rico 787, etc.), which bill as international
- toll-free (800, 888 …) and N11 numbers
- duplicates, and anything on an opt-out list

## Command line

```bash
# Check the file and preview; sends nothing
python send_sms.py sample_recipients.csv -m "Hi {first_name}, your interview is on {date}." \
    --footer "Reply STOP to opt out" --dry-run

# Send (asks to confirm; add -y to skip), skip opted-out numbers, save a per-number report
python send_sms.py recipients.csv --message-file msg.txt --footer "Reply STOP to opt out" \
    --suppress optouts.csv --report results.csv
```

Other options: `--countries US` (only one country), `--from`, `--routing-group`, `--batch-size`. Run `python send_sms.py -h` for the full list. The exit code is 0 only if every message was accepted.

## FastAPI service

```bash
uvicorn app:app --host 0.0.0.0 --port 8000 --env-file .env
# Interactive docs: http://localhost:8000/docs
```

| Endpoint | Purpose |
|---|---|
| `POST /campaigns/preview` | Upload a CSV, see valid/rejected counts, sample messages and SMS part estimate. Sends nothing. |
| `POST /campaigns` | Upload a CSV and send. Returns `202` with a campaign id; sending runs in the background. |
| `GET /campaigns/{id}` | Progress and, when finished, the per-number result (BulkSMS message id, status, credits). |
| `GET /health` | Liveness check. |

Both POST endpoints take multipart form fields: `file` (the CSV), `message`, optional `footer`, `phone_column`, `countries` (default `US,CA`) and `suppress` (a file of opted-out numbers).

```bash
curl -H "X-API-Key: $APP_API_KEY" -F file=@recipients.csv \
     -F 'message=Hi {first_name}, your interview is on {date}.' \
     -F 'footer=Reply STOP to opt out' http://localhost:8000/campaigns
```

Campaign status is kept in memory, so it's lost on restart and only works with a single worker process. Swap `_campaigns` for Redis or a database before scaling out. Uploads are capped at 5 MB (`MAX_UPLOAD_BYTES`).

## How sending works

- Messages go to BulkSMS in batches of 100 per request.
- Each batch carries a `deduplication-id`, reused on retries, so a timeout followed by a retry can't send the same batch twice.
- `429` and `5xx` responses and network errors are retried up to 4 times with backoff (honouring `Retry-After`).
- A `401`/`403` (bad token, no credit, sender not allowed) stops the run; the remaining rows are reported as not sent.
- `auto-unicode=true` is set, so messages with emoji or characters outside the GSM alphabet are sent as Unicode. Unicode cuts a single SMS from 160 to 70 characters, and the preview shows the estimated parts so you can see the cost before sending. Long messages are capped at 3 parts.

## US and Canada rules (check these before going live)

Sending to North America is more regulated than most countries. Confirm the specifics with BulkSMS support for your account, because this is general guidance rather than something checked against your account:

1. **No alphanumeric sender IDs.** US and Canadian carriers don't accept a name like "GlowHire" as the sender. You need a real number, set in `BULKSMS_FROM`.
2. **US numbers must be registered.** US carriers block unregistered application-to-person traffic. The sending number needs to be either a **verified toll-free number** or a **10DLC** number with an approved brand and campaign (registered through The Campaign Registry), or a short code for high volume. Ask BulkSMS which of these they can provide or let you bring.
3. **Consent and opt-out.** The US (TCPA) and Canada (CASL) require prior consent from each recipient, the sender to be identified in the message, and a working opt-out. Include something like "Reply STOP to opt out" (`--footer`), and keep the opted-out numbers in a file passed via `--suppress` / `suppress`. BulkSMS's inbound messages (or webhooks) are where STOP replies arrive.
4. **Quiet hours.** Avoid sending outside about 8am to 9pm in the recipient's local time; some US states set stricter windows. Canada spans six time zones and the US spans six more, so schedule large sends with that in mind.
5. **Content.** Carriers filter SHAFT content (sex, hate, alcohol, firearms, tobacco/cannabis) and URL shorteners like bit.ly. Use your own domain for links.

## Trying it without a BulkSMS account

`mock_bulksms_server.py` is a local fake of the BulkSMS API, and `.env.mock` points the CLI (`--env-file .env.mock`) and the service (`uvicorn ... --env-file .env.mock`) at it. `mock_data/` has a CSV full of valid and invalid US/Canadian numbers to try. Step-by-step PowerShell instructions and expected output: [docs/testing-with-mock.md](docs/testing-with-mock.md).

## Tests

```bash
pytest -q
```

The tests use a fake BulkSMS API (`tests/fake_bulksms.py`), so they never send a real message or need credentials. They cover number cleanup, CSV parsing, batching, retry with the same deduplication id, aborting on bad credentials, and the FastAPI endpoints.

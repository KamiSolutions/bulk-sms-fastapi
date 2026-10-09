# Bulk SMS API for the US and Canada (BulkSMS.com)

A FastAPI service that takes a CSV of US and Canadian numbers and sends an SMS to each through the [BulkSMS JSON REST API](https://www.bulksms.com/developer/json/v1/) (`POST /v1/messages`, HTTP Basic auth with an API token). Other systems or a web form call it over HTTP; it validates every number, sends in the background and keeps a spend log.

The core logic lives in the `bulksms/` package (number cleanup, CSV loading, the BulkSMS client and spend log) and `app.py` is the web service on top of it.

## Setup (Windows PowerShell)

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env   # then fill in the values in Notepad
```

If `Activate.ps1` is blocked ("running scripts is disabled"), run `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass` in that window and try again.

| Variable | What it is |
|---|---|
| `BULKSMS_TOKEN_ID`, `BULKSMS_TOKEN_SECRET` | API token from the BulkSMS web app, Settings > API Tokens |
| `BULKSMS_FROM` | The sending number in E.164, e.g. `+18885550100` (see "US and Canada rules" below) |
| `BULKSMS_ROUTING_GROUP` | `ECONOMY`, `STANDARD` (default) or `PREMIUM` |
| `APP_API_KEY` | Shared secret clients send as `X-API-Key` to the service |
| `SPEND_LOG_PATH` | Optional. Where the spend log is written (default `spend_log.csv` in the project folder) |
| `SPEND_CREDIT_PRICE`, `SPEND_CURRENCY` | Optional. What one BulkSMS credit costs you, to show an estimated cost next to credits |
| `MAX_UPLOAD_BYTES` | Optional. Upload size limit (default 5 MB) |

## Run it

```powershell
uvicorn app:app --port 8010 --env-file .env
```

Port 8010 is only an example; use any free port. Interactive docs are at http://localhost:8010/docs, where you can try every endpoint from the browser (click **Try it out** and put your key in the `x-api-key` field). Add `--host 0.0.0.0` to accept connections from other machines.

Check it's up and which BulkSMS API it's pointed at:

```powershell
Invoke-RestMethod http://localhost:8010/health
```

## Endpoints

| Endpoint | Purpose |
|---|---|
| `POST /campaigns/preview` | Upload a CSV, see valid/rejected counts, sample messages and SMS part estimate. Sends nothing. |
| `POST /campaigns` | Upload a CSV and send. Returns `202` with a campaign id; sending runs in the background. |
| `GET /campaigns/{id}` | Progress and, when finished, the per-number result (BulkSMS message id, status, credits) and the spend row it logged. |
| `GET /spend` | Credits used and messages sent, all time and per month (`?month=2026-10` for one month). |
| `GET /health` | Liveness check, no key needed. |

Every endpoint except `/health` needs the `X-API-Key` header. Both POST endpoints take multipart form fields:

| Field | |
|---|---|
| `file` | The CSV (required) |
| `message` | Template, e.g. `Hi {first_name}, your interview is on {date}.` Optional if the CSV has a `message` column |
| `footer` | Appended to each message, e.g. `Reply STOP to opt out` |
| `suppress` | A file of opted-out numbers to skip |
| `phone_column` | Name of the phone column, if it isn't detected automatically |
| `countries` | `US,CA` (default), `US` or `CA` |

### Calling it with curl

Use `curl.exe`, not `curl` (in Windows PowerShell 5.1, `curl` is an alias for a different command).

```powershell
# Preview: sends nothing
curl.exe -s -H "X-API-Key: <your key>" `
  -F "file=@recipients.csv" `
  -F "suppress=@optouts.csv" `
  -F "message=Hi {first_name}, your interview is on {date}." `
  -F "footer=Reply STOP to opt out" `
  http://localhost:8010/campaigns/preview

# Send: same fields, posted to /campaigns. Returns {"id": "...", "state": "queued", ...}
curl.exe -s -H "X-API-Key: <your key>" -F "file=@recipients.csv" `
  -F "message=Hi {first_name}" -F "footer=Reply STOP to opt out" `
  http://localhost:8010/campaigns

# Progress and results
curl.exe -s -H "X-API-Key: <your key>" http://localhost:8010/campaigns/<campaign id>

# Spend
curl.exe -s -H "X-API-Key: <your key>" "http://localhost:8010/spend?month=2026-10"
```

### Or with Invoke-RestMethod (PowerShell 7 or later)

`-Form` needs PowerShell 7 (`pwsh`); it isn't in Windows PowerShell 5.1.

```powershell
$h = @{ "X-API-Key" = "<your key>" }
$form = @{
  file    = Get-Item .\recipients.csv
  message = "Hi {first_name}, your interview is on {date}."
  footer  = "Reply STOP to opt out"
}
Invoke-RestMethod http://localhost:8010/campaigns/preview -Method Post -Headers $h -Form $form

$c = Invoke-RestMethod http://localhost:8010/campaigns -Method Post -Headers $h -Form $form
$r = Invoke-RestMethod "http://localhost:8010/campaigns/$($c.id)" -Headers $h
$r.state; $r.result.messages | Format-Table phone, ok, status, error

Invoke-RestMethod http://localhost:8010/spend -Headers $h | ConvertTo-Json -Depth 5
```

Campaign status is kept in memory, so it's lost on restart and only works with a single worker process. Swap `_campaigns` in `app.py` for Redis or a database before scaling out.

## The CSV

Any CSV with a phone column works. The column is found automatically if it's called `phone`, `phone_number`, `mobile`, `cell`, `number`, `to` or `msisdn` (otherwise pass `phone_column`). Comma, semicolon and tab separators, and Excel's UTF-8 BOM, are handled. `sample_recipients.csv` is a small example:

```csv
first_name,phone,date
Ava,(416) 555-0123,Tuesday
Liam,+1 212 555 0147,Wednesday
```

Any column can be a placeholder in the message template. A `message` column in the CSV overrides the template for that row.

Every number is cleaned to E.164 (`+14165550123`). Accepted forms include `4165550123`, `(416) 555-0123`, `1-416-555-0123`, `+1 416 555 0123`, `011 1 416…` and numbers with an extension. Each row is classed as US or CA by area code. These are skipped, with the reason shown in the preview:

- numbers that aren't +1, or don't have 10 digits
- +1 numbers outside the US and Canada (Jamaica 876, Dominican Republic 809, Puerto Rico 787, etc.), which bill as international
- toll-free (800, 888 …) and N11 numbers
- duplicates, and anything on the opt-out list

## Spend tracking

Every campaign sent through `POST /campaigns` (not previews) adds one row to `spend_log.csv`: time (UTC), campaign id, sent, failed, credits used, sent and credits per country (US/CA), the credit balance after the send, and the reason if it stopped early. `GET /spend` totals it.

- **Credits, yes.** BulkSMS returns the credit cost of each message and the account's credit balance, and both are logged.
- **Money, no.** The BulkSMS API never returns a money amount. Set `SPEND_CREDIT_PRICE` to what one credit cost you and `/spend` adds an estimated cost.
- **Number fees, no.** Registering a US or Canadian number and its monthly fee are billed on your BulkSMS account, not through the API.

The log is the only record of history, so back it up; it is in `.gitignore` so it never ends up in the repo. More detail: [docs/spend-tracking.md](docs/spend-tracking.md).

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
3. **Consent and opt-out.** The US (TCPA) and Canada (CASL) require prior consent from each recipient, the sender to be identified in the message, and a working opt-out. Include something like "Reply STOP to opt out" (the `footer` field), and keep the opted-out numbers in a file sent as `suppress`. BulkSMS's inbound messages (or webhooks) are where STOP replies arrive.
4. **Quiet hours.** Avoid sending outside about 8am to 9pm in the recipient's local time; some US states set stricter windows. Canada spans six time zones and the US spans six more, so schedule large sends with that in mind.
5. **Content.** Carriers filter SHAFT content (sex, hate, alcohol, firearms, tobacco/cannabis) and URL shorteners like bit.ly. Use your own domain for links.

## Trying it without a BulkSMS account

`mock_bulksms_server.py` is a local fake of the BulkSMS API on `http://127.0.0.1:8001/v1`, and `.env.mock` points the service at it with the key `mock-key`. `mock_data/` has a CSV full of valid and invalid US/Canadian numbers and an opt-out list. In short:

```powershell
# Window 1
python mock_bulksms_server.py
# Window 2
uvicorn app:app --port 8010 --env-file .env.mock
# Window 3
curl.exe -s -H "X-API-Key: mock-key" -F "file=@mock_data/mock_recipients.csv" -F "message=Hi {first_name}" http://localhost:8010/campaigns/preview
```

Step-by-step instructions and expected output: [docs/testing-with-mock.md](docs/testing-with-mock.md).

## Tests

```powershell
python -m pytest -q
```

The tests use a fake BulkSMS API (`tests/fake_bulksms.py`), so they never send a real message or need credentials. They cover number cleanup, CSV parsing, batching, retry with the same deduplication id, aborting on bad credentials, spend logging and the API endpoints.

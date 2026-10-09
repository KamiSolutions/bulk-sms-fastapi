# Trying the API against the mock BulkSMS API (Windows PowerShell)

This runs the FastAPI service end to end on your machine with **no BulkSMS account, no real credentials and no real messages**. A small local server, `mock_bulksms_server.py`, pretends to be `api.bulksms.com`, and `.env.mock` points the service at it.

What's included:

| File | What it is |
|---|---|
| `mock_bulksms_server.py` | Local fake of the BulkSMS API on `http://127.0.0.1:8001/v1`. Prints every "sent" SMS in its window. |
| `.env.mock` | Fake settings: `BULKSMS_API_URL` points at the mock, dummy token, `APP_API_KEY=mock-key`. Safe to commit. |
| `mock_data/mock_recipients.csv` | 22 rows: valid US and Canadian numbers in many formats, duplicates, Caribbean and Puerto Rico numbers, toll-free, too short, letters, UK, N11, blank, two opted-out numbers, one number the mock rejects. |
| `mock_data/mock_optouts.csv` | Two opted-out numbers that appear in the CSV. |

The real-send path is unchanged: without `--env-file .env.mock`, the service still talks to `https://api.bulksms.com/v1`.

## 1. One-time setup

Open PowerShell in the project folder (for example `C:\Users\<you>\bulk-sms-fastapi`):

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

If `Activate.ps1` is blocked ("running scripts is disabled"), run this once in that window and try again:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
```

You'll use **three PowerShell windows**, each in the project folder with `.\.venv\Scripts\Activate.ps1` run first. The examples use port 8010 for the service; any free port works.

## 2. Window 1: start the mock BulkSMS API

```powershell
python mock_bulksms_server.py
```

Expect:

```
Mock BulkSMS API on http://127.0.0.1:8001/v1  (no real SMS is ever sent)
```

Leave it running. Each message it "sends" is printed here.

## 3. Window 2: start the service against the mock

```powershell
uvicorn app:app --port 8010 --env-file .env.mock
```

Leave it running. The requests below go in window 3.

## 4. Window 3: call the API

First check the service is pointed at the mock:

```powershell
Invoke-RestMethod http://localhost:8010/health
```

Expect `ok: True` and `bulksms_api: http://127.0.0.1:8001/v1`. **If `bulksms_api` shows `https://api.bulksms.com/v1`, the service is pointed at the real BulkSMS**: stop it and start it again with `--env-file .env.mock`.

If you get `Not Found` instead, something other than the service is answering on port 8010 (usually the mock server, or an older uvicorn still running). Stop everything on that port with Ctrl+C, or find it with `Get-NetTCPConnection -LocalPort 8010 | Select OwningProcess` and `Stop-Process -Id <that number>`, then start the service again.

You can also try every endpoint from the browser at http://localhost:8010/docs: open an endpoint, click **Try it out**, and put `mock-key` in its `x-api-key` field.

### With curl

Use `curl.exe`, not `curl` (in Windows PowerShell 5.1, `curl` is an alias for a different command).

Preview (sends nothing):

```powershell
curl.exe -s -H "X-API-Key: mock-key" `
  -F "file=@mock_data/mock_recipients.csv" `
  -F "suppress=@mock_data/mock_optouts.csv" `
  -F "message=Hi {first_name}, your interview is on {date}." `
  -F "footer=Reply STOP to opt out" `
  http://localhost:8010/campaigns/preview
```

Expect JSON starting `{"valid":9,"rejected":11,"duplicates_removed":2,"by_country":{"CA":5,"US":4},"estimated_sms_parts":9,...` followed by the rejected rows with their reasons and 5 sample messages. The rejected rows are:

```
row 11: 876-555-0100      area code 876 is outside the US and Canada
row 12: +1 809 555 0123   area code 809 is outside the US and Canada
row 13: 787-555-0144      area code 787 is outside the US and Canada
row 14: 800-555-0100      toll-free numbers cannot receive SMS
row 15: 555-0123          expected 10 digits after +1, got 7
row 16: +44 20 7946 0958  not a +1 (US/Canada) number
row 17: 212-555-CALL      contains non-digit characters
row 18: 911-555-0100      N11 service codes are not phone numbers
row 19: (blank)           empty
row 20: +1 617 555 0181   opted out
row 21: 647-555-0192      opted out
```

Without the `suppress` file the two opted-out numbers count as valid, so you'd see 11 valid and 9 rejected instead (and later 10 sent, 1 failed). Both are correct for their request. The 2 duplicates are rows 4 and 10 (the same numbers as rows 2 and 1, written differently).

Send (to the mock) by posting the same thing to `/campaigns`:

```powershell
curl.exe -s -H "X-API-Key: mock-key" `
  -F "file=@mock_data/mock_recipients.csv" `
  -F "suppress=@mock_data/mock_optouts.csv" `
  -F "message=Hi {first_name}, your interview is on {date}." `
  -F "footer=Reply STOP to opt out" `
  http://localhost:8010/campaigns
```

Expect `{"id":"<campaign id>","state":"queued","valid":9,...}`. Then check it (paste the id):

```powershell
curl.exe -s -H "X-API-Key: mock-key" http://localhost:8010/campaigns/<campaign id>
```

Expect `"state":"done"` and `"result":{"sent":8,"failed":1,"credits_used":8.0,...}`, the spend row it logged, then one entry per message. The one failure is Henry, `305-555-0666`: the mock rejects any number ending in `0666` so you can see a per-message failure. Window 1 shows the 9 messages.

Spend so far:

```powershell
curl.exe -s -H "X-API-Key: mock-key" http://localhost:8010/spend
```

Expect `"all_time":{"campaigns":1,"sent":8,"failed":1,"credits_used":8,"us_sent":3,...,"ca_sent":5,...}`. Mock runs log to `spend_log.mock.csv`, never to the real `spend_log.csv`.

### Or with Invoke-RestMethod (PowerShell 7 or later)

`-Form` needs PowerShell 7 (`pwsh`); it isn't in Windows PowerShell 5.1.

```powershell
$h = @{ "X-API-Key" = "mock-key" }
$form = @{
  file     = Get-Item .\mock_data\mock_recipients.csv
  suppress = Get-Item .\mock_data\mock_optouts.csv
  message  = "Hi {first_name}, your interview is on {date}."
  footer   = "Reply STOP to opt out"
}
Invoke-RestMethod http://localhost:8010/campaigns/preview -Method Post -Headers $h -Form $form

$c = Invoke-RestMethod http://localhost:8010/campaigns -Method Post -Headers $h -Form $form
Start-Sleep 1
$r = Invoke-RestMethod "http://localhost:8010/campaigns/$($c.id)" -Headers $h
$r.state; $r.result.messages | Format-Table phone, ok, status, error

Invoke-RestMethod http://localhost:8010/spend -Headers $h | ConvertTo-Json -Depth 5
```

## 5. See what the mock received

```powershell
curl.exe -s -u mock-token:mock-secret "http://127.0.0.1:8001/v1/messages?limit=5"
curl.exe -s -X POST http://127.0.0.1:8001/mock/reset    # clear it and restore 1000 credits
```

## 6. Trying failure cases

**Retries and running out of credit.** Stop the mock (Ctrl+C in window 1) and restart it with these settings:

```powershell
$env:MOCK_FAIL_FIRST = 2   # the first 2 requests get "503 Service Unavailable"
$env:MOCK_CREDITS = 6      # less credit than the 9 messages need
python mock_bulksms_server.py
```

Send the campaign again from window 3 and check it after a few seconds. Window 2 shows `BulkSMS 503, retrying in 1.0s` twice: the batch is retried with the same deduplication id until the mock accepts the request. Then the mock refuses it for lack of credit, so the campaign ends with `"state":"aborted"`, `"sent":0,"failed":9` and `"aborted":"BulkSMS 403: Insufficient Credits - Balance 6, this batch needs 8 (mock)"`. All 9 recipients are one batch (the service sends up to 100 per request), so nothing goes out and every row is reported as not sent rather than attempted.

When finished, clear the mock settings in window 1 with `Remove-Item Env:MOCK_FAIL_FIRST, Env:MOCK_CREDITS` and restart the mock.

**A wrong API token.** Stop the service (Ctrl+C in window 2) and restart it with a bad token id:

```powershell
$env:BULKSMS_TOKEN_ID = "bad"
uvicorn app:app --port 8010 --env-file .env.mock
```

Send the campaign again. It ends with `"state":"aborted"` and `"aborted":"BulkSMS 401: Unauthorized - The token id or secret is wrong (mock: token id 'bad')"`, and nothing is sent. Afterwards stop the service, run `Remove-Item Env:BULKSMS_TOKEN_ID` and start it again.

## 7. Automated tests

```powershell
python -m pytest -q
```

Expect `43 passed`. These use in-process fakes and don't need the mock server running.

## Notes

- Settings already set in your PowerShell session win over the file, so a `$env:BULKSMS_API_URL` you set yourself would override `.env.mock`. `/health` shows which API is in use.
- The mock charges 1 credit per SMS part. Real BulkSMS pricing to the US and Canada is different; check your account's price list.
- The mock accepts every message straight away. Real delivery reports (DELIVERED, UNDELIVERABLE) come later from BulkSMS and aren't simulated.

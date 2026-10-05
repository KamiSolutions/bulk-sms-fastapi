# Trying it out against the mock BulkSMS API (Windows PowerShell)

This runs the CLI and the FastAPI service end to end on your machine with **no BulkSMS account, no real credentials and no real messages**. A small local server, `mock_bulksms_server.py`, pretends to be `api.bulksms.com`, and `.env.mock` points everything at it.

What's included:

| File | What it is |
|---|---|
| `mock_bulksms_server.py` | Local fake of the BulkSMS API on `http://127.0.0.1:8001/v1`. Prints every "sent" SMS in its window. |
| `.env.mock` | Fake settings: `BULKSMS_API_URL` points at the mock, dummy token, `APP_API_KEY=mock-key`. Safe to commit. |
| `mock_data/mock_recipients.csv` | 22 rows: valid US and Canadian numbers in many formats, duplicates, Caribbean and Puerto Rico numbers, toll-free, too short, letters, UK, N11, blank, two opted-out numbers, one number the mock rejects. |
| `mock_data/mock_optouts.csv` | Two opted-out numbers that appear in the CSV. |

The real-send path is unchanged: without `--env-file .env.mock` (CLI) or `--env-file .env.mock` (uvicorn), the code still talks to `https://api.bulksms.com/v1`.

## 1. One-time setup

Open PowerShell in the project folder (for example `C:\Users\injozi\bulk-sms-fastapi`):

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

If `Activate.ps1` is blocked ("running scripts is disabled"), run this once in that window and try again:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
```

You'll use **three PowerShell windows**, each in the project folder with `.\.venv\Scripts\Activate.ps1` run first.

## 2. Window 1: start the mock BulkSMS API

```powershell
python mock_bulksms_server.py
```

Expect:

```
Mock BulkSMS API on http://127.0.0.1:8001/v1  (no real SMS is ever sent)
```

Leave it running. Each message it "sends" is printed here.

## 3. Window 2: the command-line script

Preview only (sends nothing, not even to the mock):

```powershell
python send_sms.py mock_data\mock_recipients.csv `
  -m "Hi {first_name}, your interview is on {date}." `
  --footer "Reply STOP to opt out" `
  --suppress mock_data\mock_optouts.csv `
  --env-file .env.mock --dry-run
```

Expect:

```
Recipients: 9 valid {'CA': 5, 'US': 4}, 11 rejected, 2 duplicates removed
Estimated SMS parts: 9
  row 11: '876-555-0100' skipped (area code 876 is outside the US and Canada)
  row 12: '+1 809 555 0123' skipped (area code 809 is outside the US and Canada)
  row 13: '787-555-0144' skipped (area code 787 is outside the US and Canada)
  row 14: '800-555-0100' skipped (toll-free numbers cannot receive SMS)
  row 15: '555-0123' skipped (expected 10 digits after +1, got 7)
  row 16: '+44 20 7946 0958' skipped (not a +1 (US/Canada) number)
  row 17: '212-555-CALL' skipped (contains non-digit characters)
  row 18: '911-555-0100' skipped (N11 service codes are not phone numbers)
  row 19: '' skipped (empty)
  row 20: '+1 617 555 0181' skipped (opted out)
  row 21: '647-555-0192' skipped (opted out)
  ...
Dry run: nothing was sent.
```

The 2 duplicates are rows 4 and 10 (the same numbers as rows 2 and 1, written differently).

Now "send" to the mock, in batches of 4 so you can see batching, and save a report:

```powershell
python send_sms.py mock_data\mock_recipients.csv `
  -m "Hi {first_name}, your interview is on {date}." `
  --footer "Reply STOP to opt out" `
  --suppress mock_data\mock_optouts.csv `
  --env-file .env.mock --batch-size 4 --report results_mock.csv -y
```

Expect, after the same preview:

```
Using BulkSMS API at http://127.0.0.1:8001/v1 (not the real BulkSMS)
Account mock-account: 1000.0 credits available
  batch 1/3 submitted
  batch 2/3 submitted
  batch 3/3 submitted

Sent 8, failed 1, credits used 8
Report written to results_mock.csv
```

The one failure is Henry, `305-555-0666`: the mock rejects any number ending in `0666` so you can see a per-message failure. The exit code is 1 because not every message went through. Window 1 shows the 9 messages, and `results_mock.csv` lists every row with its message id, status or skip reason (open it in Excel). `results*.csv` is already in `.gitignore`.

**Always check for the line `Using BulkSMS API at http://127.0.0.1:8001/v1`.** If it's missing, the script is pointed at the real BulkSMS.

## 4. Window 3: the FastAPI service

Start it in window 3, against the mock:

```powershell
uvicorn app:app --port 8000 --env-file .env.mock
```

Leave it running and go back to window 2 for the requests below. First check it's pointed at the mock:

```powershell
Invoke-RestMethod http://localhost:8000/health
```

Expect `ok: True` and `bulksms_api: http://127.0.0.1:8001/v1`.

You can also upload the CSV from the browser at http://localhost:8000/docs: open an endpoint, click **Try it out**, and put `mock-key` in its `x-api-key` field.

### Upload the CSV with curl

Use `curl.exe`, not `curl` (in Windows PowerShell 5.1, `curl` is an alias for a different command).

Preview (sends nothing):

```powershell
curl.exe -s -H "X-API-Key: mock-key" `
  -F "file=@mock_data/mock_recipients.csv" `
  -F "suppress=@mock_data/mock_optouts.csv" `
  -F "message=Hi {first_name}, your interview is on {date}." `
  -F "footer=Reply STOP to opt out" `
  http://localhost:8000/campaigns/preview
```

Expect JSON starting `{"valid":9,"rejected":11,"duplicates_removed":2,"by_country":{"CA":5,"US":4},"estimated_sms_parts":9,...` with the rejected rows and 5 sample messages.

Send (to the mock) by posting the same thing to `/campaigns`:

```powershell
curl.exe -s -H "X-API-Key: mock-key" `
  -F "file=@mock_data/mock_recipients.csv" `
  -F "suppress=@mock_data/mock_optouts.csv" `
  -F "message=Hi {first_name}, your interview is on {date}." `
  -F "footer=Reply STOP to opt out" `
  http://localhost:8000/campaigns
```

Expect `{"id":"<campaign id>","state":"queued","valid":9,...}`. Then check it (paste the id):

```powershell
curl.exe -s -H "X-API-Key: mock-key" http://localhost:8000/campaigns/<campaign id>
```

Expect `"state":"done"` and `"result":{"sent":8,"failed":1,"credits_used":8.0,...}` followed by one entry per message.

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
Invoke-RestMethod http://localhost:8000/campaigns/preview -Method Post -Headers $h -Form $form

$c = Invoke-RestMethod http://localhost:8000/campaigns -Method Post -Headers $h -Form $form
Start-Sleep 1
$r = Invoke-RestMethod "http://localhost:8000/campaigns/$($c.id)" -Headers $h
$r.state; $r.result.messages | Format-Table phone, ok, status, error
```

## 5. See what the mock received

```powershell
curl.exe -s -u mock-token:mock-secret "http://127.0.0.1:8001/v1/messages?limit=5"
curl.exe -s -X POST http://127.0.0.1:8001/mock/reset    # clear it and restore 1000 credits
```

## 6. Trying failure cases

Stop the mock (Ctrl+C in window 1) and restart it with these settings to see how the sender copes:

```powershell
$env:MOCK_FAIL_FIRST = 2   # the first 2 batches get "503 Service Unavailable"
$env:MOCK_CREDITS = 6      # credit runs out part way through
python mock_bulksms_server.py
```

Then rerun the `--batch-size 4` send from step 3. Add `-v` to see the retries. Expect:

```
WARNING BulkSMS 503, retrying in 1.0s
...
Sent 4, failed 7, credits used 4
Stopped early: BulkSMS 403: Insufficient Credits - Balance 2, this batch needs 4 (mock)
```

The first batch is retried with the same deduplication id until it goes through; the second batch is refused for lack of credit, and the rest are reported as not sent rather than attempted.

A wrong API token: in window 2 run

```powershell
$env:BULKSMS_TOKEN_ID = "bad"
python send_sms.py mock_data\mock_recipients.csv -m "Hi {first_name}" --env-file .env.mock -y
Remove-Item Env:BULKSMS_TOKEN_ID
```

Expect `error: could not log in to BulkSMS: BulkSMS 401: Unauthorized ...` and nothing sent.

When finished, clear the mock settings in window 1 with `Remove-Item Env:MOCK_FAIL_FIRST, Env:MOCK_CREDITS`.

## 7. Automated tests

```powershell
python -m pytest -q
```

Expect `38 passed`. These use in-process fakes and don't need the mock server running.

## Notes

- Settings already set in your PowerShell session win over the file, so a `$env:BULKSMS_API_URL` you set yourself would override `.env.mock`. The "Using BulkSMS API at ..." line and `/health` show which API is in use.
- The mock charges 1 credit per SMS part. Real BulkSMS pricing to the US and Canada is different; check your account's price list.
- The mock accepts every message straight away. Real delivery reports (DELIVERED, UNDELIVERABLE) come later from BulkSMS and aren't simulated.

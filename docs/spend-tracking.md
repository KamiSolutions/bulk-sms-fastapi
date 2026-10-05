# Spend tracking: update and try it (PowerShell)

Version 3 logs the credits every campaign uses to a CSV and adds a report, from the command line and from the API.

## 1. Copy in the changed files

Unzip `bulk-sms-v3.zip` and copy these into your `bulk-sms-fastapi` folder, replacing the old ones:

| File | |
|---|---|
| `bulksms\spend.py` | new: writes and totals the spend log |
| `spend_report.py` | new: the totals command |
| `tests\test_spend.py` | new: tests |
| `docs\spend-tracking.md` | new: this guide |
| `bulksms\__init__.py` | changed |
| `send_sms.py` | changed: logs each send |
| `app.py` | changed: logs each campaign, adds `GET /spend` |
| `tests\test_app.py` | changed |
| `README.md` | changed: "Spend tracking" section |
| `.env.example`, `.env.mock`, `.gitignore` | changed: new settings, log files kept out of git |

Nothing else changed and there are no new packages to install. Check it with:

```powershell
cd C:\path\to\bulk-sms-fastapi
.\.venv\Scripts\Activate.ps1
pytest -q
```

Expect `44 passed`.

## 2. Try it against the mock

Terminal 1, the mock BulkSMS API:

```powershell
python mock_bulksms_server.py
```

Terminal 2, a CLI send:

```powershell
python send_sms.py mock_data\mock_recipients.csv -m "Hi {first_name}" --footer "Reply STOP to opt out" --suppress mock_data\mock_optouts.csv --env-file .env.mock -y
```

The last lines now include `Spend logged to ...\spend_log.mock.csv (8 credits this month so far)`.

Terminal 3, the service (port 8010) and an API send:

```powershell
uvicorn app:app --port 8010 --env-file .env.mock
```

```powershell
$h = @{ "X-API-Key" = "mock-key" }
curl.exe -s -H "X-API-Key: mock-key" -F "file=@mock_data\mock_recipients.csv" -F "message=Hello {first_name}" http://127.0.0.1:8010/campaigns
Invoke-RestMethod http://127.0.0.1:8010/spend -Headers $h | ConvertTo-Json -Depth 5
```

`/spend` shows `all_time` and `by_month` with campaigns, sent, failed, credits used, and US/CA split. Add `?month=2026-10` for one month. The finished campaign (`GET /campaigns/<id>`) also carries the row it logged under `result.spend`.

The report from the command line:

```powershell
python spend_report.py --env-file .env.mock
```

```
Month     Campaigns    Sent  Failed  US sent  CA sent   Credits
---------------------------------------------------------------
2026-10           2      18       2        7       11        18
---------------------------------------------------------------
Total             2      18       2        7       11        18
```

## 3. Real use

Real sends (your `.env`) write to `spend_log.csv` in the project folder; mock runs write to `spend_log.mock.csv`, so the two never mix. Both are git-ignored, so back up `spend_log.csv` yourself.

To see money as well as credits, add to `.env` what one credit cost you on your BulkSMS bundle:

```
SPEND_CREDIT_PRICE=0.05
SPEND_CURRENCY=USD
```

The report and `/spend` then add an estimated cost. This is an estimate: the BulkSMS API only reports credits, never money, and the US/Canada number registration and monthly number fees are billed on your BulkSMS account, so check its billing page for those.

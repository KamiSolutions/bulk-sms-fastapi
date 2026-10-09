# Spend tracking (PowerShell)

The service logs the credits every campaign uses to a CSV, and `GET /spend` totals it, all time and per month, with a US/Canada split.

## What gets logged

Every campaign sent through `POST /campaigns` adds one row to the spend log when it finishes. Previews add nothing. Each row has the time (UTC), campaign id, sent, failed, credits used, sent and credits per country (US/CA), the account's credit balance after the send, and the reason if the campaign stopped early.

Real sends (your `.env`) write to `spend_log.csv` in the project folder; mock runs (`.env.mock`) write to `spend_log.mock.csv`, so the two never mix. Set `SPEND_LOG_PATH` to put the log somewhere else. Both files are git-ignored, so back up `spend_log.csv` yourself: it is the only record of history.

The finished campaign (`GET /campaigns/<id>`) also carries the row it logged under `result.spend`.

## Try it against the mock

Window 1, the mock BulkSMS API:

```powershell
python mock_bulksms_server.py
```

Window 2, the service:

```powershell
uvicorn app:app --port 8010 --env-file .env.mock
```

Window 3, a send and the totals:

```powershell
curl.exe -s -H "X-API-Key: mock-key" -F "file=@mock_data\mock_recipients.csv" -F "message=Hello {first_name}" http://127.0.0.1:8010/campaigns

$h = @{ "X-API-Key" = "mock-key" }
Invoke-RestMethod http://127.0.0.1:8010/spend -Headers $h | ConvertTo-Json -Depth 5
Invoke-RestMethod "http://127.0.0.1:8010/spend?month=2026-10" -Headers $h | ConvertTo-Json -Depth 5
```

`/spend` returns `all_time` and `by_month`, each with campaigns, sent, failed, credits used and the US/CA split, plus `latest_balance` (the credit balance after the most recent send). `?month=` takes `YYYY-MM`; anything else is a `422`.

## Estimated cost

To see money as well as credits, add to `.env` what one credit cost you on your BulkSMS bundle:

```
SPEND_CREDIT_PRICE=0.05
SPEND_CURRENCY=USD
```

`/spend` then adds `estimated_cost` to each total. This is an estimate: the BulkSMS API only reports credits, never money, and the US/Canada number registration and monthly number fees are billed on your BulkSMS account, so check its billing page for those.

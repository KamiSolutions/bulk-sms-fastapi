from datetime import datetime

from bulksms import load_recipients, spend
from bulksms.client import MessageOutcome, SendResult

RECIPIENTS = load_recipients(b"phone\n4165550123\n2125550147\n6045550199\n", template="Hi").recipients


def result():
    return SendResult(outcomes=[
        MessageOutcome("+14165550123", 1, True, credit_cost=2),
        MessageOutcome("+12125550147", 2, True, credit_cost=1),
        MessageOutcome("+16045550199", 3, False, error="BLOCKED", credit_cost=0),
    ])


def at(month):
    return datetime.fromisoformat(f"{month}-15T12:00:00+00:00")


def test_record_splits_by_country(tmp_path):
    path = tmp_path / "log.csv"
    row = spend.record("c1", "api", result(), RECIPIENTS, balance_after=97.5, path=path)
    assert row["sent"] == 2 and row["failed"] == 1 and row["credits_used"] == 3
    assert (row["us_sent"], row["ca_sent"], row["us_credits"], row["ca_credits"]) == (1, 1, 1, 2)
    rows = spend.read(path)
    assert len(rows) == 1 and rows[0]["balance_after"] == "97.5" and rows[0]["campaign_id"] == "c1"


def test_totals_per_month(tmp_path, monkeypatch):
    path = tmp_path / "log.csv"
    monkeypatch.delenv("SPEND_CREDIT_PRICE", raising=False)
    spend.record("a", "api", result(), RECIPIENTS, balance_after=10, path=path, now=at("2026-09"))
    spend.record("b", "api", result(), RECIPIENTS, balance_after=7, path=path, now=at("2026-10"))
    spend.record("c", "api", result(), RECIPIENTS, balance_after=4, path=path, now=at("2026-10"))
    t = spend.totals(path)
    assert t["all_time"]["campaigns"] == 3 and t["all_time"]["credits_used"] == 9
    assert {m: s["credits_used"] for m, s in t["by_month"].items()} == {"2026-09": 3, "2026-10": 6}
    assert t["latest_balance"] == 4 and "estimated_cost" not in t["all_time"]
    oct_ = spend.totals(path, month="2026-10")
    assert oct_["all_time"]["campaigns"] == 2 and list(oct_["by_month"]) == ["2026-10"]


def test_estimated_cost(tmp_path, monkeypatch):
    path = tmp_path / "log.csv"
    monkeypatch.setenv("SPEND_CREDIT_PRICE", "0.04")
    monkeypatch.setenv("SPEND_CURRENCY", "USD")
    spend.record("a", "api", result(), RECIPIENTS, path=path)
    t = spend.totals(path)
    assert t["all_time"]["estimated_cost"] == 0.12 and t["currency"] == "USD"


def test_empty_log(tmp_path):
    t = spend.totals(tmp_path / "missing.csv")
    assert t["all_time"]["campaigns"] == 0 and t["by_month"] == {} and t["latest_balance"] is None


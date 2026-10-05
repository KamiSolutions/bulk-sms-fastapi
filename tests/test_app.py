from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app as app_module
from bulksms import BulkSMSClient
from tests.fake_bulksms import FakeBulkSMS

SAMPLE = (Path(__file__).resolve().parents[1] / "sample_recipients.csv").read_bytes()
KEY = {"X-API-Key": "test-key"}


@pytest.fixture
def fake(monkeypatch, tmp_path):
    monkeypatch.setenv("APP_API_KEY", "test-key")
    monkeypatch.setenv("SPEND_LOG_PATH", str(tmp_path / "spend.csv"))
    fake = FakeBulkSMS()
    monkeypatch.setattr(app_module, "make_client", lambda: BulkSMSClient("id", "secret", transport=fake.transport()))
    return fake


@pytest.fixture
def api(fake):
    return TestClient(app_module.app)


def test_requires_key(api):
    r = api.post("/campaigns/preview", files={"file": ("r.csv", SAMPLE)}, data={"message": "x"})
    assert r.status_code == 401


def test_preview_sends_nothing(api, fake):
    r = api.post("/campaigns/preview", headers=KEY, files={"file": ("r.csv", SAMPLE)}, data={"message": "Hi {first_name}"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["valid"] == 3 and body["rejected"] == 2 and body["sample"][0]["body"] == "Hi Ava"
    assert fake.calls == []


def test_campaign_flow(api, fake):
    r = api.post("/campaigns", headers=KEY, files={"file": ("r.csv", SAMPLE)},
                 data={"message": "Hi {first_name}", "footer": "Reply STOP to opt out"})
    assert r.status_code == 202, r.text
    cid = r.json()["id"]
    status = api.get(f"/campaigns/{cid}", headers=KEY).json()  # background task ran before response returned
    assert status["state"] == "done"
    assert status["result"]["sent"] == 3
    assert status["result"]["messages"][0]["phone"] == "+14165550123"


def test_bad_csv(api):
    r = api.post("/campaigns/preview", headers=KEY, files={"file": ("r.csv", b"name\nbob\n")}, data={"message": "x"})
    assert r.status_code == 422


def test_campaign_is_logged_and_totalled(api, monkeypatch):
    monkeypatch.setenv("SPEND_CREDIT_PRICE", "0.05")
    for _ in range(2):
        r = api.post("/campaigns", headers=KEY, files={"file": ("r.csv", SAMPLE)}, data={"message": "Hi {first_name}"})
        assert r.status_code == 202, r.text
    status = api.get(f"/campaigns/{r.json()['id']}", headers=KEY).json()
    assert status["result"]["spend"]["credits_used"] == 3
    assert status["result"]["spend"]["balance_after"] == 100

    t = api.get("/spend", headers=KEY).json()
    assert t["all_time"] == {"campaigns": 2, "sent": 6, "failed": 0, "credits_used": 6, "us_sent": 2, "ca_sent": 4,
                             "us_credits": 2, "ca_credits": 4, "estimated_cost": 0.3}
    assert list(t["by_month"]) == [status["created_at"][:7]]
    assert api.get("/spend", headers=KEY, params={"month": "1999-01"}).json()["all_time"]["campaigns"] == 0
    assert api.get("/spend", headers=KEY, params={"month": "oct"}).status_code == 422
    assert api.get("/spend").status_code == 401

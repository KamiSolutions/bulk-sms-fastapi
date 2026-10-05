from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app as app_module
from bulksms import BulkSMSClient
from tests.fake_bulksms import FakeBulkSMS

SAMPLE = (Path(__file__).resolve().parents[1] / "sample_recipients.csv").read_bytes()
KEY = {"X-API-Key": "test-key"}


@pytest.fixture
def fake(monkeypatch):
    monkeypatch.setenv("APP_API_KEY", "test-key")
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

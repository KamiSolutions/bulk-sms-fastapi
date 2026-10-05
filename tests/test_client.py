import json

import pytest

from bulksms import BulkSMSClient, load_recipients
from tests.fake_bulksms import FakeBulkSMS


def recipients(n):
    csv = "phone\n" + "\n".join(f"212555{1000 + i}" for i in range(n))
    return load_recipients(csv, template="Hello").recipients


def client(fake, **kw):
    return BulkSMSClient("id", "secret", sender="+18885550100", transport=fake.transport(), sleep=lambda s: None, **kw)


def test_batches_and_payload():
    fake = FakeBulkSMS()
    res = client(fake, batch_size=2).send(recipients(5))
    assert res.sent == 5 and res.failed == 0 and res.credits == 5
    assert len(fake.calls) == 3
    req = fake.calls[0]
    assert req.headers["authorization"].startswith("Basic ")
    assert req.url.params["auto-unicode"] == "true"
    body = json.loads(req.content)
    assert body[0] == {"to": "+12125551000", "body": "Hello", "routingGroup": "STANDARD", "userSuppliedId": "row-1", "from": "+18885550100"}


def test_retry_keeps_dedup_id():
    fake = FakeBulkSMS(fail_first=2)
    res = client(fake).send(recipients(1))
    assert res.sent == 1
    ids = {c.url.params["deduplication-id"] for c in fake.calls}
    assert len(fake.calls) == 3 and len(ids) == 1


def test_auth_failure_aborts_remaining_batches():
    fake = FakeBulkSMS(status_code=401, problem={"title": "Unauthorized", "detail": "bad token"})
    res = client(fake, batch_size=2).send(recipients(5))
    assert res.sent == 0 and res.failed == 5
    assert res.aborted and len(fake.calls) == 1


def test_missing_credentials(monkeypatch):
    monkeypatch.delenv("BULKSMS_TOKEN_ID", raising=False)
    monkeypatch.delenv("BULKSMS_TOKEN_SECRET", raising=False)
    with pytest.raises(ValueError):
        BulkSMSClient()

import pytest
from fastapi.testclient import TestClient

import mock_bulksms_server as mock

AUTH = ("mock-token", "mock-secret")


@pytest.fixture
def api():
    mock._reset()
    return TestClient(mock.app)


def send(api, messages, dedup="d1"):
    return api.post("/v1/messages", params={"deduplication-id": dedup}, json=messages, auth=AUTH)


def test_needs_credentials(api):
    assert api.get("/v1/profile").status_code == 401
    assert api.get("/v1/profile", auth=("bad", "x")).status_code == 401
    assert api.get("/v1/profile", auth=AUTH).json()["username"] == "mock-account"


def test_send_and_magic_failure(api):
    r = send(api, [{"to": "+14165550123", "body": "Hi", "userSuppliedId": "row-1"},
                   {"to": "+13055550666", "body": "Hi", "userSuppliedId": "row-2"}])
    assert r.status_code == 201
    first, second = r.json()
    assert first["status"]["type"] == "ACCEPTED" and first["userSuppliedId"] == "row-1"
    assert second["status"]["type"] == "FAILED" and second["creditCost"] == 0
    assert api.get("/v1/profile", auth=AUTH).json()["credits"]["balance"] == mock.START_CREDITS - 1


def test_repeated_dedup_id_is_not_sent_twice(api):
    msg = [{"to": "+14165550123", "body": "Hi"}]
    a, b = send(api, msg).json(), send(api, msg).json()
    assert a == b
    assert len(api.get("/v1/messages", auth=AUTH).json()) == 1


def test_out_of_credit(api):
    mock._state["credits"] = 1
    r = send(api, [{"to": "+14165550123", "body": "Hi"}, {"to": "+12125550147", "body": "Hi"}])
    assert r.status_code == 403
    assert api.get("/v1/profile", auth=AUTH).json()["credits"]["balance"] == 1

"""An in-process stand-in for api.bulksms.com so tests never send a real SMS."""

import json

import httpx


class FakeBulkSMS:
    def __init__(self, fail_first: int = 0, status_code: int = 201, problem: dict | None = None):
        self.calls: list[httpx.Request] = []
        self.fail_first = fail_first
        self.status_code = status_code
        self.problem = problem
        self.seen_dedup_ids: set[str] = set()

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        if request.url.path == "/v1/profile":
            return httpx.Response(200, json={"username": "test", "credits": {"balance": 100}})
        if self.fail_first:
            self.fail_first -= 1
            return httpx.Response(503, headers={"Retry-After": "1"})
        if self.status_code >= 400:
            return httpx.Response(self.status_code, json=self.problem or {"title": "error"})
        messages = json.loads(request.content)
        return httpx.Response(
            201,
            json=[
                {
                    "id": f"m{i}",
                    "to": m["to"].lstrip("+"),
                    "userSuppliedId": m.get("userSuppliedId"),
                    "status": {"type": "ACCEPTED", "subtype": None},
                    "creditCost": 1,
                }
                for i, m in enumerate(messages)
            ],
        )

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self)

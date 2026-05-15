from __future__ import annotations

import logging

import requests

import usaspending_fetcher


class _FakeResponse:
    def __init__(self, results, status_code=200, text=""):
        self._results = results
        self.status_code = status_code
        self.text = text

    def json(self):
        return {"results": self._results}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.exceptions.HTTPError(
                f"{self.status_code} Client Error", response=self
            )


def test_fetch_recent_large_contracts_uses_api_safe_page_limit_and_paginates(monkeypatch):
    page_1 = [
        {"Award ID": f"A{i}", "Modification Number": "0"}
        for i in range(usaspending_fetcher.API_PAGE_LIMIT)
    ]
    page_2 = [
        {"Award ID": "B1", "Modification Number": "0"},
        {"Award ID": "B2", "Modification Number": "0"},
        {"Award ID": "B3", "Modification Number": "0"},
    ]
    calls = []
    saved = {}

    def fake_post(_url, json=None, timeout=60):
        calls.append({"json": json, "timeout": timeout})
        page = int(json["page"])
        if page == 1:
            return _FakeResponse(page_1)
        if page == 2:
            return _FakeResponse(page_2)
        return _FakeResponse([])

    monkeypatch.setattr(usaspending_fetcher, "load_state", lambda: {"seen_award_ids": []})
    monkeypatch.setattr(usaspending_fetcher, "save_state", lambda state: saved.update(state))
    monkeypatch.setattr(usaspending_fetcher.requests, "post", fake_post)

    awards = usaspending_fetcher.fetch_recent_large_contracts()

    assert len(calls) == 2
    assert calls[0]["json"]["limit"] == usaspending_fetcher.API_PAGE_LIMIT
    assert calls[0]["json"]["page"] == 1
    assert calls[1]["json"]["limit"] == usaspending_fetcher.API_PAGE_LIMIT
    assert calls[1]["json"]["page"] == 2
    assert len(awards) == usaspending_fetcher.API_PAGE_LIMIT + 3
    assert len(saved["seen_award_ids"]) == usaspending_fetcher.API_PAGE_LIMIT + 3


def test_fetch_recent_large_contracts_logs_http_error_details(monkeypatch, caplog):
    def fake_post(_url, json=None, timeout=60):
        return _FakeResponse([], status_code=422, text="invalid payload")

    monkeypatch.setattr(usaspending_fetcher, "load_state", lambda: {"seen_award_ids": []})
    monkeypatch.setattr(usaspending_fetcher, "save_state", lambda _state: None)
    monkeypatch.setattr(usaspending_fetcher.requests, "post", fake_post)

    with caplog.at_level(logging.ERROR):
        awards = usaspending_fetcher.fetch_recent_large_contracts()

    assert awards == []
    assert "status=422" in caplog.text
    assert "invalid payload" in caplog.text

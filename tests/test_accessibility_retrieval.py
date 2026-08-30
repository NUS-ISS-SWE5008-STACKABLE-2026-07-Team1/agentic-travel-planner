import json

from flaskapp.travel_ai.agents.accessibility_agent import retrieval


STATE = {
    "request": {
        "destination": "Tokyo",
        "preferences": ["private medical detail must not enter the search query"],
    }
}


def test_retrieval_is_explicitly_unavailable_without_search_key(monkeypatch):
    monkeypatch.delenv("SERPER_API_KEY", raising=False)
    result = retrieval.retrieve_accessibility_evidence(STATE)
    assert result["status"] == "unavailable"
    assert result["results"] == []
    assert result["error_code"] == "not_configured"
    assert result["search_plan"]["queries"]


def test_retrieval_uses_serper_and_returns_https_sources(monkeypatch):
    captured = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return json.dumps({"organic": [
                {
                    "title": "Measured access guide",
                    "link": "https://www.accessable.co.uk/venue",
                    "snippet": "Door width 90 cm", "position": 1,
                },
                {
                    "title": "Another source",
                    "link": "https://example.com/accessibility",
                    "snippet": "Step-free entrance is listed", "position": 2,
                },
                {
                    "title": "Insecure source",
                    "link": "http://example.net/accessibility",
                    "snippet": "Must not be accepted", "position": 3,
                },
            ]}).encode()

    def fake_urlopen(request, timeout):
        captured["body"] = json.loads(request.data)
        captured["api_key"] = request.get_header("X-api-key")
        captured["url"] = request.full_url
        captured["timeout"] = timeout
        return Response()

    monkeypatch.setenv("SERPER_API_KEY", "test-key")
    monkeypatch.setattr(retrieval, "urlopen", fake_urlopen)
    result = retrieval.retrieve_accessibility_evidence(STATE)

    assert result["status"] == "available"
    assert [item["url"] for item in result["results"]] == [
        "https://www.accessable.co.uk/venue", "https://example.com/accessibility",
    ]
    assert result["results"][0]["evidence_id"] == "E1"
    assert result["results"][0]["source_type"] == "specialist"
    assert result["results"][1]["source_type"] == "unknown"
    assert captured["url"] == retrieval.SERPER_SEARCH_URL
    assert captured["api_key"] == "test-key"
    assert "api_key" not in captured["body"]
    assert captured["body"]["num"] == retrieval.RESULTS_PER_QUERY
    assert "private medical detail" not in captured["body"]["q"]


def test_only_safe_https_urls_are_accepted():
    assert retrieval._allowed_url("https://news.wheelmap.org/place")
    assert retrieval._allowed_url("https://other-accessibility.example/place")
    assert not retrieval._allowed_url("http://wheelmap.org/place")
    assert not retrieval._allowed_url("https://user:pass@example.com/place")


def test_timeout_is_retried_and_reported_without_crashing(monkeypatch):
    calls = 0

    def timeout(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        raise TimeoutError("slow provider")

    monkeypatch.setenv("SERPER_API_KEY", "test-key")
    monkeypatch.setattr(retrieval, "urlopen", timeout)
    result = retrieval.retrieve_accessibility_evidence(STATE)

    assert calls == retrieval.MAX_ATTEMPTS
    assert result["status"] == "unavailable"
    assert result["errors"][0]["error_code"] == "timeout"

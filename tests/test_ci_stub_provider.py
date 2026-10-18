"""The CI stub model server: answers like an OpenAI endpoint, reflects nothing."""

import json
import threading
import urllib.request
from http.server import HTTPServer

import pytest

from scripts.ci_stub_provider import Handler


@pytest.fixture()
def stub_url():
    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    server.server_close()


def test_chat_completion_never_reflects_the_requested_model(stub_url):
    body = json.dumps({"model": "<script>alert(1)</script>", "messages": []}).encode()
    request = urllib.request.Request(f"{stub_url}/v1/chat/completions", data=body,
                                     headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=5) as response:
        payload = json.loads(response.read())
        assert response.headers["Content-Type"] == "application/json"
        assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert payload["model"] == "stub-model"
    assert "<script>" not in json.dumps(payload)

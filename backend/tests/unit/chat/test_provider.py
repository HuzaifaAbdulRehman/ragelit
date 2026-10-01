import json
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from typing import Any
from urllib.error import URLError
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.chat import provider as provider_module
from app.chat.provider import CompatibleProvider
from app.core.config import Settings
from app.documents.extraction import DocumentError
from app.retrieval.contracts import AuthorizedChunk


def settings(base_url: str | None = None) -> Settings:
    return Settings.model_validate(
        {
            "environment": "test",
            "secret_key": "test-only",
            "database_admin_url": "unused",
            "database_url": "unused",
            "qdrant_url": "http://localhost:6333",
            "llm_base_url": base_url,
            "llm_model": "local-test-model",
            "llm_api_key": "",
        }
    )


def evidence() -> tuple[AuthorizedChunk, ...]:
    return (
        AuthorizedChunk(
            uuid4(), uuid4(), uuid4(), "notes.txt", "Untrusted evidence", "page 1", 0.8
        ),
    )


@contextmanager
def endpoint(body: bytes) -> Iterator[tuple[str, list[dict[str, Any]]]]:
    requests: list[dict[str, Any]] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            raw = self.rfile.read(int(self.headers["Content-Length"]))
            requests.append(
                {"path": self.path, "body": json.loads(raw), "headers": self.headers}
            )
            self.send_response(200)
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1", requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_http_adapter_sends_bounded_evidence_and_parses_citations() -> None:
    context = evidence()
    content = json.dumps(
        {"answer": "Grounded answer", "citation_ids": [str(context[0].id)]}
    )
    body = json.dumps(
        {"choices": [{"finish_reason": "stop", "message": {"content": content}}]}
    ).encode()
    with endpoint(body) as (url, requests):
        result = CompatibleProvider(settings(url)).generate("Question", context)
    assert result.answer == "Grounded answer"
    assert result.citation_ids == (context[0].id,)
    request = requests[0]
    assert request["path"] == "/v1/chat/completions"
    assert "Authorization" not in request["headers"]
    payload = request["body"]
    assert payload["max_tokens"] == 1024 and payload["temperature"] == 0
    supplied = json.loads(payload["messages"][1]["content"])
    assert supplied["question"] == "Question"
    assert supplied["evidence"][0]["chunk_id"] == str(context[0].id)
    assert "untrusted" in payload["messages"][0]["content"]


def test_http_adapter_rejects_oversized_response() -> None:
    with endpoint(b"x" * (256 * 1024 + 1)) as (url, _):
        with pytest.raises(DocumentError, match="generation_response_too_large"):
            CompatibleProvider(settings(url)).generate("Question", evidence())


def test_unconfigured_generation_has_no_network_side_effect() -> None:
    with pytest.raises(DocumentError, match="generation_not_configured"):
        CompatibleProvider(settings()).generate("Question", evidence())


def test_wrapped_socket_timeout_has_stable_timeout_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def timeout(*args: object, **kwargs: object) -> None:
        raise URLError(TimeoutError("private network details"))

    monkeypatch.setattr(provider_module, "urlopen", timeout)
    with pytest.raises(DocumentError) as caught:
        CompatibleProvider(settings("http://127.0.0.1:1/v1")).generate(
            "Question", evidence()
        )
    assert caught.value.code == "generation_timeout"
    assert caught.value.status == 504
    assert "private network details" not in str(caught.value)


@pytest.mark.parametrize(
    "content",
    [
        {"answer": "x" * 8001, "citation_ids": []},
        {"answer": "answer", "citation_ids": ["not-a-uuid"]},
        {"answer": "answer", "citation_ids": [], "unknown": "field"},
    ],
)
def test_http_adapter_rejects_malformed_answer_shape(content: dict[str, Any]) -> None:
    body = json.dumps(
        {
            "choices": [
                {"finish_reason": "stop", "message": {"content": json.dumps(content)}}
            ]
        }
    ).encode()
    with endpoint(body) as (url, _):
        with pytest.raises(ValidationError):
            CompatibleProvider(settings(url)).generate("Question", evidence())


def test_http_adapter_does_not_accept_a_truncated_generation() -> None:
    context = evidence()
    content = json.dumps(
        {"answer": "Possibly unfinished", "citation_ids": [str(context[0].id)]}
    )
    body = json.dumps(
        {"choices": [{"finish_reason": "length", "message": {"content": content}}]}
    ).encode()
    with endpoint(body) as (url, _):
        with pytest.raises(DocumentError) as caught:
            CompatibleProvider(settings(url)).generate("Question", context)
    assert caught.value.code == "generation_incomplete"
    assert caught.value.status == 502

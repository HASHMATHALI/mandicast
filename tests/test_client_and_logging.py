"""API client retries and secret handling, against a local test HTTP server (never the real API)."""

from __future__ import annotations

import logging
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from mandicast.ingest.client import DataGovClient, FetchError
from mandicast.logging_setup import RedactingFilter, redact, register_secret

KEY = "test-key-not-real-0123456789abcdef"


class _Handler(BaseHTTPRequestHandler):
    script: list[int] = []
    seen: list[str] = []

    def do_GET(self):  # noqa: N802 - http.server API
        self.seen.append(self.headers.get("User-Agent", ""))
        code = self.script.pop(0) if self.script else 200
        self.send_response(code)
        self.end_headers()
        if code == 200:
            self.wfile.write(b"ok")

    def log_message(self, *args):  # silence the test server
        pass


@pytest.fixture
def server():
    srv = HTTPServer(("127.0.0.1", 0), _Handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    _Handler.seen = []
    yield f"http://127.0.0.1:{srv.server_address[1]}/resource/x"
    srv.shutdown()


def client(url: str, retries: int = 3) -> DataGovClient:
    return DataGovClient(url, KEY, "MandiCast-test", timeout_s=5, retries=retries, sleep=lambda s: None)


def test_retries_transient_errors_then_succeeds(server):
    _Handler.script = [504, 502, 429]
    assert client(server)._get({"format": "csv"}) == b"ok"
    assert len(_Handler.seen) == 4
    assert _Handler.seen == ["MandiCast-test"] * 4  # the explicit User-Agent the API requires


def test_non_retryable_error_fails_fast_without_leaking_key(server):
    _Handler.script = [404]
    with pytest.raises(FetchError) as exc:
        client(server)._get({"format": "csv"})
    assert "HTTP 404" in str(exc.value) and KEY not in str(exc.value)
    assert len(_Handler.seen) == 1


def test_gives_up_after_retries_without_leaking_key(server):
    _Handler.script = [503] * 10
    with pytest.raises(FetchError) as exc:
        client(server, retries=2)._get({"format": "csv"})
    assert "after 2 retries" in str(exc.value) and KEY not in str(exc.value)
    assert len(_Handler.seen) == 3


def test_connection_error_is_retried(server):
    dead = "http://127.0.0.1:9/resource/x"  # discard port: connection refused
    with pytest.raises(FetchError) as exc:
        client(dead, retries=1)._get({"format": "csv"})
    assert KEY not in str(exc.value)


def test_missing_key_is_rejected():
    with pytest.raises(ValueError, match="DATA_GOV_IN_API_KEY"):
        DataGovClient("https://example.invalid", "", "ua")


def test_redaction_of_registered_secret_key_param_and_url_password():
    register_secret("s3cr3t-password-value")
    text = ("GET https://api.data.gov.in/resource/x?api-key=abcdef123456&format=csv "
            "postgresql+psycopg://mandicast:s3cr3t-password-value@127.0.0.1:5433/mandicast")
    out = redact(text)
    assert "abcdef123456" not in out and "s3cr3t-password-value" not in out
    assert "api-key=***" in out and "format=csv" in out


def test_log_filter_redacts_messages_and_exceptions(caplog):
    register_secret("another-secret-123456")
    logger = logging.getLogger("redaction-test")
    logger.addFilter(RedactingFilter())
    with caplog.at_level(logging.INFO, logger="redaction-test"):
        logger.info("calling ?api-key=%s now", "another-secret-123456")
        try:
            raise RuntimeError("failed with another-secret-123456")
        except RuntimeError:
            logger.exception("boom")
    joined = "\n".join(r.getMessage() + (r.exc_text or "") for r in caplog.records)
    assert "another-secret-123456" not in joined

"""Credential redaction at persistence, logging and response boundaries."""
from __future__ import annotations

import logging
import re
import traceback
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

SENSITIVE = {"key", "api_key", "apikey", "access_token", "token", "secret", "password", "authorization"}
URL_PATTERN = re.compile(r"https?://[^\s\"<>]+", re.IGNORECASE)


def sanitize_url(url: str) -> str:
    try:
        parts = urlsplit(url)
        host = parts.netloc.rsplit("@", 1)[-1]
        query = urlencode([(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
                           if k.lower().replace("-", "_") not in SENSITIVE])
        return urlunsplit((parts.scheme, host, parts.path, query, ""))
    except ValueError:
        return "[invalid URL]"


def redact(value: str) -> str:
    from .config import get_settings
    settings = get_settings()
    for secret in (settings.youtube_api_key, settings.tavily_api_key):
        if secret:
            value = value.replace(secret, "[REDACTED]").replace(quote(secret, safe=""), "[REDACTED]")
    value = URL_PATTERN.sub(lambda m: sanitize_url(m.group()), value)
    return re.sub(r"(?i)\b(api[_-]?key|access_token|authorization|password|secret)\s*[=:]\s*[^\s,;]+",
                  r"\1=[REDACTED]", value)


def install_log_redaction() -> None:
    previous = logging.getLogRecordFactory()
    if getattr(previous, "_redacting", False):
        return
    def factory(*args, **kwargs):
        record = previous(*args, **kwargs)
        record.msg = redact(record.getMessage())
        record.args = ()
        if record.exc_info:
            record.exc_text = redact("".join(traceback.format_exception(*record.exc_info)))
            record.exc_info = None
        return record
    factory._redacting = True
    logging.setLogRecordFactory(factory)


class RedactedResponses:
    """JSON and SSE are emitted as complete chunks by the application."""
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        active = False
        async def safe_send(message):
            nonlocal active
            if message["type"] == "http.response.start":
                headers = message.get("headers", [])
                active = any(k == b"content-type" and (b"json" in v or b"event-stream" in v) for k, v in headers)
                if active:
                    message = {**message, "headers": [(k, v) for k, v in headers if k != b"content-length"]}
            if active and message["type"] == "http.response.body":
                message = {**message, "body": redact(message.get("body", b"").decode("utf-8")).encode("utf-8")}
            await send(message)
        await self.app(scope, receive, safe_send)

"""Structured JSON logging. One JSON object per line on stdout, ready for any log collector.

The current request id lives in a context variable, so every log line written while handling a request
carries it without being passed around. Log lines must never contain question or document text: log
ids, outcomes and timings only (the trace holds the PII-masked query).
"""
from __future__ import annotations

import contextvars
import json
import logging
import os
import sys
from datetime import datetime, timezone

request_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar("request_id", default=None)

_RESERVED = set(vars(logging.LogRecord("", 0, "", 0, "", (), None))) | {"message", "asctime", "request_id", "color_message"}
_base_factory = logging.getLogRecordFactory()


def _record_factory(*args, **kwargs) -> logging.LogRecord:
    """Stamp the request id onto each record when it is CREATED, so handlers that format later
    (queues, log shippers, test capture) still see the right id."""
    record = _base_factory(*args, **kwargs)
    record.request_id = request_id_var.get()
    return record


logging.setLogRecordFactory(_record_factory)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        out = {
            "ts": datetime.fromtimestamp(record.created, timezone.utc).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "request_id": getattr(record, "request_id", None),
        }
        out.update({k: v for k, v in vars(record).items() if k not in _RESERVED and not k.startswith("_")})
        if record.exc_info:
            out["error_type"] = record.exc_info[0].__name__  # type only: messages and tracebacks can echo input
        return json.dumps(out, default=str)


def configure_logging(level: str | None = None) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    handler._bankrag = True  # replace only our own handler; leave handlers the host installed alone
    root = logging.getLogger()
    root.handlers[:] = [h for h in root.handlers if not getattr(h, "_bankrag", False)] + [handler]
    root.setLevel(level or os.environ.get("LOG_LEVEL", "INFO"))
    for name in ("uvicorn", "uvicorn.error"):  # route the server's own lines through the JSON formatter
        logging.getLogger(name).handlers[:] = []
        logging.getLogger(name).propagate = True
    logging.getLogger("uvicorn.access").disabled = True  # replaced by our own access line, which has the request id

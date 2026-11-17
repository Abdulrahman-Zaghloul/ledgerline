"""Prometheus metrics.

Labels use the route *template* (``/v1/accounts/{account_id}``), never the raw path:
one time series per real account id would grow without bound and overwhelm Prometheus.
"""

from __future__ import annotations

from prometheus_client import Counter, Histogram

HTTP_REQUESTS = Counter(
    "ledger_http_requests_total", "HTTP requests handled", ["method", "route", "status"]
)
HTTP_LATENCY = Histogram(
    "ledger_http_request_duration_seconds",
    "HTTP request latency",
    ["method", "route"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5),
)
LEDGER_TRANSACTIONS = Counter("ledger_transactions_total", "Ledger transactions recorded", ["kind"])
IDEMPOTENT_REPLAYS = Counter(
    "ledger_idempotent_replays_total", "Requests answered from a stored idempotent response"
)
OUTBOX_EVENTS = Counter(
    "ledger_outbox_events_total", "Outbox delivery attempts by outcome", ["outcome"]
)

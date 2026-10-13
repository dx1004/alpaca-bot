# Week 3 acceptance evidence

## W3-04 — Durable local order-intent increment (2026-10-12; not accepted)

The existing SQLite `Journal` now migrates schema 1 to 2 transactionally and keeps an immutable `order_intents` table keyed by `client_order_id`. `reserve_order_intent()` commits an intent before any external submission; a duplicate ID fails, including after reopening the database. A failed serialization leaves no reservation. This is a local engineering primitive only: it does not construct or submit broker orders, approve a strategy, or declare an ambiguous acknowledgment safe to retry. A later adapter must validate exact paper endpoint and account identity, record request/result evidence, query by client ID after uncertain outcomes, and reconcile before any retry. The intended order schema and SDK/transport semantics are not yet accepted.

The required independent GPT-6.1 Sol Low public-source retrieval failed with `Selected model is at capacity`; no substitute model was claimed. The Alpaca order documentation/SDK semantics stage remains unresolved. The repository shell was read-only for ordinary writes; one narrowly escalated write probe succeeded and its temporary file was removed. Ruff/mypy default cache writes also failed under the restricted mount, so verification used isolated `/tmp` caches; this is not a claim that project-wide write access was repaired.

Validation from `/mnt/t/alpaca-bot` after the change:

```text
rtk proxy env PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -p no:cacheprovider -q  -> 75 passed, 1 upstream websockets.legacy deprecation warning
rtk proxy env RUFF_CACHE_DIR=/tmp/alpaca-w3-ruff .venv/bin/ruff check src tests  -> All checks passed
rtk proxy env RUFF_CACHE_DIR=/tmp/alpaca-w3-ruff .venv/bin/ruff format --check src tests  -> 26 files already formatted
rtk proxy env MYPY_CACHE_DIR=/tmp/alpaca-w3-mypy .venv/bin/python -m mypy --strict src tests  -> no issues in 26 source files
```

W3-04 remains in progress. W3-01/02/03 and W3-08 still lack their required accepted strategy/W2 dependencies; W3-05/06/07 require W3-04 and later gates. No broker order, purchase, live endpoint or formal paper run was performed.

## W3-04 — One-attempt send marker and Alpaca order semantics (2026-10-13; not accepted)

The journal now migrates schema 2 to 3 and adds an immutable `order_send_attempts` row keyed by the previously reserved `client_order_id`. A send marker commits before transport handoff. It cannot be inserted for an absent intent or inserted twice, including after restart. This closes a local crash window: a restarted process must not interpret an existing intent without a response as proof that no request was sent. There is still no transport or automatic broker submission.

Independent GPT-6.1 Sol Low public-source retrieval on 2026-10-13 and direct source review established:

- Alpaca documents `POST /v2/orders` and `GET /v2/orders:by_client_order_id` with a caller-assigned ID in [Working with /orders](https://docs.alpaca.markets/us/docs/working-with-orders) and the [lookup reference](https://docs.alpaca.markets/us/reference/getorderbyclientorderid).
- The same Alpaca FAQ warns that an order submission timeout may still have reached execution and says not to resend or mark it canceled until confirmed through Alpaca support/trading staff. A lookup `404` is therefore **not** accepted as permission to resubmit; lookup visibility latency and permanent ID uniqueness are not established by these sources.
- The pinned [`alpaca-py` v0.44.0 TradingClient](https://raw.githubusercontent.com/alpacahq/alpaca-py/v0.44.0/alpaca/trading/client.py) supports `paper=True` but also `url_override`, so paper mode alone is insufficient endpoint validation. Its [REST implementation](https://raw.githubusercontent.com/alpacahq/alpaca-py/v0.44.0/alpaca/common/rest.py) calls Requests without a finite timeout and retries configured HTTP response codes, including on POST; the tagged [defaults](https://raw.githubusercontent.com/alpacahq/alpaca-py/v0.44.0/alpaca/common/constants.py) include 429 and 504. The default SDK submission method is not accepted for an unattended one-attempt order path.

The user-selected Ollama Cloud `gemma4:31b` was verified by an actual public-only technical-analysis response (`route=cloud`, returned model `gemma4:31b`, `success=true`). Its proposed `PENDING`-then-POST state would allow a crash after network send but before a local update to look unsent; its suggestion that `GET 404` could release a pending intent to POST would also conflict with the conservative timeout rule. Neither suggestion was adopted. The durable pre-send marker and no-automatic-replay boundary follow the documented Alpaca ambiguity and local crash test, not the model's proposed transition.

Remaining W3-04 work: bind immutable request bytes, exact paper account/endpoint identity and accepted purpose to the intent; provide a bounded, non-retrying paper transport; record request/result evidence; reconcile unknown outcomes and restart state without treating 404 as rejection or retry permission. Later W3-05 must compare broker orders/positions against local state. No strategy order path is enabled.

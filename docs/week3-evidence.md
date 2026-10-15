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

## W3-04 — Synthetic paper transport contract (2026-10-14; not accepted)

`paper_order_http.py` adds a single-request transport for the documented paper `POST /v2/orders` and `GET /v2/orders:by_client_order_id` endpoints. Construction rejects any trading base URL other than `https://paper-api.alpaca.markets`, and the real HTTPS helper hard-codes `paper-api.alpaca.markets`; no override or redirect path exists. The transport requires caller-supplied positive timeout and byte caps, makes no retry, and treats transport failure as an unknown outcome. A 504 is returned as evidence to the caller, never automatically replayed. Its socket I/O is bounded by a monotonic deadline and per-operation timeout; operating-system DNS resolution is not proven to obey that deadline.

The only POST entry point reads a previously reserved journal intent. It checks the exact paper endpoint, matching opaque paper account reference, `ENGINEERING_TEST` purpose and canonical saved JSON request with the same `client_order_id`; it commits the one-time send marker before invoking HTTP. A restarted call for that ID fails locally even after a 504 or timeout. This account reference has **not** yet been matched to a live paper account response, so this is a synthetic transport contract, not account authorization. No executable Ross or hybrid strategy can use this purpose. The module is not connected to a scheduler, CLI or service, and tests inject fake responses or a fake HTTPS connection; they make no broker request.

The new transport requires a fresh G02 review because the original W1 proof relied partly on the absence of any order path. Synthetic tests reject a live-host `PaperConfig` and confirm the default HTTPS helper uses only the hard-coded paper host. This preserves the paper-only property for the new component, but does not accept W3-04: durable broker request/result evidence, real account binding, unknown-outcome lookup and broader order/position reconciliation remain open. G03/G04 and strategy authority remain blocked. The `Journal` now rejects order-intent writes and adapter reads unless its runtime root and database file are both owner-owned with no group/other permissions. The negative test sets broad permissions and verifies that no intent is reserved; the actual host runtime path has not been validated for account/order data.

Verification used only injected responses and a fake HTTPS connection. It covered exact paper host, rejected live configuration, account/purpose mismatch, canonical request and matching client ID, one durable POST attempt after 504 or timeout, response/request byte caps, and no implicit retry. No paper or live endpoint was contacted. From `/mnt/t/alpaca-bot`:

```text
rtk proxy env PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -p no:cacheprovider -q  -> 129 passed; one upstream websockets.legacy deprecation warning
rtk proxy env RUFF_CACHE_DIR=/tmp/alpaca-w3-ruff .venv/bin/ruff check src tests  -> All checks passed
rtk proxy env RUFF_CACHE_DIR=/tmp/alpaca-w3-ruff .venv/bin/ruff format --check src tests  -> 44 files already formatted
rtk proxy env MYPY_CACHE_DIR=/tmp/alpaca-w3-mypy .venv/bin/python -m mypy --strict src tests  -> no issues in 44 source files
```

## W3-04 — Durable HTTP observations (2026-10-15; not accepted)

Journal schema 4 adds append-only `order_http_observations`, keyed in order by a local sequence and associated with a prior send marker. Each observation records whether the request was POST or GET, when it completed locally, and either the adapter-capped HTTP response body and status or an explicit transport-unknown result. A schema-3 migration test preserves the prior intent and send marker. A failed observation write cannot clear the send marker; it must be treated as an unresolved outcome. The request body remains in the immutable reserved intent. All order observations are stored under the owner-only runtime path gate.

The paper adapter now persists a POST observation before returning the response to its caller, and its GET-by-client-ID path requires the same reserved paper account/endpoint/purpose and a prior send marker before contacting the paper host. A simulated 504 followed by a restart and GET 404 leaves both responses in the journal, while the duplicate POST remains blocked. A 404 is only lookup evidence, not proof of order rejection or permission to resubmit. Transport exceptions record `TRANSPORT_UNKNOWN` when local storage remains available. These tests inject fake responses and do not contact Alpaca.

This closes a local evidence gap but does not accept W3-04. The opaque account reference is not bound to a verified paper account; broker order responses have not been interpreted against actual order records or positions; a missing/contradictory broker result has no accepted recovery disposition. The component remains disconnected from an automatic runner and cannot authorize Ross or hybrid strategy orders. The current implementation does not prove DNS resolution obeys its timeout. W3-05/06/07 and formal paper remain gated.

Validation from `/mnt/t/alpaca-bot`: the full suite passed with 313 tests (one upstream `websockets.legacy` deprecation warning) after the schema/adapter change. A further simulated result-storage failure test passed in the 27-test journal/transport subset: the send marker survived restart and prevented another POST. After that test addition, Ruff check and format, strict mypy across 52 source/test files, and `git diff --check` passed. The added test did not contact Alpaca or use real account data.

## W3-04 — Account-binding investigation and component acceptance (2026-10-15)

The remaining account gap was an implementation omission: the transport compared a caller-provided account label without authenticating its UUID. Schema 5 adds immutable account-verification evidence and binds one private journal to one account reference derived from the authenticated UUID. The adapter verifies the account through the hard-coded paper host before each submit/lookup. A mismatched account is rejected before it can bind a journal containing another account's intent; later foreign-account reservations fail in the same transaction. Request/result evidence remains in project-owned SQLite state. No credentials or raw account identifiers are placed in Git.

Submission eligibility is deliberately separate from identity: new engineering submissions require ACTIVE/USD and explicit false trading-block flags. Account blocks or unknown flags do not prevent identity-verified read-only lookups for an existing uncertain order. The supported engineering request contract is simple, quantity-based equity, market/limit, day, without extended hours; this is a bounded engineering surface, not a Ross/hybrid trading rule. Other shapes fail closed. Response checks match client ID, symbol, side, type, time in force, quantity, relevant prices, extended-hours flag and broker UUID; invalid fill quantities, unsupported complex/replacement paths and UUID conflicts across valid persisted responses remain unresolved. A matched lookup records a response hash and never releases the send marker or authorizes replay.

Primary-source retrieval used an independent GPT-6.1 Sol Low worker and coordinator source review on 2026-10-15: [account endpoint](https://docs.alpaca.markets/us/reference/getaccount-1), [tagged SDK account/order models](https://github.com/alpacahq/alpaca-py/blob/v0.44.0/alpaca/trading/models.py), [order-list scope and cap](https://docs.alpaca.markets/us/reference/getallorders-1), [positions endpoint](https://docs.alpaca.markets/us/reference/getallopenpositions), and [submission-timeout FAQ](https://docs.alpaca.markets/us/docs/working-with-orders). Public-only Ollama Cloud inference returned `route=cloud`, model `gemma4:31b`, `success=true`. Its account/client-ID conflation and claim that empty snapshots prove a clean slate were rejected. Empty lists do not establish historical fills, atomic state, resolution of a pending local send or permission to trade.

The real GET-only audit and its [redacted report](../evidence/paper-order-readonly-2026-10-15.json) verified the authenticated account and captured empty open-order/position snapshots. The private journal has no send marker. Raw responses stay under `~/.local/state/alpaca-bot/paper-engineering`; directory mode 0700, database mode 0600, correct owner and SQLite integrity were verified. No broker order, cancellation, account modification, data purchase or formal paper evaluation occurred.

Resolved/scoped environment faults:

```text
status: ENV_FAULT — resolved for this read-only audit
category: runtime_storage_permissions
evidence: DrvFS without Unix metadata exposed the attempted project runtime directory/database as 0777; the privacy gate stopped account access.
affected_scope: private W3 account/order evidence on the Windows-backed mount
safe_to_continue: local synthetic tests and an owner-only WSL runtime
recommended_action: use the independently configured WSL state directory; verified 0700/0600, ownership, SQLite integrity and the original GET-only audit afterward. No mount/global-permission changes were made.

status: ENV_FAULT — affected operation verified through tool-approved escalation
category: sandbox_network_and_external_runtime_access
evidence: sandbox DNS lookup returned gaierror; a later read-only SQLite WAL connection also required access outside workspace roots.
affected_scope: real read-only API verification and private runtime integrity read
safe_to_continue: local tests; the same explicitly approved GET-only/read-only operations
recommended_action: retain sandbox controls and use scoped tool approval. The approved operations succeeded; global sandbox networking was not changed.
```

Independent local review found and verified fixes for blocked-account lookup suppression, contradictory extended-hours/prices, changed broker UUID, first-binding poisoning and a later foreign-account reservation blocking recovery. The final review reported no remaining actionable finding in the scoped current bytes. This review is synthetic and does not claim an actual broker order cycle or W3-05 lifecycle validation.

Acceptance boundary: W3-04's account-bound engineering transport, durable evidence and no-duplicate-send contract can be accepted independently of W3-05 lifecycle/positions and W3-07 real order-cycle tests. Those later gates and G03/G04/G05 remain open. DNS resolution still has no proven hard deadline; unattended execution/protection requires its later fault/recovery gates. The broader repository checks encountered separate in-progress scanner-replay failures (noncontiguous replay input and a missing `Journal.read_events` dependency); they are not a passing release check or a reason to change scanner code in this W3 task.

Final scoped checks: the journal/HTTP/contract subset passed 62 tests; strict mypy passed for `src`, the three direct test modules and `scripts/audit_paper_order_state.py` (34 source files). Ruff check/format passed for `src`, `tests` and the new audit script (60 files); `git diff --check` passed. The wider run observed 425 passing tests and 27 scanner-replay failures, and wider type checking found two missing-`read_events` errors in that separate test module. These broader failures remain reported and the release gates remain open; they do not invalidate the independently checked W3-04 component contract.

## W3-05 — Lifecycle, SDK updates and account reconciliation (2026-10-15)

This increment implements a read-only engineering reconciliation component for
W3-04's supported simple-equity request contract. It cannot select trades, submit
or cancel orders, resolve incident disposition, or start a formal paper sample.
Strategy, risk, data eligibility, W3-06 protection and W3-07 real order cycles
remain independent gates. It introduces no Ross or hybrid strategy parameter.

### Sources and contract decisions

Independent GPT-6.1 Sol Low retrieval and coordinator inspection covered Alpaca's
[trade-update stream](https://docs.alpaca.markets/us/docs/websocket-streaming),
[order states](https://docs.alpaca.markets/us/docs/orders-at-alpaca),
[order-list pagination](https://docs.alpaca.markets/us/reference/getallorders-1),
[account activities](https://docs.alpaca.markets/us/reference/getaccountactivities-2),
and the pinned
[`alpaca-py` v0.44.0 stream](https://raw.githubusercontent.com/alpacahq/alpaca-py/v0.44.0/alpaca/trading/stream.py).
The installed SDK primitives were inspected as well. Public-only Ollama Cloud
analysis returned `route=cloud`, model `gemma4:31b`, `success=true`; it supplied
review suggestions, not operational authority. Proposed terminal-state rankings,
WS/activity ID equivalence and microsecond-only ordering were rejected.

- The SDK's paper connection/authentication/subscription primitives are reused
  with the exact paper endpoint and pinned version. The wrapper additionally
  requires the `listening` acknowledgement; SDK subscription intent alone is
  insufficient. Incoming binary/text frames are preserved before decoding.
  Outbound authentication is never journaled or logged.
- Order snapshots carry cumulative `filled_qty`. Duplicate WS and REST snapshots
  never add that quantity twice. Provider timestamps retain nanosecond precision;
  local receipt datetimes allow only their 999ns representation uncertainty.
- Activity IDs identify account activity records, not WS execution IDs. FILL
  activity pages are summed independently and compared with cumulative fills.
  Broker UUIDs must map to one owned client ID; activity IDs must not be reused
  across broker orders.
- There is no documented exactly-once/replay cursor for trade updates. Reconnect
  and restart always require REST recovery. Open orders use the documented
  order-ID cursor (500 per page), FILL activities use their last-record ID cursor
  (100 per page); timestamp ties are not pagination cursors. Caps, duplicates,
  loops, failed lookups and incomplete pages remain explicit differences.
- Sequential REST snapshots are not atomic. Two matching capture rounds establish
  observed convergence only. Account authentication brackets collection; owned
  lookups, open orders, positions and activities must agree, with fresh clocks,
  complete pages and unchanged local evidence. A lookup 404 never grants replay.
- `done_for_day`, replacements, corrections and unsupported shapes remain
  `UNKNOWN` or explicit blockers. REST short-position sign interpretation is
  deliberately unaccepted for this long-equity component. Existing protections
  are not canceled or modified.

### Requirement-to-evidence map

| Requirement | Implementation and validation |
| --- | --- |
| Accepted, rejected, partial, filled, cancel-pending, canceled, expired, unknown | `order_lifecycle.py`; pure transition tests preserve cumulative exposure, identity and timestamp conflicts without a terminal-state ranking. |
| Durable raw evidence and atomic projection | `Journal.apply_order_event()` persists frame/batch, deduplication identity and projection in one SQLite transaction. Reducer/storage failures roll back together. Exact duplicates do not add fills; reused identities with different payloads remain conflicts. |
| Restart recovery | `verify_rebuild()` reconstructs all immutable account lifecycle events and requires the cache to reach the latest event. Missing, rewound or changed caches fail closed. A valid restart retains known fills, invalidates stream trust and requires a new reconciliation. |
| Local inputs cannot disappear during reconciliation | Intent, send-marker and successful HTTP history are reread under the write lock; concurrent changes are retained in the batch and block a clean match. |
| WS plus REST fills and orders | `paper_trade_stream.py`, `paper_order_recovery.py`, `order_reconciliation.py`; bounded original-frame capture, explicit subscription/session health, owned lookups and FILL pages. |
| Account inventory and external differences | A one-time baseline must reference a recent, complete, stable same-account recovery batch before any local intent. Open/external orders, unknown lookups, unsupported positions and fill/position differences are explicit. Generic or stale position snapshots cannot establish the baseline. |
| Preserve known exposure on malformed/missing data | Valid order fills survive malformed WS `position_qty`; saved HTTP fills survive a current lookup 404 or missing historical timestamp. Unknown time is explicitly labeled; it is never replaced with an invented broker timestamp. |
| Responsive collection and bounded close | REST collection owns a separate thread-local SQLite connection; stream callbacks remain on the event loop. Close timeout aborts the transport before awaiting cancellation acknowledgement. Incomplete collection is persisted before baseline initialization is refused. |
| No broker write or formal paper authority | The verification CLI uses authentication, subscription and GET requests only. `trading_authorized=false`, `snapshots_atomic=false`; a matched capture cannot authorize a trade, resubmit or release a send marker. |

Synthetic tests cover duplicate/changed execution IDs, shuffled and stale
snapshots, terminal/cancel-fill conflicts, cumulative regressions, nanosecond
ordering, concurrent journal connections, transactional failure, cache rollback, restart,
pagination boundaries/loops/caps, external inventory, wrong account, malformed
frames and unknown HTTP outcomes. Independent reviewers reproduced and checked
the corrections; reviews do not establish real execution or strategy acceptance.

`scripts/reconcile_paper_orders.py` is a finite, opt-in read-only probe. Raw account,
order and stream evidence stays in the owner-only WSL journal; only redacted flags
and difference codes may enter Git. Closing the stream leaves a recovery gap,
including after a successful capture. Missing credentials, account mismatch,
storage failure or incomplete evidence cannot silently produce acceptance.
DNS resolution still has no proven hard deadline; this component must not be
promoted into an unattended exposure/protection runner before later fault gates.

### Verification and component acceptance

The [redacted real verification](../evidence/paper-lifecycle-readonly-2026-10-15.json)
contains two GET-only/SDK subscription captures against the same private journal,
including a fresh process after the first stream closed. Both reconciled the
empty engineering inventory and verified deterministic rebuild; both closed with
a recovery gap. The private runtime's owner and 0700/0600 modes, SQLite integrity,
lifecycle payload hashes and absence of local intents/send attempts were checked
separately. Raw responses and account identity remain outside Git. This verifies
transport integration and empty-account restart recovery, not a real fill,
cancellation, short position or broker-order cycle.

Independent review identified and verified corrections for rewound caches,
concurrent local input omission, lookup404 rollback, malformed position quantity,
reused broker/activity IDs, missing historical timestamps, historical fills lost
behind an unsuccessful lookup, conflicting UUID quantity contamination, blocked
stream callbacks, discarded partial captures and cancellation-resistant close.
After corrections the final focused review approved its scope. The full current
test suite, including async collector tests, passed 673 tests with one upstream
`websockets.legacy` deprecation warning. Ruff check/format and strict mypy passed;
the new verification script was included in static checks. No dependency upgrade
or unrelated scanner/strategy repair was made by this W3-05 increment.

```text
rtk proxy .venv/bin/python -m pytest -q
rtk proxy .venv/bin/ruff check src tests scripts/reconcile_paper_orders.py
rtk proxy .venv/bin/ruff format --check src tests scripts/reconcile_paper_orders.py
rtk proxy .venv/bin/mypy --strict src tests scripts/reconcile_paper_orders.py
rtk git diff --check
```

The full pytest command used tool-approved execution outside the affected
sandbox. Environment evidence and limitation:

```text
status: ENV_FAULT — isolated; affected operation verified through approved execution
category: asyncio_default_executor_shutdown
evidence: a repository-independent asyncio.run/to_thread(lambda: 1) reproduction
returned the worker value but hung in sandbox runner shutdown (5-second cap,
exit124). The identical approved operation printed runner_closed and exited0;
the full test suite and both real read-only probes also exited successfully there.
affected_scope: sandboxed async executor shutdown and test acceptance
safe_to_continue: static/synchronous checks and the same approved test/probe paths
recommended_action: preserve sandbox controls; use scoped tool approval for the
affected checks. The sandbox root cause is not established or globally repaired.
No repository timing/network workaround or global configuration reset was applied.
```

W3-05 is accepted as an engineering component. Its state/report cannot authorize
new exposure, cancel protection, clear an unresolved send marker, enable live
trading or begin formal paper. W3-06 protection/incident rules and W3-07 actual
order-cycle evidence remain open, alongside independent strategy/risk/data gates.

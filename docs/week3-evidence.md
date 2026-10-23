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

## W3-06 through W3-08 — Verified increment, full acceptance open (2026-10-15)

The user requested the execution, risk, SDET and quantitative-validation roles
to complete all three tasks. Their full requirements and dependencies remain
unchanged. This is a checked implementation increment, not completion of those
tasks. Current source is authoritative: the separately accepted
`HYBRID_500_IEX_INTRADAY_RESEARCH_V1` fixes P01–P13 for research, including P11
partial-fill/exit obligations and P12/P13 vetoes. Its
[acceptance record](../evidence/hybrid-500-iex-intraday-research-v1.json)
explicitly grants no executable release, data qualification or order authority.
W3-01/W3-02 and the executable/data gates remain open.

### Incident and cancellation increment

`incident_state.py` projects version/account/round-trip-scoped obligations from
explicit baseline, cumulative order evidence, observed inventory and gate facts.
It preserves known fills and valid protection during uncertainty; tracks entry,
cancel, target and closing obligations; detects missing protection, excess exits,
reverse inventory and live exits after flat. Correctly sized partial target/stop
fills are not mislabeled as oversells. Stale or identity-vetoed events retain
their original fingerprints; changed reuse is a durable conflict. Specific
research obligations require the named version and P11/P12/P13 references.
No proposal dispatches a broker request or authorizes automatic liquidation.

`incident_journal.py` commits raw facts, deduplication identity and projection
atomically in a separate `ALPACA_INCIDENT` namespace. Rebuild checks every scoped
immutable event and its complete deduplication cache. Unambiguous tuple keys
separate scopes. Restart preserves inventory, protection and pending obligations,
adds a recovery veto and grants no dispatch. The persistent component rejects
caller-supplied runtime-accepted flags until the real risk/protection acceptance
verifier exists; a boolean plus an arbitrary hash is not acceptance.

Journal schema 6 adds immutable owned-entry cancel attempts; migration tests
preserve schema-5 account, intent, send, HTTP and event evidence. A cancel marker
requires a sent simple buy engineering intent, matching request/client identity
and one unambiguous broker UUID across valid local evidence. It commits before
DELETE. Unsupported/sell/protective shapes are refused. Duplicate cancellation
is blocked locally, including after restart, timeout or 422. DELETE responses
are append-only evidence and require the prior cancel marker. The existing POST
contract is unchanged.

The shared journal now checks cached deduplication claims against their immutable
source before suppression. The lifecycle rebuild also verifies its complete
deduplication map. This closes a demonstrated orphan-cache case that could hide
a new fill without changing the visible projection. Incident insertion does not
pollute W3-05 lifecycle replay.

Independent review reproduced and verified corrections for two cancellation
ownership gaps and four incident defects: orphan deduplication, scope/event key
collision, changed stale-event evidence loss and legitimate partial-exit false
alarms. The final bounded rereview approved this increment. Relevant tests also
exercise the six W3-06 fault groups at reducer/storage level, failed-commit
rollback, coherent cache rewind, missing/hash/sequence cache faults, restart,
cross-scope identity and the inability to self-grant runtime authority.

### Protection handoff decision remains unresolved

Primary-source retrieval used independent GPT-6.1 Sol Low and coordinator review:
[order/group semantics](https://docs.alpaca.markets/us/docs/orders-at-alpaca),
[cancellation](https://docs.alpaca.markets/us/reference/deleteorderbyorderid-1),
[replacement](https://docs.alpaca.markets/us/reference/patchorderbyorderid-1),
[single-position close](https://docs.alpaca.markets/us/reference/deleteopenposition-1),
and the tagged
[v0.44.0 request models](https://raw.githubusercontent.com/alpacahq/alpaca-py/v0.44.0/alpaca/trading/requests.py).
Cancellation acceptance is not finality. Native bracket exits wait for complete
entry fill; group exits can race. Independent exits reserve available inventory.
These facts leave this exact P11 transition unqualified:

| Phase | Required behavior | Current qualification |
| --- | --- | --- |
| First partial entry | Protect its actual filled quantity at frozen S and cancel the entry remainder | Cumulative ownership/cancel primitives implemented; real stop dispatch, quantity growth and racing fills still need acceptance |
| Entry becomes terminal | Reconcile fills/average F, then fix the whole-position target | Research definition exists; risk/price/charge ownership is still W3-02 work |
| Existing stop to target-plus-stop | Retain protection while establishing the final target | No cited retail API/SDK contract establishes atomic standalone-stop-to-OCO conversion |
| Incident or session close | Act only on owned, reconciled residual quantity through an accepted close disposition | Close-position does not document atomic coordination with a working stop; no dispatcher is enabled |

Documented PATCH fields do not establish group conversion or atomic coupling
with another order's fills. This is an evidence gap, not proof that Alpaca can
never support a solution. Keeping the stop while investigating amendments leaves
target installation unresolved. Cancel-confirm-reconcile-submit-OCO introduces
a protection gap, with no documented bound; a timeout cannot rename that gap as
continuous protection. Per-fill OCO groups introduce early-target and reservation
semantics that are not the accepted P11 policy. None was silently selected.
Broker confirmation or a separately accepted adaptation is required before
accepting this handoff. No account-wide cancel/close endpoint is authorized as a
workaround.

### Four-week protocol and remaining acceptance

The user selected engineering reliability plus strategy-rule execution
consistency; P&L is descriptive, never a pass threshold. The
[W3-08 draft](w3-paper-protocol.md) defines the proposed four-week window,
pre-launch hashes, full natural-candidate census, per-cell positive/negative
coverage witnesses, a natural filled-to-flat witness, independently blinded
prospective fidelity review, six separate engineering fault groups, fixed
restart/abort rules and PASS/FAIL/INCONCLUSIVE outcomes. Coverage witnesses are
not a powered sample or statistical reliability claim. No signal/coverage
shortfall can be repaired by inducing trades, lowering a quota or extending the
window after observing outcomes.

The draft still requires accepted executable/risk/data/incident contracts,
actual calendar/build/account freeze fields and independent review assignments.
W3-07 still requires the real stop/close/cancel integration, an isolated bounded
engineering harness and actual owned-order cleanup/reconciliation evidence after
all dependencies pass. Current tests inject responses; none is a real broker
cycle. No new credential access, broker request, order, cancellation, runtime
migration or formal paper enrollment occurred in this increment.

Public-only Ollama Cloud inference was verified twice as `route=cloud`,
`model=gemma4:31b`, `success=true` for generic incident and study-design critique.
No local proposal, account data or credentials were exported. Claims that a
unique client ID alone makes retries safe, cumulative fills need no baseline,
a closing flag handles already working exits, every four-week study lacks
statistical power, or synthetic packets can replace natural witnesses were
rejected. Deterministic code and source review own the adopted controls.

Validation: the complete current repository suite passed **792 tests**, with one
upstream `websockets.legacy` deprecation warning. Full Ruff check/format passed;
strict mypy passed for `src`, `tests` and `scripts/reconcile_paper_orders.py`
(77 files), and scoped diff checks passed.
The full pytest command used the already verified tool-approved execution path.
The sandboxed async executor-shutdown ENV_FAULT recurred during independent
verification; the affected run was stopped with exit 130. Static/synchronous
checks remained safe, approved full execution completed, and no application or
global-environment workaround was applied. Global sandbox repair is not claimed.


### Protection continuity decision and stop contract follow-up (2026-10-16)

The user chose **retain continuous protection and continue verifying Alpaca's
supported handoff**. A cancel-confirm-submit gap is therefore not an accepted
adaptation. This decision resolves the research-route preference, not the
broker capability or W3-06 dependency. No order, cancellation, paid data, live
trading or formal paper is authorized by this follow-up or the heartbeat.

Public source review still does not establish an atomic standalone-stop-to-OCO
conversion, concurrent target installation against stop-reserved inventory, or
atomic stop-quantity growth with racing entry fills. Alpaca's own forum reply
on [2025-08-15](https://forum.alpaca.markets/t/updating-profit-and-stoploss/11641)
says complex-order quantity cannot be amended; its
[2021-06-24/25 explanation](https://forum.alpaca.markets/t/unable-to-close-positions/6024)
describes sell-quantity reservations and waiting for cancellation confirmation.
These older replies supplement current API documentation; neither proves a
current atomic mechanism absent. The minimal unresolved broker questions are:

1. Which retail Trading API endpoint/fields attach a final target to an existing
   working stop while that original stop remains executable throughout?
2. What documented atomic boundary increases protection for late entry fills,
   and prevents overselling if the stop and additional entry fill concurrently?
3. What exact rejection, timeout, replacement and recovery states preserve the
   same protection and owned-quantity invariants?

No support message was sent and no broker experiment was run. Further retrieval
in the independent worker stopped on a 401/token-expired ENV_FAULT. Coordinator
read-only retrieval subsequently succeeded for these already identified public
URLs; this verifies that tool path only, not repair of the worker's credentials
or completion of the remaining OpenAPI/SDK investigation.

The simple engineering contract now represents standalone SELL STOP/DAY, with
finite positive numbers, exact stop-price response identity and the documented
numeric grid (two decimal places at/above one dollar, four below; numeric
trailing zeros accepted without Decimal-context rounding). Unsupported buy-stop,
complex/replacement and inferred elected-type changes stay rejected. This
shared validator also expands the shapes representable by the existing explicit
engineering POST interface; it is not a runtime protection verifier or automatic
dispatch authority. Both entry-cancel layers still independently reject sell
stops. A dedicated regression uses a fully valid stop request/response and
asserts rejection without HTTP, cancel marker or altered observation evidence.

The [expected calendar](../evidence/w3-paper-calendar-20261026-20261120.json)
enumerates 20 proposed sessions with zoneinfo-derived UTC instants across DST.
It remains draft and requires final manifest binding and broker clock/calendar
review. W3-08 still lacks executable/risk/data/incident acceptance, account
baseline and independent-review freeze fields; W3-06/W3-07 remain incomplete.

Final verification of this follow-up: independent source/adjacent-path review
found no actionable stop-contract defect, then separately checked the valid-stop
cancellation regression and unchanged incomplete launch gates. The full synthetic
suite passed **853 tests**, with only the existing upstream websockets deprecation;
full Ruff check/format and strict mypy passed (77 source files). Calendar checks
verified all 20 local/UTC round trips, 6.5-hour scheduled lengths and the DST
transition. `git diff --check` passed. The full suite used the already verified
tool-approved execution path; no sandbox async repair or real broker cycle is
claimed. Exact final commands were:

- `rtk proxy env PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider --basetemp /tmp/alpaca-w3-stop-final`
- `rtk proxy .venv/bin/ruff check --no-cache src tests scripts/reconcile_paper_orders.py`
- `rtk proxy .venv/bin/ruff format --check --no-cache src tests scripts/reconcile_paper_orders.py`
- `rtk proxy env PYTHONDONTWRITEBYTECODE=1 .venv/bin/mypy --strict --cache-dir /tmp/alpaca-w3-stop-mypy src tests scripts/reconcile_paper_orders.py`

Retrieval environment packet:

```text
status: ENV_FAULT
category: independent_worker_tool_authentication_expired
evidence: worker reported HTTP 401 token_expired during further public retrieval
affected_scope: worker's remaining OpenAPI/SDK handoff investigation
safe_to_continue: local checks and coordinator read-only review of identified public URLs, subsequently verified successful
recommended_action: revalidate worker authentication before resuming that retrieval stage; do not infer atomic protection semantics
```


### Bounded OpenAPI and pinned SDK verification (2026-10-16)

A fresh independent GPT-6.1 Sol Low public-only retrieval succeeded without the
previous authentication failure. Coordinator source review verified the official
[published Trading OpenAPI](https://raw.githubusercontent.com/alpacahq/alpaca-docs/master/oas/trading/openapi.yaml)
`PatchOrderRequest` and the pinned
[v0.44.0 replacement model](https://raw.githubusercontent.com/alpacahq/alpaca-py/v0.44.0/alpaca/trading/requests.py).
Both describe six replacement fields without an order-group conversion or
existing-stop attachment field. The repository OpenAPI is an older corroborating
artifact, not a complete current-server contract: the current retail reference
has additional IPO/advanced fields. Creation-model support for linked exits
does not establish adoption of an already working stop. Documented sibling-stop
quantity adjustment after partial take-profit execution likewise does not
establish atomic protection of partial buy-entry fills.

The original order may fill before a replacement reaches the venue; replacement
HTTP success is not finality. No inspected contract establishes the required
continuous-protection handoff. This is a bounded evidence gap, not an exhaustive
absence claim or permission for a cancel-and-resubmit adaptation. Tagged SDK
test retrieval encountered a generic web-tool Internal Error, so exhaustive
test/maintainer-issue coverage is not claimed. The remaining exact broker
questions above still require an authoritative answer before implementation
acceptance. W3-01/W3-02 remain pending and the research acceptance manifest still
has executable release disabled. No dependency, launch status or runtime code
was changed; unchanged tests were not rerun for this documentation-only update.


## W3-01/W3-02 — Offline implementation verified; original acceptance open (2026-10-16)

The user requested completion of the strategy/risk prerequisites. Current
W2-05 and W2-06 have accepted independent research-path components, so the
known P01–P13 definitions were implemented without changing their policy,
research digest or Ross baseline. This is a checked implementation increment,
not completion of the original executable/data-qualified dependency. The
[hash-bound verification record](../evidence/w3-entry-risk-offline-2026-10-16.json)
records the exact nine implementation/test files and open gates.

`strategy_features.py` implements exact rational EMA from exactly 100 expected
bars, complete provider-minute aggregation anchored to supplied session open,
truncated final buckets, as-of revisions and opening feature freeze.
`entry_strategy.py` implements reserved-first monitor proposals, gap/pullback,
strict prior-high crossing, session invalidation, decision-clock timeframe
switch/expiry, no chase, and typed unavailable-input vetoes. It emits research
signals only. Unknown source/condition/continuity facts do not qualify a trade.
The arming observation can supply the crossing baseline; a threshold change
alone cannot produce a signal, and switching timeframes discards the old
baseline. No undocumented discretionary trade rule was filled in.

`strategy_risk.py` computes exact next-cent limits, frozen floor-cent stops,
maximal whole-share size with complete quantity-specific entry/stop-exit charge
bounds, P12 capacity without double-counting accrued/remaining charges,
reconciled-terminal 2R targets, daily/streak latches and closing obligations.
Daily state binds account, date and frozen E0; sizing requires that state and
carries the same provenance. A false ledger latch cannot override three losses
or an already latched daily state. No fee, borrowing or liquidity default is
assumed. The production account/fee/mark verifiers and durable operational daily
owner are not supplied by these calculation dataclasses.

`strategy_attempts.py` binds the pinned specification and private journal account
to one deterministic research intent per symbol/date/version. Signal appearance
does not consume the allowance; a successful durable write does, and restart
recovers consumption from the intent. Risk account/date/E0 and signal prices
must match. Failed persistence consumes nothing. These intents use a distinct
non-dispatch purpose and have no broker request body; an injected-transport test
verifies the existing paper adapter rejects them before HTTP or a send marker.
This is not a production slot/reservation or execution acceptance.

Independent review reproduced and corrected malformed boolean qualification,
duplicate-event suppression bypassing current veto/expiry, and missing sizing
account/date/E0 provenance. Root review additionally required frozen daily E0
and independent latch enforcement. Final bounded review approved those changes.
An independent closed-form EMA oracle checks the recurrence under low Decimal
precision; integration verifies gap-to-pullback-to-cross, explicit nonzero fees,
sizing and durable non-dispatch attempt. The five targeted files passed
**149 tests**; the complete repository passed **1008 tests**, with only the
existing upstream websockets deprecation. Ruff check/format and strict mypy
passed for 88 files; diff checks passed. Commands:

- `rtk proxy env PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider --basetemp /tmp/alpaca-w30102-full`
- `rtk proxy .venv/bin/ruff check --no-cache src tests scripts/reconcile_paper_orders.py`
- `rtk proxy .venv/bin/ruff format --check --no-cache src tests scripts/reconcile_paper_orders.py`
- `rtk proxy env PYTHONDONTWRITEBYTECODE=1 .venv/bin/mypy --strict --cache-dir /tmp/alpaca-w30102-mypy src tests scripts/reconcile_paper_orders.py`

The full test run used the previously verified tool-approved execution path
because sandboxed async executor shutdown remains an isolated environment
limitation. No repair of that global behavior is claimed. Tests use synthetic
account/market/fee facts; no credentials, broker operation or private runtime
state were accessed. Public-only Ollama Cloud inference was verified as
`route=cloud`, `model=gemma4:31b`, `success=true` for a generic exact-arithmetic
engineering checklist. No local strategy specification or account facts were
exported. Its ratio-only sizing suggestion and blanket architectural rules were
not adopted; coordinator calculations, source review and tests govern the code.

Independent Sol Low primary retrieval confirmed that Alpaca's
[stream schema](https://docs.alpaca.markets/us/docs/real-time-stock-pricing-data)
documents timestamps/conditions and bar revisions but does not establish IEX
clock forwarding or a trigger whitelist. The
[current FAQ](https://docs.alpaca.markets/us/docs/market-data-faq) has field-specific
bar conditions; the [older aggregation article](https://alpaca.markets/learn/stock-minute-bars)
uses different clock wording. Latest-trade endpoint filtering does not define
raw-stream trigger eligibility. These sources do not turn historical bars into
point-in-time inputs or promise the user's 1000ms research bounds. Actual source
qualification and runtime ownership remain open; original W3-01/W3-02 are not
checked complete, and W3-06/07/08 or formal paper are not unblocked.

## W3-01/W3-02 — Actual input capture and durable risk increment (2026-10-16)

User request: complete actual market/trade-condition, account/fee input and
operational daily-risk ownership qualification. This increment completes the
bounded collection, exact fee calculations and offline persistence checks;
**actual executable acceptance remains blocked** by the specific gates below.
No broker order, cancel, account reset, data purchase or formal paper evaluation
occurred. The Ross baseline remains separate from the hybrid research version.

### Actual evidence, not synthetic readiness

`scripts/qualify_risk_inputs.py --connect --stream-seconds 10` used the existing
paper credentials locally, GET-only account/configuration/position/open-order/
activity/calendar endpoints and a finite IEX SPY stream. Raw bodies, request
clocks, source identities and hashes are retained in a new owner-only WSL
`~/.local/state/alpaca-bot/risk-input-qualification-20261016/journal.sqlite3`.
It did not reuse the order-engineering journal. Summaries omit account IDs,
balances, credential values and raw account bodies. `PRAGMA integrity_check`
and immutable event read/hash verification passed.

Initial capture ended 18:53:51 UTC. Independent review found an integration
defect: producer requested `nested=true` while the flat-order inspector required
`nested=false`. This was corrected, covered by the collector integration test,
and a new capture was appended, preserving the first capture. Corrected capture
ended **18:58:40 UTC**, account event 331, calendar 330, condition metadata
332–337. Its one activity page is an observation, not completeness proof.

- Actual equity does **not** equal the user's USD 500 target. Exact account
  values stay private. No reset or artificial substitution of 500 occurred.
- Capture is after the session open. It cannot create today's pre-session E0;
  the collector deliberately freezes no baseline, even on a pre-open run.
- Pending-transfer fields and day-trading buying power were absent. Missing
  fields stay unknown, not zero. Day-trading buying power is not inferred to
  grant leverage or made a strategy requirement. The restrictive flat 1x
  available-funds profile remains `UNQUALIFIED`; settlement is `UNKNOWN`.
- Cash-flow/charge completeness, reservations, bid marks and activity-page
  completeness are not established by a GET response or an empty positions list.
  Actual ledger inputs are therefore not promoted to `RiskAccount`/`RiskLedger`.
- Corrected SPY sample has 1,046 observations: 1,025 quotes and 21 trades. All
  measured ages met the one-second research bounds at collection time. This is
  not a provider-clock or latency guarantee, strategy sample, or SIP parity.
  Closing the finite observer requires continuity recovery before any reuse.

The initial checker rejected the 21 trades because it stripped a condition code.
Actual official metadata at
`https://data.alpaca.markets/v2/stocks/meta/conditions/trade?tape=A` and `tape=B`
maps exact **U+0020** to **Regular Sale**. Symbol/exchange validation must remain
strict, but condition codes must preserve this legitimate byte. This schema
correction does not accept a strategy trigger whitelist. Unknown/unmapped
conditions and corrections remain explicit; runtime eligibility stays false.

### Fee source and calculation

The canonical [Alpaca brokerage fee schedule](https://files.alpaca.markets/disclosures/library/BrokFeeSched.pdf)
is revised **2026-10-12**. Its revision is not asserted to be a separately stated
effective date. The exact source PDF hash and verified policy are bound in
`evidence/alpaca-equity-fees-20261012.json`; policy-byte changes fail verification.
For the documented ordinary retail NMS stock scope: ordinary commissions are
zero; SEC sells use 0.0000206 of executed value; TAF is zero; CAT uses 0.000003 per
executed equivalent NMS share on both sides. Each fee type is aggregated daily
per account and independently rounded upward to a cent. Do not import the older
support page's per-order rounding or other account/option/router fee profiles.

`equity_fees.py` uses exact fractions, duplicate/conflicting-fill detection and
separate daily ceilings. Prospective leg bounds conservatively bound incremental
daily fees; they are not an actual broker per-order minimum. A stop-price fee
calculation does not bound every possible exit price or guarantee a stop fill.
Neither a policy hash nor this calculator establishes actual account tariff
applicability or `FeeBounds.verified`.

[Alpaca paper documentation](https://docs.alpaca.markets/us/docs/paper-trading)
states paper omits regulatory fees and several live execution effects. User
choice of conservative source-based paper risk charges versus platform-actual
paper charges is pending; raw broker and modelled fee ledgers must remain
separate. No choice has been silently made.

### Durable owner and honest authority boundary

`risk_journal.py` owns one account/version projection via Journal's serialized
transaction and immutable event/rebuild checks. It preserves daily E0 identity,
loss streak/latch, completed roundtrip identities and a single held slot across
restart. Only matching reconciled terminal evidence can retire a held slot;
changed duplicate outcomes, stale clocks and corrupted caches fail closed.
Referenced events must exist, hash-verify and be visible before the decision
clock, both before resolving inputs and during replay.

Independent review reproduced an important initial gap: injected resolver facts
could yield a positive offline size even with nonexistent references. References
are now checked, and the resolver is expressly **OFFLINE_FIXTURE_ONLY**, including
persisted qualification and public `runtime_qualified=False` with explicit
`ACTUAL_RISK_INPUT_QUALIFIER_UNAVAILABLE` reason. Source presence alone does not
prove account semantics. No injected callback is an actual input acceptance
adapter; constructing the owner without one refuses observe/reserve. A concrete,
source-qualified operational account/fee/reconciliation resolver remains open.

### Remaining acceptance, without lowering the original gates

1. Resolve the paper fee-policy choice and actual account tariff applicability.
2. User decision confirmed 2026-10-16: preserve the existing paper account and
   its history; use a dedicated USD 500 paper account. This selects the account
   separation policy, not proof that an additional account has been created or
   that Alpaca supports a particular provisioning method. Do not reset the
   existing account or reuse its credentials for the new account by assumption.
   Obtain and verify the dedicated account configuration; qualify funds from
   evidence, including handling omitted fields without inventing values.
3. Acquire a genuine pre-session E0 for a future session, bind it to account and
   official calendar, and implement/accept the actual source-derived ledger and
   reconciliation resolver. Intraday snapshots cannot backfill it.
4. Accept source-specific clock, trade-condition and quote-condition semantics
   for the hybrid version; IEX remains `NOT_SIP_EQUIVALENT`. Schema correctness
   and descriptive names do not authorize trade selection.
5. Actual fill/protection and stop-to-target handoff qualification remain separate
   W3-06/07 gates. No broker-writing experiment was used to bypass them.

Validation: full offline regression **1,128 passed** (one existing upstream
websockets deprecation); Ruff/format and strict mypy passed for the new scope
(98 source/test/script files). Subsequent condition-code regression/replay and
final independent findings disposition are recorded below. W3-01/W3-02 remain
`[!]`; no full acceptance or paper/order authority is claimed.

Final disposition: independent review passed all three corrections within the
stated offline scope, running **129 targeted tests**. Coordinator targeted checks
also passed 129 tests, Ruff/format and strict mypy. A read-only replay of the
preserved corrected capture (no new network sample) confirms **all 1,025 quotes
and 21 trades pass the corrected structure checks**. The original collection
summary is preserved rather than rewritten. This does not retroactively accept
provider-clock origin, condition-selection policy or runtime qualification.

### Dedicated-account provisioning evidence (2026-10-16 follow-up)

Independent GPT-6.1 Sol Low public retrieval and coordinator source readback
confirmed the [official dashboard creation route](https://docs.alpaca.markets/us/docs/paper-trading):
select the paper account menu, choose Open New Paper Account, and generate the
new account's own keys. This is documented separately from account deletion;
the user's existing account/history can therefore be preserved by the
documented creation workflow. No account provisioning or credential mutation
has been performed here.

The source still mixes new create/delete instructions with legacy reset and
arbitrary-balance wording. It does not establish the present creation dialog's
USD 500 control/minimum or this user's remaining account capacity. These need
actual dashboard verification before submission. Follow-up source readback of
the [official guide](https://alpaca.markets/learn/start-paper-trading), revised
2026-03-23, confirms its published maximum of **3** distinct paper accounts
under “Key Differences Between Paper Trading API vs Live Trading API.” This
does not establish the user's current remaining slots. The user has been asked to
check the supported dialog, without sending credentials through chat. W3-08 now
records the confirmed separation policy and this precise provisioning gap.

User confirmation received 2026-10-16: the new-account dialog allowed USD 500
and the dedicated account has been created. This resolves the user-side UI and
creation question, not authenticated broker verification. Its distinct identity,
actual balance, account capabilities, positions/open orders and account-specific
credentials remain to be verified locally through GET-only capture in a new
private journal. Preserve the old account and credential file; do not silently
reuse the earlier capture as evidence for the new account.

Credential preparation: `scripts/configure_paper_credentials.py` now accepts
an explicit `--output` path, preserves existing files unless `--replace` is
explicit, and refuses a getpass fallback that cannot guarantee hidden input.
Use a distinct owner-only WSL file for the dedicated account; no new secret
has been supplied, loaded or saved by this change. The old default file remains
untouched. Verification covered the existing credential storage tests (2 pass),
the CLI help, Ruff/format and strict mypy; broker verification remains pending.

### Dedicated USD 500 account — authenticated verification (2026-10-16)

After the user confirmed secure credential setup, a finite GET-only capture
completed at **19:31:34 UTC / 12:31:34 America/Los_Angeles**. Account/calendar
events 2/1 and metadata events 3–8 are preserved in a separate owner-only
`~/.local/state/alpaca-bot/risk-input-qualification-500-20261016/journal.sqlite3`.
Opaque authenticated account bindings differ from the existing account. Actual
GET responses show ACTIVE/USD, blocking flags clear, cash and equity both
exactly USD 500, zero positions and zero open orders. No old credential file,
account history or old journal was replaced. No order or account mutation was
performed. The first invocation used an incorrect explicit home path and
stopped at credential lookup; corrected home-relative lookup succeeded. This
was a local invocation error, not a broker rejection or account fault.

The finite IEX sample contained 1,494 records, all passing current trade/quote
structure and measured-age checks; observed names include Regular Sale, Odd Lot
Trade and Regular Market Maker Open. Metadata names do not authorize a trigger
policy. This is diagnostic SPY coverage only, `NOT_SIP_EQUIVALENT`; clock origin
and strategy condition qualification remain open. Closing the observer requires
continuity recovery. SQLite integrity and all immutable source hashes passed.

This closes the new account's identity, target cash/equity and observed-empty
inventory checks. It does not close complete fund/charge/flow/reservation
qualification: omitted transfer fields remain unknown. Capture was intraday,
not a pre-session E0, and no baseline was frozen. Actual risk-input resolver,
fee-policy choice, data/clock/condition acceptance and continuous protection
handoff remain open. W3-01/02/06/07/08 are not promoted by this account check.

### Paper fee-policy decision — confirmed 2026-10-16

The user selected conservative bounds from the reviewed official fee schedule
for the paper risk budget, with paper's original broker fees reported separately.
This resolves the fee-policy preference question. The source-versioned
`equity_fees.py` calculations remain the mathematical basis: exact execution
quantities, separate account/day fee-type ceilings, conservative incremental
entry/stop reservations and no invented broker per-order minimum.

W3-08 now records separate raw-broker and modelled-cost ledgers/report fields,
reservation-versus-accrual treatment, and a requirement to reconcile rather than
double-count fees. This is a user-authorized paper accounting convention, not a
Ross rule, broker-posted fee, proof of tariff applicability, live-fee promise or
runtime authorization. Existing tests already cover the unchanged arithmetic;
no new executable fee acceptance flag or broker call is introduced here.

Remaining work is implementing and accepting the actual source-derived ledger
adapter under this convention, including execution completeness, charge matching
and reservation retirement. The missing pre-session E0, funds/flow inputs,
data/condition/clock and continuous-protection gates remain unchanged. Neither
an accepted fee convention nor the USD 500 identity check closes W3-06/07/08.

## Actual posted ledger and pre-session E0 mechanism (2026-10-16)

User request: complete actual ledger integration and risk acceptance. The
source-bound GET ledger inspection, cash-audit adapter and genuine E0 mechanism
are implemented and tested. **Full operational risk acceptance is not achieved**:
current-funds qualification, owned nonflat inventory/marks/reservations and
protection remain open, and no genuine pre-session E0 exists for today. A
callback returning verified flags is not used to close these gates.

### Implemented contracts and evidence

- `posted_ledger.py` consumes immutable account-bound HTTP captures, retains
  exact posted buy/sell cash and signed non-trade amounts, and separates original
  FEE posts from the user-selected modelled regulatory costs. Actual execution
  `qty`, not `cum_qty`, drives cash/fees; arithmetic uses exact fractions and
  reviewed daily fee-type ceilings. Changed activity IDs, future timestamps,
  inexact numbers and broken cursor chains fail. Unknown classifications remain
  unknown rather than zero. Unfiltered all-activity pagination requires an
  explicit terminal empty response; a short page or page cap is not exhaustion.
- `risk_baseline.py` reads the official calendar and actual pre-open account
  source records. A flat positive equity observation is separate from available
  funds qualification, so missing transfer fields are not invented. Creation
  rechecks timing and absence of local intents under the SQLite write lock.
  One account/session key cannot be replaced with new references or equity.
- A second immutable durability receipt proves that the first baseline
  transaction had already committed when the actual local clock was observed
  before the official open. Delayed inspection/commit, crash without receipt,
  changed projections and receipt/source hash conflicts fail closed. A missing
  receipt cannot be retrospectively certified after open. Receipt qualification
  begins at its actual `available_at`; historical consumers cannot use proof
  received later, even when the original candidate was pre-open.
- `risk_ledger_audit.py` joins actual captures, calendar, baseline and receipt
  hashes. It checks preserved baseline activity IDs, exact posted cash deltas,
  cash/quantity agreement for a flat book, late baseline fills and unknown costs.
  Original broker fees and modelled fee deltas remain separate. Foreign fills,
  short prefixes, nonflat inventory and unresolved ownership are explicit
  failures/unqualified paths. The audit is **not** a complete nonflat operational
  risk resolver and does not make `RiskJournal`'s fixture seam authoritative.
- `qualify_risk_inputs.py` now saves the complete ledger assessment privately
  and emits only a redacted summary. Optional `--freeze-baseline` cannot invent
  an intraday E0. Closing its finite IEX observation still requires continuity
  recovery; all runtime/order authorization remains false.

Independent GPT-6.1 Sol Low retrieval and coordinator review checked
[activity fields and paging](https://docs.alpaca.markets/us/docs/account-activities),
[current query semantics](https://docs.alpaca.markets/us/reference/getaccountactivities-2)
and [account field definitions](https://docs.alpaca.markets/us/docs/account-plans).
Creation filters are not execution/settlement filters. An empty terminal page
only exhausts the currently returned result; no atomic snapshot, maximum late
posting delay or finality watermark was established. Net-amount dates cannot
silently assign intraday attribution. Public-only Ollama Cloud analysis returned
`model=gemma4:31b`, `success=true`; its generic completeness checklist was checked
against these sources, not treated as operational authority. No private source
or account data was exported.

### Independent findings and validation

Fresh independent review found and verified fixes for three material issues:
pre-open validation followed by a post-open commit; discarded nanosecond
remainders in a baseline comparison; and historical use of a future receipt.
Final independent review passed **79 tests** within this scope. Coordinator
full offline regression passed **1,213 tests**, with one existing upstream
websockets deprecation. Ruff check/format, strict mypy (**105 files**, including
the affected scripts) and diff checks passed. Tests cover restart/tamper,
cursor exhaustion, duplicates, unknown NTA, fee separation, DST/half-days,
source/account conflicts, delayed commit and two-phase crash recovery.

Commands, from `/mnt/t/alpaca-bot`:

```text
rtk proxy env PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider --basetemp /tmp/alpaca-ledger-full tests
rtk proxy .venv/bin/ruff check src tests scripts/qualify_risk_inputs.py
rtk proxy .venv/bin/ruff format --check src tests scripts/qualify_risk_inputs.py
rtk proxy .venv/bin/mypy --strict --cache-dir /tmp/alpaca-ledger-final-mypy src tests scripts/qualify_risk_inputs.py scripts/configure_paper_credentials.py scripts/reconcile_paper_orders.py
```

The full async suite used approved execution outside the previously confirmed
sandbox executor-shutdown fault; no application workaround was added.

### Actual dedicated-account audit

The new implementation completed a finite GET/IEX capture at **19:51:09 UTC /
12:51:09 America/Los_Angeles**. In the existing dedicated-account private journal,
official calendar event 663 and account event 664 feed assessment 665. Hash and
SQLite integrity checks passed. Actual observed activity/fill/position/open-order
counts are all zero; no NTA is observed, modelled regulatory fees are zero, and
the target-equity check still matches USD 500. There are zero local order intents,
baseline candidates and durability receipts. The requested intraday freeze
correctly reports `BASELINE_QUALIFICATION_FAILED`, `PRESESSION_E0_MISSING` and
`cash_matches=null`; zero events are not permission to substitute a baseline.
No broker write, reset, cancellation or formal paper run occurred.

### Next actual evidence window

The captured broker calendar lists October 19 as a session. The existing W3
Codex heartbeat `alpaca-bot-w3-strategy-risk-execution` was updated, not duplicated,
to a one-time **2026-10-19 06:20 America/Los_Angeles / 13:20 UTC** capture, ten
minutes before the documented regular open. Saved schedule readback preserves
the explicit UTC start and same target chat. Its prompt requires a fresh clock,
official calendar, dedicated credentials and protected state; it attempts the
already checked collector only between 06:15 and 06:25 Los Angeles time. A missed
window is `NOT_CAPTURED`, never a backfilled E0. Existing E0 evidence must be
verified rather than replaced. On completion or a missed window it deletes this
one-time automation. A successful capture is only a prerequisite, not risk/order
or formal-paper authority. Existing W4 scheduling is unchanged.

[Official scheduled-task documentation](https://learn.chatgpt.com/docs/automations?surface=app)
requires the computer powered on, desktop app running and project available for
local execution. Wake-from-sleep and missed-run catch-up were not established;
neither is promised. Notify only on completion, failed gates or required action.

Resolved bounded research allocation fault:

```text
status: ENV_FAULT (resolved for the affected research stage)
category: subagent_allocation
evidence: one research follow-up reported agent thread limit reached; a later
  follow-up completed primary-source retrieval after active work changed
affected_scope: scheduled-task public-source retrieval
safe_to_continue: deterministic code/tests were independent; retrieval later verified
recommended_action: preserve source routing; do not infer why capacity recovered
```

Original W3 acceptance remains open. Full funds/flow/charge and owned nonflat
reconciliation, qualified bid marks, risk reservations, data conditions/clocks
and protection continuity cannot be inferred from this flat-account audit.

## Reported funds, ownership and bid inputs — 2026-10-16

The new `risk_ownership.py`, `risk_marks.py` and `risk_input_evidence.py`
qualify immutable source observations, not executable strategy or runtime risk
authority. Historical lifecycle inspection does not append a START event or
consume future evidence. Every selected capture, quote and metadata reference
is checked against its journal hash and availability time.

For an actually reported 1× account, the derived conservative reported notional
cap is `min(cash, available buying_power)`. This is an engineering bound, not
an Alpaca formula, settled-cash proof or promise of future availability. Open
broker buy reservations already affect available buying power and are not
subtracted twice. Unrepresented local intents or unexplained pending cashflows
veto qualification; absent optional transfer fields remain unknown. NMBP is
not silently substituted as a user-selected settled-only policy. Sources:
[account fields](https://docs.alpaca.markets/us/docs/account-plans),
[order buying-power checks](https://docs.alpaca.markets/us/docs/orders-at-alpaca),
[settlement distinction](https://alpaca.markets/support/why-do-i-get-an-error-of-insufficient-balance-when-my-cash-balance-holds-the-requested-amount).

Nonflat ownership requires source-bound lifecycle reconciliation and matching
local intents, broker orders and cumulative fills. Valuation uses the latest
visible eligible regular IEX bid plus authenticated condition metadata; broker
`current_price` is not a bid. The profile uses provider-reported event clocks,
not proven exchange-origin clock accuracy. IEX remains `NOT_SIP_EQUIVALENT`;
this does not establish executable liquidity, NBBO or entry-data eligibility.
Sources: [position marks](https://docs.alpaca.markets/us/docs/working-with-positions),
[trade updates](https://docs.alpaca.markets/us/docs/websocket-streaming),
[stock stream](https://docs.alpaca.markets/us/docs/real-time-stock-pricing-data).

Actual dedicated-account capture at **21:51:48 UTC / 14:51:48 Los Angeles**
persisted account event 1305 and reported-input assessment 1315 in the existing
private journal. Available funds, ownership and bid valuation all qualified;
positions and valued positions were zero, so valuation is exactly zero without
inventing a quote. Hash/SQLite integrity passed and the USD 500 target matched.
The finite stream returned zero records after regular hours: no fresh live bid
is claimed. Separately, actual preserved quote 1302 with metadata 670 passes the
new bid verifier at its original **19:51:09.449237 UTC** decision cutoff. This
is a historical diagnostic witness; nonflat position integration is tested with
synthetic fixtures, not claimed as actual broker experience.

Unknown transfers, non-atomic snapshots, pre-session E0, full accounting,
runtime reservations, protection and executable release remain separate gates.
The older strict account/full-ledger assessment therefore still reports its
own unqualified status. The new observer result does not override it. No broker
write or formal paper evaluation occurred. The October 19 E0 schedule is unchanged.

Full regression: **1,282 tests passed**, one existing upstream websockets
deprecation. Independent scoped review passed **75 tests**, with no remaining
material findings. Ruff check passed, format checked 110 files, strict mypy
passed 112 files. Commands:

```text
rtk proxy env PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider --basetemp /tmp/alpaca-risk-ready-full tests
rtk proxy .venv/bin/ruff check src tests scripts/qualify_risk_inputs.py
rtk proxy .venv/bin/ruff format --check src tests scripts/qualify_risk_inputs.py
rtk proxy .venv/bin/mypy --strict --cache-dir /tmp/alpaca-risk-ready-final-mypy src tests scripts/qualify_risk_inputs.py scripts/configure_paper_credentials.py scripts/reconcile_paper_orders.py
```

Independent GPT-6.1 Sol Low retrieved primary sources. A public-URL-only Cloud
critique returned `gemma4:31b` successfully; conclusions were checked against
the original sources. No private account or local document was exported.

## Published protection conflict and vendor question — 2026-10-16

Current interpretation (2026-10-20): the vendor-only wording below is historical.
Public native mechanisms must be evaluated first; support is an optional route
for the remaining specific undocumented guarantees, not a universal development
prerequisite. See [the native-mechanism review](#native-api-compatibility-review--2026-10-20).

**Failed gate:** Alpaca's [user-protection table](https://docs.alpaca.markets/us/docs/user-protection)
explicitly rejects a new stop sell while a limit buy remains open, including
paper. This conflicts with protecting the first partial fill using a standalone
stop while the remaining entry stays working. Bracket exits activate only when
the entry fills completely; they do not establish the required partial-fill
coverage. No rejection was experimentally provoked and no protection bypass
is proposed.

The current [PATCH contract](https://docs.alpaca.markets/us/reference/patchorderbyorderid-1)
has eight properties, including `advanced_instructions` and `notional`; the
pinned SDK's smaller request model is not the exhaustive REST contract. Neither
PATCH nor its advanced routing fields documents adoption of an existing stop
into OCO. The [October 8 retail grouping update](https://docs.alpaca.markets/us/changelog/2026-10-08-order-group-db02af7)
adds response identifiers, not documented membership mutation. The official
schema pinned to October 12 commit `2a973a046607c42742bf141184b71f1990fa1809`
does contain `group_id/group_role` on Order and OrderLeg; an older cached copy
must not be used to claim these fields are absent. Source SHA256, exact pointers
and property inventories are recorded in
`evidence/alpaca-retail-protection-contract-20261016.json` and were reverified
against immutable public bytes. Missing documentation does not prove no
undocumented capability exists; it cannot establish the required guarantee.

Position-close documentation also does not establish atomic coordination with
an existing stop. Cancel-before-close is not an accepted continuity-preserving
substitute. W3-06 remains blocked pending an authoritative supported contract.

### Ready-to-submit support request (not sent)

Official [support request](https://alpaca.markets/support/request) links to the
contact form. No verified support email or broker reply is claimed. This draft
contains no credentials, account identifiers or private account observations.

**Subject: Retail Trading API: partial-fill stop coverage and atomic exit handoff**

Please clarify the currently supported US retail Trading API contract, separately
for paper and live:

1. A LIMIT BUY for 100 shares has filled 40, with 60 still working. Your wash-trade
   table says an independent STOP SELL is rejected while a LIMIT BUY is open.
   Is there a supported server-side mechanism that activates protection for each
   partial fill? Please provide endpoint, payload and event/quantity guarantees.
2. After entry is terminal, an accepted standalone STOP SELL protects the actual
   long quantity. Can a take-profit be added while retaining the existing stop,
   without a cancel gap, duplicate reservation or excess-exit race? Is there an
   atomic adoption/conversion operation? Please specify request fields, partial
   fill/replace behavior and rejection recovery. Response group identifiers alone
   do not establish such an operation.
3. At session close, is there a supported atomic close operation coordinated with
   that working stop, including stop-fill races and preventing a reverse position?
   Does `position_intent=sell_to_close` enforce this server-side, or only classify
   the request?
4. If any guarantee is unsupported, please explicitly identify it and the supported
   alternative. We are not requesting a wash-protection bypass or guaranteed stop
   execution price. Please link current authoritative documentation or a versioned
   technical contract.

Sending the form requires separate user authorization and contact information;
none has been submitted. An answer must be reviewed before implementation or
acceptance. Neither this packet nor the reported-input pass enables orders.

## Bounded protection experiment plan — 2026-10-16

The user requested completion of the documentation/test route. Preparation is
authorized; this plan does not enable unattended execution or waive the existing
no-broker-orders boundary. Use the existing W3 engineering components, not a
second strategy runner. No actual experiment below has been executed.

| Experiment | Deterministic/offline evidence | Additional isolated paper evidence | Acceptance limit |
| --- | --- | --- | --- |
| Working entry and stop rejection | Replay documented rejection, partial fill and uncertain response; preserve actual owned quantity | Observe a naturally occurring partial fill, then a bounded stop request only under an approved experiment | A fully filled or unfilled entry does not test partial-fill behavior |
| Bracket activation | Replay entry partial/full, exit activation and late events | Capture all parent/child snapshots and updates through entry completion | An accepted bracket is not proof that partial fills were protected |
| Existing-stop handoff | Test unknown response, conflicting reservations and forbidden cancel-before-replace | Only a documented supported operation may be attempted; do not guess a conversion payload | Repeated success does not establish atomicity; unsupported payloads are not a safe search method |
| Cancel/fill and close race | Inject both event orders, disconnect, restart, duplicate updates and both exits filling | Observe supported lifecycle transitions without intentionally creating competing exits | Paper does not prove live race frequency or exchange execution guarantees |

### Concrete execution envelope for review

- Paper endpoint only; dedicated experiment journal outside the formal sample.
  Do not reuse or reset the USD 500 evaluation account for experimental fills
  without an explicit account-scope decision. Preserve all existing evidence.
- One whole share maximum, one symbol and one scenario at a time, no shorting,
  borrowing, extended hours or automatic retries. A USD 25 entry-notional ceiling
  is a proposed engineering bound, not a strategy rule. If no suitable eligible
  symbol fits it, skip; never increase the limit automatically.
- Check broker calendar/clock, asset eligibility, account identity and current
  empty owned inventory before entry. Record intent durably before the only send
  attempt; subscribe before sending and retain raw HTTP/stream observations.
- A response timeout means unknown: reconcile by client ID, never resubmit.
  An unobserved natural partial fill is `NOT_OBSERVED`, not a passed scenario.
- The current harness is **not ready for dispatch**: approved exit/protective
  cleanup and crash recovery must cover every scenario first. Do not deliberately
  create an unprotected partial position merely to investigate protection.
  Never cancel a valid stop to make a new exit request acceptable.
- Before any actual order, freeze the selected account, symbol, price/quantity,
  time limits, allowed requests and supported cleanup in a reviewable manifest.
  Missing support for safe cleanup blocks that scenario; a timeout cannot be
  converted into an automatic market liquidation policy.

### Completion criteria and honest outcomes

The offline fault suite may pass independently. A paper scenario needs source
requests, acknowledgments, all cumulative fills, exit states, restart replay and
final broker/local reconciliation in isolated evidence. Residual orders or
inventory are unresolved, never silently erased. Public reports expose only
redacted summaries. Each finding is labeled documented, observed, not observed
or unresolved; paper observations cannot be promoted into undocumented broker
guarantees. W3 acceptance and the user's continuous-protection requirement are
unchanged. Vendor clarification is necessary only for guarantees still missing
after the supported public contract and bounded observations are evaluated.

### Current W3-06/W3-08 evidence binding

The related incident, private journal, entry cancellation, historical ownership
reader and joined risk-input regression passed **121 tests** on the current
worktree. `evidence/w3-engineering-fixture-binding-20261016.json` binds nine
existing test functions, their source hashes and all six mandatory fault groups;
the protocol now references this draft binding. Functions were resolved using
the Python AST before recording references. The binding explicitly excludes
actual broker acceptance, release freeze and runtime authority. It does not
freeze missing reviewer identities or substitute synthetic policy flags for
W3-02/W3-06 acceptance. W3-07 still has no actual order-cycle evidence. The
protection contract, safe dispatch/cleanup implementation and full prerequisite
acceptance remain open; none is replaced with a weaker policy.

## Isolated engineering recovery program — 2026-10-16

Implemented `src/alpaca_ross_bot/paper_engineering.py` and
`scripts/run_paper_engineering.py`, using the existing paper HTTP transport,
private Journal and lifecycle REST recovery. This is an executable recovery
component, not an enabled entry/protection/close runner. No actual API call was
made while implementing it, and no existing account or runtime evidence changed.

The experiment scope durably binds experiment identity, engineering account,
declared formal account and formal storage root. Engineering/formal roots must
not overlap, including resolved aliases. Database/WAL/SHM symlinks, hardlinks
and non-regular files are rejected before opening and before later operations.
Existing unscoped evidence cannot be adopted; a different scope cannot relabel
the same journal. Scope evidence and its cached digest must agree on restart.
Private permissions and source hashes remain enforced by the existing Journal.

GET-only recovery authenticates the engineering account, rebuilds lifecycle
state and captures complete REST evidence for persisted intents. A timed-out
send, missing lookup, incomplete capture or stream gap never grants retry or
cleanup authority. Recovery never sends POST/DELETE and does not fabricate a
stream subscription, pre-session baseline or owned-position acceptance. Even a
complete observed empty REST book is not reported as an accepted cleanup.
Formal-account isolation is explicitly unauthenticated in this read-only report:
two supplied references alone do not establish two actual accounts.

The separate explicit `cancel_entry_once` method first authenticates the formal
account into an independent protected proof journal, then authenticates the
engineering account. It delegates only to the existing owned-buy-entry cancel
transport, retaining the durable one-attempt marker and all raw response evidence.
The method is never called at startup or recovery and has no CLI switch. Unknown
DELETE outcomes cannot be repeated after restart. Protective sell cancellation,
automatic liquidation, stop-to-OCO conversion and new entry dispatch remain
unsupported. They cannot be enabled merely by an experiment scope file. A
cancel acknowledgment is not terminal cancellation, flatness or cleanup success.

### Operator entry point and boundaries

Without `--recover-readonly`, the CLI returns `NOT_RUN` without reading scope,
credentials or creating state. With the explicit flag it requires a private scope
file with exactly `experiment_id`, `account_ref`, `formal_account_ref` and
`formal_runtime_root`, plus an independently selected runtime root and credential
file. References must come from actual authenticated account evidence; do not
send credentials or private scope values in chat. Only the engineering account's
credentials are needed for this GET-only entry point. Account and raw HTTP
evidence remain in protected local storage; stdout contains redacted counts,
status and difference codes, never raw exceptions/account values.

```text
rtk proxy .venv/bin/python scripts/run_paper_engineering.py --scope-file <private-scope-json> --runtime-root <isolated-engineering-root> --credentials-file <engineering-credentials-json>
```

The command above is deliberately a no-op. Add `--recover-readonly` only for an
authorized GET capture. There is no command here for a new entry, protective exit,
automatic cleanup, formal paper or live trading. The prior USD 500 formal account
must not be reused as the engineering account. No new test account was created.

### Verification and remaining acceptance

Independent review found two material issues during development: a hardlinked
database could alias formal storage, and a failed positions capture could be
misreported as empty. Both were reproduced and fixed, with regression tests.
The final independent harness/cancellation review passed **47 tests**, with no
remaining material findings within this scope. Coordinator full offline suite
passed **1,295 tests**, with one existing upstream websockets deprecation. Ruff
check and format (113 files) passed; strict mypy passed 115 files, including the
new CLI. Tests cover account disguise, directory/symlink/hardlink isolation,
scope tampering, restart identity, persistence failure, unknown send recovery,
unknown cancellation without replay, incomplete capture and default CLI no-op.

```text
rtk proxy env PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider --basetemp /tmp/alpaca-engineering-full-final tests
rtk proxy .venv/bin/ruff check src tests scripts/run_paper_engineering.py scripts/qualify_risk_inputs.py
rtk proxy .venv/bin/ruff format --check src tests scripts/run_paper_engineering.py scripts/qualify_risk_inputs.py
rtk proxy .venv/bin/mypy --strict --cache-dir /tmp/alpaca-engineering-current-mypy src tests scripts/run_paper_engineering.py scripts/qualify_risk_inputs.py scripts/reconcile_paper_orders.py scripts/configure_paper_credentials.py
```

The sandboxed related async suite encountered the already documented executor
shutdown limitation and was interrupted; full final validation used the approved
execution environment. No application workaround or global change was made.

This completes the verified isolated recovery and explicit owned-entry-cancel
component. It does not complete the requested full safe order-cycle harness:
supported initial protection, continuous handoff and accepted safe position-close
semantics are still missing. Those missing paths remain non-dispatchable rather
than being replaced with cancel-before-close or guessed exits. W3-07 therefore
remains open pending W3-02/W3-06, supported cleanup and actual API-cycle evidence.

## Source-bound risk inputs connected to incidents — 2026-10-18

`IncidentJournal.observe_risk()` now accepts immutable capture/calendar/quote
references rather than qualification booleans. Under the existing private write
transaction, `incident_risk.py` derives the session open from captured official
calendar evidence, verifies reported funds, source-owned inventory and bid marks,
and reads the point-in-time durable daily-risk prefix. Its assessment, source
hashes and selected request are recorded with the incident event. Historical
rebuild derives the assessment again; a mismatched cache, source or deduplication
record fails closed. No broker/account access or runtime change was performed.

Verified ownership is compared with both the incident's expected inventory and
its supplied observed position. A caller's flat/reconciled/data flags cannot
override a disagreement. Missing, stale or invalid risk sources retain a veto;
they do not discard existing protective-order identities or known fills. A new
ordinary observation or restart requires risk refresh, and cannot erase bound
risk gates. Daily stops and evidence-identity conflicts remain sticky even when
risk evidence arrives before the first order observation. Incident schema 1
history remains readable; schema 2 adds the bound-risk projection only when used.

`RiskJournal.read_evidence_at()` verifies the current cache/deduplication state,
then reconstructs only the visible immutable prefix, without a resolver call,
restart event or other mutation. It checks UTC, account/version, event metadata,
source availability at the original risk event and external clock conflicts.
Later daily-state events cannot retrospectively change an earlier observation.

The existing daily-risk journal still uses an **OFFLINE_FIXTURE_ONLY** input seam.
Its observed stop/veto may conservatively stop the incident, but it cannot certify
actual account P&L, complete fees, reservations or operational risk eligibility.
Missing or stale daily state remains explicit. Accounting, executable data,
runtime risk-owner and protection acceptance remain separate blocked gates;
neither caller flags nor positive reported-input qualification clears them.
All new paths preserve `runtime_authorized=false` and have no dispatch operation.
This is the requested risk-to-incident connection, not full W3-02/W3-06 acceptance.

### Replay correction and verification

Independent review reproduced a material defect: reading today's complete intent
list while rebuilding yesterday's bound assessment could make a legitimate new
intent invalidate history. The writer now preserves the sorted intent census
and hashes of immutable intent contents under the same write lock. Historical
assessment validates that preserved context through a private observer seam;
ordinary/current qualification still reads all current intents. The regression
proves that a later intent leaves old reconstruction unchanged and causes a
fresh assessment to veto its unresolved ownership. `observe_risk()` exposes no
caller override for this census. No historical intent-creation time is invented.

Final independent review passed **76 related tests**, with no remaining material
findings within this source-binding scope. Coordinator related integration passed
134 tests before the census regression; final full regression passed **1,320
tests**, with one existing upstream websockets deprecation. Ruff check, format
(116 files), strict mypy (118 files) and diff checks passed. Full async tests used
the previously approved execution environment; no global or application workaround
was added. Relevant checks:

```text
rtk proxy env PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider --basetemp /tmp/alpaca-incident-risk-full tests
rtk proxy .venv/bin/ruff check src tests scripts/run_paper_engineering.py scripts/qualify_risk_inputs.py
rtk proxy .venv/bin/ruff format --check src tests scripts/run_paper_engineering.py scripts/qualify_risk_inputs.py
rtk proxy .venv/bin/mypy --strict --cache-dir /tmp/alpaca-incident-risk-final-mypy src tests scripts/run_paper_engineering.py scripts/qualify_risk_inputs.py scripts/reconcile_paper_orders.py scripts/configure_paper_credentials.py
```

Tests also cover source-owned nonflat bid qualification, inventory mismatch,
missing bid/source vetoes, daily fixture-stop propagation and restart persistence,
pre-observation identity conflicts, failed persistence, cache tampering, existing
protection preservation and historical daily-read corruption/availability cases.
No formal paper sample started, and W3-06/W3-07/W3-08 remain unaccepted overall.

## Owned nonflat equity conservation — 2026-10-18

`owned_equity_ledger.py` now performs a source-bound conservation audit from a
genuinely frozen flat E0. It joins captured account/activity pages, independently
replayed order ownership and current owned IEX bid evidence. It checks every
new execution against the lifecycle's activity census by ID, order, symbol,
side, exact execution quantity, price and timestamp. Cumulative execution totals
must match owned-order filled quantities; net quantities must match the observed
owned inventory. Missing, foreign or disagreeing executions prevent acceptance.

In the supported no-new-NTA case, exact reported cash must equal baseline cash
plus gross sell notionals minus gross buy notionals. Observed equity at bid is
reported cash plus source-owned quantity times bid; the gross change is measured
against E0. Modelled paper regulatory costs are deducted separately using the
reviewed cumulative fee delta, with its source-document reference retained.
Provider position `market_value`, `current_price`, cumulative execution `cum_qty`
and broker unrealized P&L do not supply this calculation. Unknown new cashflows,
fees or corporate-action activities are rejected instead of being assumed zero
or silently combined with modelled fees. Raw evidence is preserved privately.

Sources rechecked 2026-10-18: [execution fields and nontrade activities](https://docs.alpaca.markets/us/docs/account-activities),
[account equity structure](https://docs.alpaca.markets/us/docs/account-plans),
and [paper limitations, including omitted regulatory fees](https://docs.alpaca.markets/us/docs/paper-trading).
The conservation formula is a derived bookkeeping identity conditional on these
observations, not an Alpaca promise of synchronized snapshots, final posting,
settled cash, liquidation proceeds or future funds availability. A temporary
posting mismatch remains a discrepancy, not a known loss or an assumed zero.

Independent GPT-6.1 Sol Low gathered primary evidence. Public-only Cloud critique
was verified as `gemma4:31b` remote inference, with alias metadata pointing to
`https://ollama.com`. Its obsolete settlement-cycle statement was discarded;
neither settlement nor cash-posting timing is inferred from that model response.
No local source, private account evidence or credentials were sent to Cloud.

Independent component review passed **11 tests**, with no remaining material
findings in this bounded audit. Full offline regression passed **1,331 tests**,
with one existing upstream websockets deprecation. Ruff check, format (118 files),
strict mypy (120 files) and diff checks passed. Tests include nonflat ownership,
cash mismatch, missing/foreign fills, execution-price disagreements, cumulative
versus execution quantity, new NTA, missing genuine E0, stale bids, redaction and
low Decimal precision. The deliberately wrong provider position mark is ignored.

```text
rtk proxy env PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider --basetemp /tmp/alpaca-owned-equity-full tests
rtk proxy .venv/bin/ruff check src tests scripts/run_paper_engineering.py scripts/qualify_risk_inputs.py
rtk proxy .venv/bin/ruff format --check src tests scripts/run_paper_engineering.py scripts/qualify_risk_inputs.py
rtk proxy .venv/bin/mypy --strict --cache-dir /tmp/alpaca-owned-equity-final2-mypy src tests scripts/run_paper_engineering.py scripts/qualify_risk_inputs.py scripts/reconcile_paper_orders.py scripts/configure_paper_credentials.py
```

This is an additional implemented dependency toward the actual risk resolver.
It is not attached as an authorizing daily owner, reservation allocator or release
verifier. `runtime_authorized` remains false. Actual E0, complete risk reservation
and roundtrip ownership, accepted operational inputs and continuous protection
still require their own evidence. No actual account access, broker order or formal
paper evaluation was performed, and no W3 parent was checked complete.

## Source-bound daily-risk and reservation owner — 2026-10-18

`account_risk_owner.py` replaces the injected resolver **for its separately
selected source-bound path**. It derives E0, exact modelled net P, reported
available funds, owned inventory and bid marks from the retained calendar,
account, execution, lifecycle, baseline-receipt and reviewed fee evidence. Its
atomic private event, deduplication and state projection use a separate journal
namespace; legacy `RiskJournal` events remain `OFFLINE_FIXTURE_ONLY`. No migration
or reinterpretation of old fixture state is performed. Model output, caller
qualification flags and a target USD 500 balance cannot supply these facts.

For the existing hybrid research risk profile P09/P10/P12, a declared cent-grid
long entry plan supplies L/S, not a caller-chosen quantity. The owner computes the
largest positive whole-share quantity satisfying both reported funds with entry
fee bounds and the lesser of 0.5% E0 per trade and remaining 1% E0 daily allowance.
These are the preserved hybrid research rules, not newly attributed Ross rules.
Pending entry risk and source-owned open-position stop risk are charged
separately, including future fee bounds. A broker-represented pending buy hold is
not subtracted twice; remaining local entry-fee liability is retained. One
durable slot prevents concurrent allocations across independent connections.

Entry and explicitly bound immutable sell intents must agree with the frozen
plan, source account, symbol, side, type, quantity and stop level as applicable.
New same-session fills not attributable to a held or completed plan veto the
owner even when the account is flat. A terminal, source-reconciled flat book
completes one exact roundtrip; repeated observations cannot count a loss twice.
Three losing roundtrips or reaching the daily loss threshold latches the session
stop, including after mark recovery and restart. Pending cancellation or unknown
submission does not release a slot. An unsent plan needs explicit abandonment,
fresh flat proof, no send marker and no declared exit; its identity remains used.
These declarations establish accounting ownership, not broker protection or
permission to send an entry, stop, target or close order.

### Historical evidence and next-session continuity

Each owner event preserves its locked intent-content hashes and observed send
markers, timestamp and immutable source-sequence horizon. Replay checks that
original context instead of today's expanding intent list. Higher-sequence
lifecycle, quote, metadata, baseline or owner events sharing the same timestamp
are excluded; current cache/source integrity is still verified independently.
An exact caller-command retry reuses its original assessment bytes;
a changed command with the same identity latches a conflict. Retrying an old
positive proposal after release, another allocation or a current veto returns
zero; it cannot recreate the slot or return a foreign plan's feasible quantity.
Commit failure rolls back evidence, dedupe and projection together.

`risk_baseline.py` now permits a next-day pre-session E0 with retained historical
intents only after fresh complete lifecycle evidence proves every retained order
terminal, inventory flat and all current intent/HTTP inputs reconciled. The
baseline preserves immutable intent and source hashes; it does not delete history
or reset unknown orders. A historical cutoff treats a later candidate or later
receipt as unavailable, including a crash between candidate and receipt. Default
current-baseline reads still reject a candidate lacking its durability receipt.

`owned_equity_ledger.py` verifies the **lifetime known-owned** execution census
against lifecycle cumulative fills, while only new rows since the flat E0 affect
current-session cash, quantities and modelled P. Retained prior-day fills cannot
inflate today's inventory or be mistaken for an unexplained current execution.
Unknown new activities and incomplete, changed or conflicting evidence still
veto. The tests exercise an earlier flat roundtrip followed by a source-proven
next-day E0 and new partially filled entry.

### Incident binding and verification boundary

`IncidentJournal.observe_risk()` can explicitly select this actual owner using
the reviewed fee-policy path. Its immutable observation records the selection,
replays the source-owner prefix, propagates daily stops and compares observed
inventory with incident expectations. An exit-binding event cannot refresh the
underlying account observation's age. Freshness uses the oldest validated raw
account HTTP request start, not the later evaluation clock. Accounting clears
only when the owner and current incident select the same account/calendar/mark
evidence and current stricter freshness checks pass. A newer incident capture
requires a matching owner refresh; stale or different-source accounting stays
unqualified. Missing source-owner state does not fall
back to fixture qualification. Schema-1/2 historical incident data and the
default fixture path remain readable. Runtime release, executable-data and
protection gates remain closed even when reported accounting is verified.

Independent review found and verified corrections for unbound executions while
flat, retry identity after later intents, and future unfinished E0 changing a
past missing-E0 assessment. The final bounded independent combination passed
97 tests plus direct historical/retry reproductions, with no remaining material
findings in that scope. New source-owner integration has 20 tests, including two
private connections, partial fills, pending cancel, unknown acknowledgement,
cache/dedupe corruption, stale/foreign sources, post-append commit failure,
completed roundtrips and both daily-stop rules. Six further tests cover actual
owner-to-incident propagation, source freshness, missing-owner refusal and both
pending/completed future E0 historical cutoffs.

A final review additionally reproduced raw-source age being refreshed by
evaluation time and later same-clock source records changing history. Both were
corrected as described above. Five dedicated regressions cover strict source
age, same-clock lifecycle/quote/owner changes and both pending/completed E0 ties.
Next-session baseline tests also preserve their original source-sequence proof
after later same-clock lifecycle changes. The final independent scoped review
passed **113 tests**, including all five new horizon regressions, with no
remaining material findings in source binding, replay or incident propagation.

Final full offline regression passed **1,389 tests**, with one existing upstream
websockets deprecation. Ruff check, format (124 files), strict mypy (126 files) and
diff checks passed. Full async tests used the previously validated execution
environment; no global or application workaround was added.

```text
rtk proxy env PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider --basetemp /tmp/alpaca-account-risk-horizon-full tests
rtk proxy .venv/bin/ruff check src tests scripts/run_paper_engineering.py scripts/qualify_risk_inputs.py
rtk proxy .venv/bin/ruff format --check src tests scripts/run_paper_engineering.py scripts/qualify_risk_inputs.py
rtk proxy .venv/bin/mypy --strict --cache-dir /tmp/alpaca-account-risk-horizon-mypy src tests scripts/run_paper_engineering.py scripts/qualify_risk_inputs.py scripts/reconcile_paper_orders.py scripts/configure_paper_credentials.py
```

A public-only generic failure-mode critique returned verified remote
`gemma4:31b` inference on 2026-10-19 UTC (2026-10-18 Los Angeles). No local source,
private account evidence, credentials or risk parameters were sent to Cloud;
only deterministically verified implementation was adopted.

This closes the **source-bound owner implementation increment**, not a trading
release or W3 parent acceptance. Real pre-session E0 and repeated operational
source evidence, accepted executable strategy/data qualification, protection
continuity and actual broker API cycles remain unaccepted. The implemented
quantity is a non-dispatch risk proposal with `strategy_plan_qualified=false`
and `runtime_authorized=false`. It neither proves settled/available future funds
nor guarantees final/atomic broker snapshots, stop fills, slippage or protection.
The published partial-fill protection conflict and stop-to-OCO handoff are still
unresolved. No actual account access, order, cancellation, data purchase, formal
paper start or W3 completion checkbox occurred in this increment.

## Capital review state and October 19 open preparation — 2026-10-18

The user requested completion of W3 and advance preparation for tomorrow's
real-time tests. This increment implements the remaining independently safe
capital-review component and connects actual-source risk observations to the
finite read-only collector. It does not redefine the W3 acceptance criteria.

### Capital-stage component

`capital_stage.py` derives fresh reported USD equity, multiplier and buying power
from immutable account/calendar evidence and separately reads the qualified
session E0. The private journal owns atomic capital observations and their
source-sequence horizon, independent account/strategy/risk/broker version fields,
idempotent retry identity, cache/source reconstruction and downgrade history.
Concurrent retries cannot duplicate the observation; commit failure cannot
publish a projection or consume its identity.

Current reported equity reaching USD 2,000 records only the user's authorized
research-review threshold. E0 crossing that threshold is a separate observed
fact, not an extra invented policy requirement. Reported multiplier and buying
power do not establish borrowing or instrument entitlement. Blocked/invalid
account flags produce an unqualified stage. Withdrawal of the observed review
threshold is durable; it cannot force liquidation or silently change strategy,
risk, leverage or broker configuration. Higher stages remain
`BLOCKED_PENDING_SEPARATE_ACCEPTANCE`. Operational tranche activation,
hysteresis, a sourced leveraged Ross version and separate leveraged-paper
evidence are still unspecified/unaccepted; no defaults are invented to fill
those gaps. W3-03's original W3-02 dependency therefore remains open.

### Read-only collector and timed boundaries

`scripts/qualify_risk_inputs.py` now has an explicit observation option that
calls `AccountRiskOwner.observe()` and `CapitalStageOwner.assess()` with the
fresh capture/calendar/owned-mark references. It does not call `reserve()` or
any order/cancel API. The summary includes redacted source-owner/capital stage,
current-session baseline presence, veto codes, rebuild and zero-intent status;
account identities, money, credentials and raw responses remain private.

The dedicated-account mode requires an existing journal and authenticated
binding; an account change refuses subsequent capture and preserves old history.
Date/phase guards run before credential/network use, after fresh calendar
validation, before freezing E0, before metadata requests and risk/capital
assessment, and before success is returned. An in-flight request may complete
after a boundary, but a late phase cannot be accepted as successful. Interrupted
evidence remains in the journal. Open collection cannot request baseline freeze.
The reported E0 must belong to the latest snapshot's session and receipt, not a
previous session's retained owner state.

### Actual preflight and native scheduler registration

A bounded **GET-only preflight** at 2026-10-19 01:21:45 UTC (October 18 in Los
Angeles) verified the dedicated credentials still match the existing private
journal's account binding, private permissions and SQLite integrity, with zero
local order intents and zero send markers. No account state was changed. Fresh
Alpaca calendar response for October 19 reported **09:30–16:00 New York**, or
**06:30–13:00 Los Angeles**. Calendar response SHA-256:
`b3c9c1464db3966cf10f7400ccb14f357384c063cb48f9d419a9f70911718fad`.
This preflight is connectivity/calendar preparation, not a durable pre-session
E0 or complete source/risk acceptance. Its redacted report is retained locally
at `/tmp/alpaca-open-20261019-preflight.json`; no private account body is copied
into Git.

The existing native W3 heartbeat `alpaca-bot-w3-strategy-risk-execution` was
updated, not duplicated, in the same persistent W3 chat. Active status, target
chat, read-only instructions and recurrence were read back from the saved
automation. RFC recurrence expansion independently confirmed exactly:

| Los Angeles, October 19 | UTC | Planned observation |
| --- | --- | --- |
| 06:20 | 13:20 | Pre-open GET/calendar, genuine E0 plus durability receipt, short IEX observation and risk/capital state |
| 06:30 | 13:30 | Finite open IEX/account sample plus read-only SDK/REST lifecycle diagnostic |
| 06:35 | 13:35 | Second finite sample/reconciliation; end the one-day heartbeat |

The pre-open window is 06:15 inclusive to 06:25 exclusive; the open window is
06:30 inclusive to 06:40 exclusive. Missing the first cannot be backfilled by
the later phases. Each uses the explicit existing dedicated credentials/root,
retains separate redacted output, leaves all strategy/protection/release gates
closed, and may report failed qualification without pretending it passed.
Earlier phases retain the task; the final phase or expiry deletes it. Native
scheduler registration is verified, but actual delivery and tomorrow's
observations are not yet verified. Local execution requires the computer
powered on and desktop app running, per [official OpenAI documentation](https://learn.chatgpt.com/docs/automations?surface=app).

### Remaining W3 acceptance

| Item | What remains open |
| --- | --- |
| W3-01 | Accepted executable candidate and source-scoped scanner/news/trigger data gates; diagnostic SPY is not a small-cap candidate census |
| W3-02 | Genuine E0 and accepted operational data/strategy/risk/protection inputs; a source-bound observation is not a release |
| W3-03 | W3-02 dependency and any separately accepted operational higher-stage transition; research component is implemented |
| W3-04/05 | Previously accepted engineering components; no new strategy authority |
| W3-06 | Continuous protection of partial entry fills and protection-preserving stop/target/close handoff |
| W3-07 | Actual isolated order cycles and protected cleanup after their execution gates pass |
| W3-08 | Complete accepted release/version/data/incident evidence and final protocol freeze |

Primary Alpaca documentation rechecked October 18 still lists existing limit
buy plus new stop sell as rejected and applies wash-trade protection to paper:
[user protection](https://docs.alpaca.markets/us/docs/user-protection).
[Bracket documentation](https://docs.alpaca.markets/us/docs/orders-at-alpaca)
activates exits only after the entry completely fills. Applying the first rule
to a working remainder after a partial fill is an explicit inference; no actual
order was submitted to test it. An authoritative atomic standalone-stop-to-OCO
conversion contract remains absent from the reviewed interface evidence, not
proved impossible by an absence search. The user's continuous-protection
requirement is preserved; tests cannot waive it.

Public-only Cloud critique returned verified `gemma4:31b` remote inference.
Its suggestion that synthetic responses prove real broker behavior was rejected.
Only first-party interface statements and deterministic local evidence inform
the acceptance conclusions. No private code, account state or credentials were
sent to Cloud.

No broker order, cancellation, account reset, data purchase, live enablement or
formal four-week paper start occurred. No W3 parent was checked complete merely
because the date or test count advanced. The scheduled samples are diagnostic
observations, not continuous full-market monitoring, proof of executable
strategy fidelity or actual fill/protection acceptance.

### Final verification

The capital component has 31 deterministic integration tests; the collector has
46, including genuine-source E0/missing-E0 observations, account mismatch with
history preservation, source redaction, no reservation, seven mid-collection
expiry boundaries and an earlier-session E0 retained in owner state but absent
from the current snapshot. Final independent scoped review passed all **77
tests**, with no remaining material findings in this bounded preparation scope.
Final full offline regression passed **1,460 tests**, with one existing upstream
websockets deprecation. Ruff check, format (126 files), strict mypy (128 files)
and diff checks passed. No test result grants broker or formal-paper authority.

```text
rtk proxy env PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider --basetemp /tmp/alpaca-w3-open-ready-full tests
rtk proxy .venv/bin/ruff check src tests scripts/run_paper_engineering.py scripts/qualify_risk_inputs.py
rtk proxy .venv/bin/ruff format --check src tests scripts/run_paper_engineering.py scripts/qualify_risk_inputs.py
rtk proxy .venv/bin/mypy --strict --cache-dir /tmp/alpaca-open-final-mypy src tests scripts/qualify_risk_inputs.py scripts/run_paper_engineering.py scripts/reconcile_paper_orders.py scripts/configure_paper_credentials.py
```


## October 19 pre-open capture — verified 2026-10-19

The scheduled pre-open phase ran in the declared window on the previously
bound dedicated paper account and existing private journal. Collector summary:
`/tmp/alpaca-w3-20261019-preopen-132150.json`. Independent local verification:
`/tmp/alpaca-w3-20261019-preopen-verified-132225.json`. These are redacted reports;
credentials, account identity and raw broker state remain outside Git.

Fresh calendar evidence established the October 19 open at 13:30 UTC (06:30
Los Angeles). The genuine E0 candidate committed at **13:22:10.780376 UTC**;
its durability was observed at **13:22:10.787980 UTC** and receipt qualified at
**13:22:10.788014 UTC**, all before the official open. The original account and
calendar capture sequences are 1317 and 1316; receipt sequence is **1319**,
with payload hash
`sha256:e26f27e466b05c8414e6ad76282cda4eb4d647e32e72ff708cabe1bfedcf5c0b`.
Source reconstruction verified the candidate, receipt, decision-time availability
and source hashes. No intraday or synthetic E0 was substituted.

The source-bound risk snapshot was qualified for its reported-observation
profile, with no owner issue codes, no reservation and runtime authorization
false. Capital research state remains no-borrowing research with higher-stage
acceptance blocked. Both private projections rebuilt successfully. Verification
checked SQLite integrity and all **1,331** current event payloads; local order
intent, send and cancellation-attempt counts were all zero. No broker order,
cancellation, account reset or formal paper start occurred.

The legacy flat-account availability report remains `UNQUALIFIED` and settlement
unknown; the ledger observation still lists non-atomic/finality/operational
qualification limitations. Its pagination exhausted two activity pages, with
zero new fills and matching observed cash. These narrower reported-account
observations do not close operational funds, executable strategy/data or
protection/release acceptance.

IEX SPY subscription was acknowledged, but the finite pre-open capture observed
**zero market records**. Live record freshness/flow therefore was not verified,
and closing the bounded stream leaves continuity recovery required. Preserve
`NOT_SIP_EQUIVALENT`; do not report this as a healthy continuous market feed or
scanner acceptance. The 06:30 and 06:35 Los Angeles read-only phases remain
scheduled. No W3 parent is checked complete by this pre-open result.


## October 19 open observations — verified 2026-10-19

Two finite open samples and their read-only SDK/REST diagnostics completed
inside the declared 06:30–06:40 Los Angeles window. The heartbeat arrived late
for the nominal 06:30 slot: the first sample completed at **06:35:31.003018**,
and the final scheduled phase was then collected sequentially, completing at
**06:36:53.288401**. These actual observation times do not represent a capture
at the exact opening tick or a backfill of the unobserved opening interval.
No concurrent mutation of the private journal was used.

| Redacted local report | Assessed records | Schema-valid | Measured fresh |
| --- | --- | --- | --- |
| `/tmp/alpaca-w3-20261019-open-133450.json` | 4709 | 4709 | 4709 |
| `/tmp/alpaca-w3-20261019-open-final-133605.json` | 3288 | 3288 | 3288 |

These are assessed record counts, not unique trade counts or all-market
coverage. Both finite SPY/IEX subscriptions acknowledged and observed reported
regular quotes and trade conditions. Both source-bound risk snapshots were
qualified for their reported-observation profile, had a current session E0,
no issue codes and no reservation; risk/capital projections rebuilt. Provider
clock origin, strategy trigger policy and runtime eligibility remain unaccepted.
The earlier zero-record pre-open observation is retained unchanged; these later
samples establish observed open-session flow, not its pre-open root cause.

Read-only SDK subscription and REST reconciliation completed at
**06:35:53.872278** and **06:37:19.320496** Los Angeles. Both reports were
`MATCHED`, with empty difference-code lists, verified account/subscription and
successful lifecycle reconstruction. Reports are
`/tmp/alpaca-w3-20261019-reconcile-133535.json` and
`/tmp/alpaca-w3-20261019-reconcile-final-133700.json`.
The initial verified empty lifecycle baseline was registered only if absent;
existing history and the original E0 were preserved. These captures are not
atomic, and intentional stream closure requires recovery before another
operational observation. They do not establish continuous monitoring.

Independent final verification at 13:37:50 UTC checked private SQLite integrity,
all **4,858** current immutable source payloads and successful risk, capital and
lifecycle rebuilds. The original E0 receipt sequence **1319** and its previously
recorded digest were unchanged. Local intent/send/cancellation-attempt counts
were all zero. Consolidated redacted evidence is
`/tmp/alpaca-w3-20261019-open-final-verification.json`. No broker order,
cancellation, reset, data purchase, leverage/version activation or formal paper
start occurred. Preserve `NOT_SIP_EQUIVALENT` and runtime authorization false.

After both open phases finished, the native one-day W3 heartbeat
`alpaca-bot-w3-strategy-risk-execution` was deleted through the app tool, which
confirmed deletion. It was not converted into an ongoing trading task. The
planned observation sequence is complete; actual delivery differed from its
nominal schedule as noted above. W3-01/02/03 operational dependencies,
continuous protection/stop-to-OCO semantics, actual order cycles and final
paper/release freeze remain open; no parent completion checkbox changed.

## Research entry-to-risk bridge — 2026-10-19

`AccountRiskOwner.reserve_research_decision` now derives the declared pending
research signal's exact limit and cent-floored stop, rather than accepting
caller-selected prices. The declaration and risk reservation share an atomic
record; replay verifies derived prices, session identity and version metadata.
Generic reservation compatibility and old-client-ID retries remain guarded.
Capital assessment in fixtures checks the same account/calendar and independent
versions; capital and risk are not claimed to share one transaction.

Classification remains `RESEARCH_DECLARED_SIGNAL_NOT_SOURCE_ADMISSION`.
`strategy_plan_qualified=false` and `runtime_authorized=false`; declarations do
not prove raw bar provenance, eligible conditions, complete session history or
live data admission. No order intent or entry-attempt consumption is performed.
All new reservation exercises used synthetic temporary fixtures, not the user's
account. No broker requests, writes or formal paper start occurred this increment.

Validation: full offline pytest 1484 passed (one existing websockets deprecation
warning); independent bounded bridge review 157 passed, no material findings;
Ruff check and format (128 files) passed; strict mypy passed 130 source files.
Commands used the project virtual environment, `PYTHONDONTWRITEBYTECODE=1`,
pytest `-p no:cacheprovider`, and temporary test/type-cache directories.

Primary-source recheck of Alpaca stock snapshots, real-time stock data and pinned
alpaca-py v0.44.0 Bar models found no verifiable cumulative-volume update
watermark. Bar interval start, latest trade time and local response receipt do
not establish that watermark. This is an unresolved proof requirement, not a
claim that observed volume was actually stale. See the source links and
explicit alternative in [the independent proposal](w3-data-admission-proposal.md).
The proposal is NOT ACCEPTED: the user asked for a recommendation, not adoption.
The original frozen specification is unchanged. Optional non-veto news stays
optional; source-scoped history/conditions, actual input admission, protection
and release gates remain separate. W3-01/02/03 are not complete.

## Independent observation-age contract — user acceptance 2026-10-19

After the recommendation, the user explicitly selected “接受，建立独立版本并验证”.
This accepts the tradeoff in `docs/w3-data-admission-proposal.md`, not an actual
source-admission or trading release. The original research artifact, policy
section and scanner pins remain unchanged and their existing loaders pass.

The independent data profile is `IEX_SNAPSHOT_OBSERVATION_AGE_RESEARCH_V1`, linked
to research candidate `HYBRID_500_IEX_OBSERVATION_AGE_RESEARCH_V1`. It retains the
parent strategy/screen definitions and all separate condition, history,
continuity, account/risk, protection, broker-cycle and release requirements.
The existing entry/risk bridge remains bound to the original research identity;
this new data component does not silently relabel that bridge as the new version.

The new metric is at most 300 seconds since the earliest raw request start used
by the assessment, including the catalog when discovery uses it. Each page keeps
its own request time. A sweep completion, derived processing delay or cache copy
cannot refresh acquisition. Genuine new responses with unchanged content are
allowed as new observations; source-volume update time and age remain UNKNOWN.
The raw capturer remains a trust boundary, not an independently attested network
exchange. All outputs remain observation-component-only, NOT_SIP_EQUIVALENT and
non-authorizing.

Implementation: `src/alpaca_ross_bot/observation_admission.py`,
`tests/test_observation_admission.py`, and pinned
`evidence/iex-snapshot-observation-age-research-v1.json` (SHA-256
`f5439d49ac9354abbe56b7b1fcb603c915942dee647efedebf48060e63b49e8c`).
It reuses the original stricter raw-capture/discovery audit rather than relaxing
or duplicating those source contracts. The page API cannot rebuild a pool; the
completed-sweep API checks coverage identity and each returned member separately.
Missing responses remain separately reported. Current NY-day daily volume is
parsed from raw bytes, with booleans, negative volume and wrong-day buckets
refused. All used sweep inputs are retained as sequence/hash references.

Review found and corrected a future-horizon acceptance gap and added strict
raw-before-derived sequence ordering before final verification. Evidence bounds
cannot exceed the journal's actual captured horizon. No source changes followed
the full regression; only explicit non-None assertions in tests were needed for
strict typing, and the 17 affected tests were rerun afterward.

Validation (project venv, temporary caches, no broker/private-account access):

- `python -m pytest -q -p no:cacheprovider --basetemp /tmp/alpaca-observation-age-full tests`:
  1501 passed, one existing websockets legacy deprecation warning.
- Final `tests/test_observation_admission.py`: 17 passed; implementation worker's
  related discovery/snapshot/contract suite: 76 passed.
- Independent source/test review: no material findings; 69 combined tests passed.
- `ruff check` and `ruff format --check` across src/tests and the qualification /
  engineering scripts: pass, 130 files formatted.
- `mypy --strict` across src/tests and the four qualification/engineering/order /
  credential scripts: pass, 132 source files; `git diff --check`: pass.

The earlier NOT ACCEPTED proposal entry is superseded only by this explicit user
decision and bounded component verification. Raw setup/history admission,
condition policy, new-candidate entry/risk identity binding, continuous protection,
actual broker cycles and release remain unaccepted. W3-01/02/03 parents remain
open. No order intent, attempt consumption, broker mutation, data purchase or
formal paper evaluation occurred.

## New candidate binding and actual-source acceptance attempt — 2026-10-20

Scope: the user requested actual market-input qualification, new-version
entry/risk binding and protection acceptance. This increment completes an
explicit research-version bridge and records actual-source checks/refusals;
it does not claim all three operational gates passed.

### Explicit new-candidate research binding

The exact pinned `ObservationContract` is now an explicit opt-in to
`build_trend`, `evaluate_entry` and `reserve_research_decision`. Without it the
original path remains unchanged. New feature/state/signal identities must all
match `HYBRID_500_IEX_OBSERVATION_AGE_RESEARCH_V1`; signal IDs use its namespace.
Mixed versions and relabeling an old state are refused. The declaration persists
the new strategy/data identity and hash separately from the unchanged inherited
risk profile. One canonical account risk owner and slot prevent either version
from allocating an independent second risk budget. Replay checks this mapping.

This is `RESEARCH_DECLARED_SIGNAL_NOT_SOURCE_ADMISSION`, with
`source_admission_qualified=false`, source-volume age UNKNOWN and runtime/order
authority false. No caller-supplied admission boolean or parsed market value is
accepted as evidence of source eligibility. The original attempt owner still
refuses the new candidate; no attempt is consumed and no order intent is created.
Connecting an accepted actual-source decision to a dispatch/attempt owner is
still a separate operational gate.

Validation: 16 new tests and 166 related feature/entry/risk/attempt tests passed.
Independent read-only review found no actionable issues, passed the same 166
tests and seven additional contract field/type refusal checks. Full offline
pytest (`-q -p no:cacheprovider --basetemp /tmp/alpaca-candidate-binding-full tests`)
passed 1517 tests with one existing websockets deprecation warning. Ruff check,
format (131 files) and strict mypy (133 source files, including the four
qualification/engineering/reconciliation/credential scripts) passed.

### Real-source checks and current-session refusal

At **12:31:53.303859 Los Angeles** (19:31:53.303859 UTC), one finite GET-only
explicit SPY snapshot passed the independent observation-age component. The
existing dedicated account/journal binding was authenticated, observed positions
and open orders were empty, and no generic account/root was substituted.
Raw snapshot reference: sequence 4859,
`sha256:cba836dd60a5696a66511d726cbb7cd1a10806c7af37651f3f430db1481a4b52`,
captured horizon 4860. Redacted report:
`/tmp/alpaca-w3-20261020-observation-193153309609.json`; finite driver:
`/tmp/alpaca-w3-20261020-observation-capture.py`. No market/account values or raw
responses were printed. This is a diagnostic symbol, not a qualified small-cap
candidate, completed catalog sweep or proof of a setup from open.

A subsequent existing collector run used `--connect --require-bound-account
--observe-risk-owner --stream-seconds 20`, the same dedicated credentials and
private root, and no baseline freeze or dated open-window claim. It finished at
**12:33:06.483908 Los Angeles** (report filename timestamp). Redacted report:
`/tmp/alpaca-w3-20261020-inputs-193306483908.json`. Subscription was acknowledged;
the 1315 trade/quote records passed schema and measured event/receipt ages.
One additional record was a minute bar, correctly not qualified by the
trade/quote checker. Independent private read-only counts were 1296 quotes,
19 trades and one bar; SQLite integrity passed and intent/send/cancel counts
were zero. Final verification at 19:38:43 UTC confirmed the original E0 receipt
sequence 1319 and its recorded digest unchanged, SQLite integrity OK and zero
intent/send/cancel markers. Redacted proof is
`/tmp/alpaca-w3-20261020-final-integrity-193843471517.json`.
The bounded stream was intentionally closed and requires continuity
recovery; it proves neither monitoring from open nor all-market coverage.

The source-bound risk owner explicitly refused the current session with
`PRESESSION_E0_MISSING`; rebuild verified, reservation=false and runtime=false.
The prior session's E0 was preserved, not relabeled or retroactively frozen.
An intraday observation cannot supply today's missing pre-open evidence.

Current actual admission therefore remains blocked by current-session E0,
decision-visible history/features and first-RTH/setup/LOD reconstruction,
continuous monitoring, and a separately accepted condition policy. The original
collector reports provider-clock qualification false; the new contract only
measures provider-reported event and local-receipt age and makes no additional
exchange-clock SLA claim. Passing those measurements does not accept a trigger
condition policy or establish complete actual-source eligibility.

### Protection recheck: no positive acceptance

Independent GPT-6.1 Sol Low retrieval and coordinator primary-source review on
2026-10-20 rechecked the living US Trading API v2 contracts:

- [Order guide](https://docs.alpaca.markets/us/docs/orders-at-alpaca#bracket-orders):
  bracket exits activate only after full entry fill. Thus brackets do not meet
  the required protection from the first partial fill. OCO exits can both fill
  before cancellation in a fast market; their name is not an absolute guarantee
  against excess exits.
- [Replace order](https://docs.alpaca.markets/us/reference/patchorderbyorderid-1):
  no documented order-class conversion/adoption field; successful HTTP replacement
  is not guaranteed execution-venue replacement. The official SDK replacement
  request also supplies no simple-stop-to-OCO conversion operation.

No supported atomic standalone-stop-to-OCO handoff guarantee was found. This is
not proof of the impossibility of all broker-private capabilities. A client
reaction after receiving a partial fill cannot by itself establish uninterrupted
broker-native protection from that first fill. Bounded experiments can reveal
counterexamples or particular observed sequences, not prove universal atomicity.
No deliberately unprotected position was created to test this known gap.
The existing unsent vendor questions and protection requirement remain valid;
written supported broker semantics are still needed for positive acceptance.

Public-only Cloud inference was verified as `route=cloud`, `model=gemma4:31b`,
success=true at 19:30:49 UTC. It supported the limited testing/contract distinction;
its wording suggesting a single exit filling before cancellation alone falsifies
OCO was rejected (the relevant excess-exit counterexample needs both exits).
No private project/account material was exported.

Independent retrieval also rechecked [condition metadata](https://docs.alpaca.markets/us/reference/stockmetaconditions-1),
[stream schema](https://docs.alpaca.markets/us/docs/real-time-stock-pricing-data)
and [bar aggregation rules](https://docs.alpaca.markets/us/docs/market-data-faq).
Names and aggregation eligibility do not uniquely determine the strategy's
tick-trigger whitelist. Observed Regular Sale, Odd Lot and regular-open quote
definitions were preserved, not silently made executable rules. A public-only
Cloud critique at 19:34:03 UTC was also verified; its unsupported claims about
noise reduction, professional strategies and subtracting excluded volume were
rejected. No new condition policy was adopted or original volume changed.

Outcome: research binding verified; bounded actual observation component verified;
actual strategy/risk-source qualification and continuous protection NOT ACCEPTED.
W3 parents remain open. No broker writes, account reset, data purchase, weakened
protection, live mode or formal paper evaluation occurred.

## W3-06 through W3-08 — candidate incident duties, isolated preflight and draft computation, 2026-10-20

The active goal remains completion of the original W3-06/W3-07/W3-08 acceptance,
including their real dependencies. The preceding goal turn made progress through
the candidate research bridge and real source/refusal evidence. This increment
adds the required candidate-specific incident duties, authenticates isolated
engineering preflight and implements the draft study computation. No W3 parent
is marked complete by those component results.

### W3-06: new-candidate obligations and fault evidence

`IncidentPolicy` now accepts the exact pinned `ObservationContract` explicitly
for the new observation-age candidate. Its durable representation binds the
candidate/data identity separately from the inherited risk profile. Original
policy and projection serialization retain their previous shape. A missing,
forged or mixed contract cannot acquire the new binding; policy migration cannot
relabel an existing incident scope. `IncidentJournal` continues to refuse caller
runtime/protection acceptance flags. The candidate also retains an explicit
source-admission refusal and cannot authorize new exposure.

The new version previously remained `RESEARCH_POLICY_UNBOUND`, which omitted its
P11–P13 target, remainder-cancel and closing obligations. Those obligations now
apply to its own valid research identity while preserving verified stops,
tracking racing fills, retaining daily-stop and reverse-inventory faults, and
requiring reconciliation/approved disposition. It reads the same canonical
parent `AccountRiskOwner`, without a second account risk budget or risk-profile
migration.

Twenty-three candidate tests now cover typed identity/replay, storage rollback,
source-owner binding and all six fault groups. Unknown entry responses preserve
the stop and pending-entry obligations without retry authority. A cancel-pending
entry can still fill beyond its old stop quantity; the extra quantity remains
owned and triggers protection escalation. Restart testing closes and physically
reopens the SQLite connection before checking durable quantities, protection and
cancel obligations. The six groups and exact source bytes are bound in
[candidate fixture evidence](../evidence/w3-candidate-incident-fixtures-20261020.json).
The original nine-function/six-group October 16 binding was separately verified
intact. These are synthetic obligations, not broker-native continuous-protection
acceptance or a release freeze.

### W3-07: independently authenticated isolated preflight

`EngineeringHarness.recover` now optionally takes a second `formal_api` and
authenticates it freshly before the engineering recovery. An old sidecar proof
cannot stand in for the current request. Wrong formal credentials fail before
engineering requests; omitting the optional argument keeps the authentication
claim false. The CLI exposes `--formal-credentials-file` for this read-only path.
Distinct authenticated identities and disjoint configured paths do not qualify
the formal starting ledger, baseline, protection or cleanup.

The actual check used the two existing account credentials and a new private
engineering namespace, `~/.local/state/alpaca-bot/w3-engineering-isolation-20261020`.
The existing dedicated formal journal binding was also checked directly. Both
account histories were preserved; only GET and passive SDK operations occurred.
The first recovery authenticated isolation and captured a complete stable empty
book, while correctly reporting its missing lifecycle baseline/subscription.

The existing finite SDK/REST diagnostic then initialized an independently
verified empty engineering lifecycle baseline only because it was absent. At
**13:29:04.313825 Los Angeles** (20:29:04.313825 UTC), subscription was confirmed
and reconciliation was `MATCHED`, with no differences and successful rebuild.
Intentional shutdown requires recovery; snapshots remain non-atomic and the
observation does not establish continuous monitoring or an executed order cycle.

[Actual redacted preflight evidence](../evidence/w3-engineering-isolation-20261020.json)
binds the reports and runtime source hashes. Postflight verification at
20:37:17 UTC confirmed both private databases' integrity, distinct account/proof
bindings, zero intent/send/cancel markers and the original formal E0 receipt
unchanged. No formal sample or actual order cycle was started. W3-07 still needs
the supported protection/cleanup contract and actual isolated order scenarios.

### W3-08: deterministic draft report computation

`paper_report.py` implements the existing draft's declared-input coverage and
outcome rules without a P&L/returns argument. It distinguishes an unknown daily
candidate census from explicit zero, reconciles census/trace identities, dedupes
globally, checks planned session periods and decision-visible inputs/reviews,
requires distinct natural witnesses for every enabled cell and a natural owned
filled-to-flat lifecycle, and checks six scenario groups and final owned state.
Fixtures and exposed research cases cannot fill natural quotas. Failure takes
precedence over incomplete evidence, and run state distinguishes NOT_STARTED,
IN_PROGRESS, ABORTED and incomplete/completed declared windows.

Independent review found an extra-case gap: once quotas were satisfied, an
additional ENTRY with missing inputs could escape the incompleteness verdict.
The corrected computation audits every natural case, including extra cases;
missing inputs/rule/lifecycle evidence and unreached ENTRY risk decisions block
draft criteria. A filled lifecycle without an ENTRY is a contradiction. The
reproduction now returns INCONCLUSIVE and all 56 focused tests pass. No threshold
or sample minimum was changed in response to paper outcomes.

A positive computation is `DRAFT_CRITERIA_MET`; the calendar, preregistration,
source/reviewer attestations and release freeze stay unverified. It neither
authenticates raw provenance nor proves holdout enrollment/masking, and does not
replace the source-stage census adapter or descriptive fee/P&L ledgers. The
[canonical protocol](w3-paper-protocol.md#draft-computation-implementation--2026-10-20)
records this implementation and the remaining final registration fields.

### Final validation and remaining acceptance

From the repository, using the project venv and temporary test/type caches:

- Full offline pytest: `-q -p no:cacheprovider --basetemp /tmp/alpaca-w30608-final tests`
  — **1598 passed**, one existing websockets legacy deprecation warning.
- Candidate incident tests: 23 passed; isolated engineering tests: 15 passed;
  draft report tests: 56 passed. Independent reviews covered incident source and
  101 related tests at its first freeze, the engineering authentication diff,
  and all 56 corrected report tests. Root reviewed the final two added incident
  cases and physical restart, then verified the full suite.
- Ruff check and format passed (134 files). Strict mypy across src/tests plus
  qualification/engineering/reconciliation/credential scripts passed 136 files.
  `git diff --check` passed.

No affected environment fault occurred. Broker orders/cancellations/resets,
data purchases, source/protection waivers, live enablement and formal paper
evaluation remained absent. The [support questions](#ready-to-submit-support-request-not-sent)
remain prepared and unsent; an explicit external-submission/contact-information choice has been
requested, without assuming authorization.

Completion is still unproven: W3-06 needs accepted actual strategy/risk/data and
broker-supported continuous protection/close semantics; W3-07 needs safe actual
order-cycle evidence after those gates; W3-08 needs the accepted executable
candidate, dependency evidence and fully populated independently reviewed freeze.
The full active goal is preserved, not redefined as the increments above.

## Calendar and descriptive-report increment — 2026-10-20

The authenticated GET-only calendar check at 20:48:37 UTC matched all 20 proposed
October 26–November 20 sessions, including UTC hours across the DST transition.
The existing dedicated account and private journal were used. Raw broker bytes
remain private under source sequence 5392; the [redacted proof](../evidence/w3-paper-calendar-broker-check-20261020.json)
binds the response digest, report and unchanged expected-calendar artifact.
This is an actual as-of calendar observation, not preregistration or launch
approval; pre-launch calendar revalidation is still required. No broker write,
new account/root, current-session E0 backfill or formal paper start occurred.

The separate draft descriptive calculation in `paper_performance.py` preserves
broker-original and modelled regulatory costs, replacing only explicitly
attributed SEC/TAF/CAT charges once. It uses pinned account/day fee rounding,
explicit execution/roundtrip ownership, exact fractional partial fills and
exact flat cash endpoints. Missing censuses, attribution or conservation block
complete P&L claims. Funding requires review, including offsetting transfers;
unknown curve bases cannot become account-equity drawdown. Missing curves stay
unknown; supplied sampled drawdown is not a continuous maximum. All inputs
remain declared/unverified and runtime authority remains false. There is no
P&L-based pass threshold or actual study result.

The requested three acceptance paths remain distinct:

| Path | Verified component | Remaining acceptance evidence |
| --- | --- | --- |
| Actual market qualification | Finite IEX format/age observations and independent acquisition-age contract | Complete candidate/history from open, accepted condition semantics and current-session pre-open E0; no source-volume-age or SIP-equivalence claim |
| New-version entry/risk binding | Separate pinned strategy/data/risk identities and shared deterministic account-risk owner; offline version/replay negatives | Actual admissible source bundle and executable release; no dispatch/attempt authority from a research reservation |
| Protection | Incident obligations, preservation and restart/race fixtures | Broker-supported first-partial-fill protection and continuous stop/target/close handoff; public evidence remains insufficient |

The prepared Alpaca support request remains unsent. No unverified broker
semantics or finite-test outcome is substituted for continuous-protection
acceptance. W3 parents remain open.

Validation on the final bytes: **1,648 full offline tests passed**, with the
existing websockets legacy deprecation warning. The 50 focused descriptive
tests passed independent read-only review. Review reproduced and corrected
offsetting funding activity losing its review flag, and incompatible same-time
equity basis metadata being hidden by deduplication. Ruff check, format (136
files), strict mypy (138 files) and diff checks passed. Calendar/report digests
and non-authorizing flags were independently checked locally. No affected
environment fault occurred.

Commands used from the repository:

```text
rtk proxy env PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider --basetemp /tmp/alpaca-w308-performance-final tests
rtk proxy .venv/bin/ruff check src tests scripts/run_paper_engineering.py scripts/qualify_risk_inputs.py
rtk proxy .venv/bin/ruff format --check src tests scripts/run_paper_engineering.py scripts/qualify_risk_inputs.py
rtk proxy .venv/bin/mypy --strict --cache-dir /tmp/alpaca-w308-performance-mypy-final src tests scripts/qualify_risk_inputs.py scripts/run_paper_engineering.py scripts/reconcile_paper_orders.py scripts/configure_paper_credentials.py
```

## Native API compatibility review — 2026-10-20

User decision after review, 2026-10-20: preserve the original protection
requirements and continue seeking a verifiable supported implementation. The
user did not authorize an independent Bracket execution version, acceptance of
a protection gap, strategy changes or broker orders. Existing valid protection
must be preserved; no guarantee of stop execution price or universal absence of
exit races is inferred from this requirement.

The user authorized the documentation-first compatibility review, not a change
to the accepted protection policy or broker-order permissions. The conclusion
is scoped to the published US retail contract checked today. No claim is made
that an undocumented backend feature is impossible.

### Public contract and mechanism comparison

[Order documentation](https://docs.alpaca.markets/us/docs/orders-at-alpaca)
establishes the following native mechanisms:

| Mechanism | Published behavior | Compatibility result |
| --- | --- | --- |
| Bracket | Children activate on full entry fill; canceling a member cancels remaining members; DAY/GTC only | Does not implement first-partial protection or independent remainder cancellation |
| OTO | One attached exit; remaining bracket requirements inherited | No documented per-partial activation; replacement wording is internally inconsistent, so do not assume mutability |
| OCO | Grouped exits after ownership; prices can be amended | Useful native exits; no documented adoption of a pre-existing standalone stop |
| IOC | May partially fill, remainder canceled | Does not remove the partial-fill protection question |
| FOK | Entire quantity or cancellation; whole-share table has sales-contact qualification | Not an unconditional account entitlement; FOK plus Bracket conflicts with its TIF restriction |
| Trailing stop | Standalone, high-water-mark-based; not a Bracket/OCO child | Not equivalent to a fixed structural stop; no documented future-fill quantity attachment |

The [wash-protection contract](https://docs.alpaca.markets/us/docs/user-protection)
exempts trailing stops and grouped orders, but explicitly rejects ordinary
stop-sell submission against a working limit buy, including paper. An exemption
does not establish pre-position ownership, fill-linked sizing or no-short
semantics. No speculative request is used to discover these properties.

The [REST replacement interface](https://docs.alpaca.markets/us/reference/patchorderbyorderid-1)
does not document existing-stop adoption into OCO. SDK fields alone are not an
exhaustive REST schema. Successful replacement acknowledgement explicitly does
not guarantee venue replacement, so a PATCH response cannot close that gate.

[Single-position close](https://docs.alpaca.markets/us/reference/deleteopenposition-1)
creates a closing order; its documented quantity/percentage inputs do not
specify atomic coordination with a working stop.
[Account-wide close](https://docs.alpaca.markets/us/reference/deleteallopenpositions-1)
with `cancel_orders=true` cancels orders before liquidation and can fail when an
order is no longer cancelable. That is a supported sequential workflow, not
the accepted no-gap handoff. It also affects the whole account, not only an
owned experiment symbol.

[Elite routing](https://docs.alpaca.markets/us/docs/alpaca-elite-smart-router)
documents DMA/VWAP/TWAP, not the missing protection contract. Its entitlement
is not inferred for either existing paper account. No routing upgrade or data
purchase is proposed.

### Comparison against the authoritative accepted research rules

This comparison uses P06/P08/P09/P11 in the accepted research section of
`docs/no-leverage-pullback-research.md`, not a newly invented Ross requirement:

- P11 requires protection of actual partial fills, cancellation of the entry
  remainder on first fill, and reconciliation of racing fills. Canceling a
  Bracket parent is therefore not an independent remainder-only operation.
- P09 fixes the structural stop. A trailing stop changes its price behavior.
- P11 derives the final target from actual average entry after entry termination.
  An upfront Bracket target based on the entry limit can differ when execution
  improves that limit. Later target amendment does not prove the initial target
  was behaviorally equivalent or resolve the partial-entry interval.
- P11's timed close still needs a supported protection-preserving close path.
  Server-side cancellation followed by liquidation cannot be relabeled atomic.

Thus native Bracket is a supported API mechanism but is **not a drop-in
implementation of the accepted candidate**. Simple FOK followed by a new exit
request also leaves a separate post-entry request interval and changes entry
fulfillment; it is not adopted as a workaround. No executable rule, stop,
target, entry TIF or protection gate changed.

### What can be tested, and what still cannot be claimed

The existing bounded experiment plan remains the canonical test envelope.
An isolated supported Bracket experiment could observe parent/child states,
full-fill activation, replacements and reconciliation once its complete
dispatch/cleanup manifest is accepted. It would test native API behavior, not
qualify the current P11 policy. A naturally absent partial fill is NOT_OBSERVED.
Finite successful observations cannot establish atomic handoff or universal
race absence; they can reveal contradictions and implementation faults.

No safe actual-order scenario is newly enabled here: the full supported cleanup
and crash path is still required before dispatch. First finish a source-backed
execution mapping; if none preserves the accepted rules, either an explicitly
reviewed independent execution version or a concrete broker clarification is
needed. Support is one evidence route, not a mandatory prerequisite for all
development. The prior unsent support draft is preserved without submission.

Independent Sol retrieval and a verified Cloud `gemma4:31b` public-only
synthesis were used. Coordinator source review rejected overbroad synthesis
wording: FOK has no attached-stop promise; IOC merely leaves the question open;
`cancel_orders` is not guaranteed successful or atomic; venue replacement is
explicitly unguaranteed rather than an unknown. No local document/account data
was sent to Cloud. This document-only increment does not rerun or supersede the
1,648-test code validation above, start formal paper, or complete W3 parents.

Independent read-only review found no actionable public-contract discrepancy in
this section. It did not claim to validate the local research specification;
the coordinator inspected its authoritative P06/P08/P09/P11 text directly.
The coordinator also checked the public REST replacement page directly rather
than relying on the worker's SDK inventory. `git diff --check` passed. A worker
read initially used unavailable bare `python`; repeating that bounded read with
installed `sed` succeeded, resolving the affected read without a global change.

### Protection-preserving follow-up — 2026-10-20

After the user's explicit decision to retain the original requirements, a
bounded six-search follow-up checked public first-party guidance and potential
maintainer/forum answers rather than repeating the native-order comparison.
No new documented per-partial attachment, existing-stop adoption/group mutation
or stop-preserving close operation was found. This is a bounded search result,
not proof that an undocumented implementation cannot exist.

The [Alpaca-owned paper-trading guide](https://github.com/alpacahq/alpaca-skills/blob/main/skills/trading-api/paper-trading/SKILL.md)
labels `position_intent` as options-only. The coordinator inspected its raw
public text as reference data, not as instructions to install or execute.
The [create-order reference](https://docs.alpaca.markets/us/reference/postorder)
lists that enum with a generic description, but supplies no equity reduce-only
or competing-exit guarantee. Neither source permits treating `sell_to_close`
as a proven equity reversal-prevention mechanism. The guide is mutable `main`,
with no immutable revision established in this check, and is guidance rather
than a venue contract; unrelated guide statements were not adopted.

Historical forum leads were [partial take profit](https://forum.alpaca.markets/t/partial-take-profit-order/13578)
(2024-01/02) and [bracket exit behavior](https://forum.alpaca.markets/t/bracket-order-fails-to-execute-exit-orders/11097)
(2023-02). Their author name suggests Alpaca association, but the rendered
profile did not independently verify staff status. They are leads only, not
official acceptance evidence. Allocating already-owned shares to separate new
exit orders does not document adoption of an established stop; partial exit
handling does not prove protection of partial entry fills.

Sol retrieval, coordinator source checks and verified Cloud `gemma4:31b`
public-only synthesis agree on that limited conclusion. No private file or
account content was exported, no forum message/support request was sent, and
no broker test or policy change was performed. Established valid stops remain
preserved; price guarantees and universal absence of racing exits are not new
requirements. W3-06 acceptance still needs a supported mapping for the precise
partial-fill/handoff/close requirements before actual W3-07 orders; W3-08 cannot
freeze an unaccepted executable release. No parent is marked complete.

## W3-08 source-event denominator audit — 2026-10-20

The user reiterated completion of W3-06–08 without making a support submission
or contact information the next prerequisite. The task plan now retains that
clarification explicitly. Original partial-fill and established-stop protection
requirements, source qualification and broker-order gates remain unchanged.

The existing discovery audit began with a completed scan manifest. That alone
could omit collector work that left raw records without a completed manifest.
The new `Journal.read_scanner_events` and
`scanner_discovery_audit.audit_scanner_event_census` audit the fixed
`alpaca_scanner_data` source prefix through an explicit sequence horizon and
UTC decision cutoff. They retain visible catalog, snapshot and manifest event
identities, per-manifest audit errors, unresolved raw references and explicit
excluded-kind references. Unknown/unfinalized evidence is retained, not promoted
to a failed request, negative strategy decision or complete session census.

Request-complete audited manifests associate their raw dependencies; malformed
or mismatching manifests do not erase orphan records. Shared raw batch/symbol
identities count once in aggregate observation denominators, while each
manifest's denominators remain inspectable. Later manifest arrivals cannot
resolve a historical prefix's orphans. Stored payload corruption and raw
digest/size conflicts abort trust rather than turning the census into zeros.
SQLite authorizer tests reject any attempted insert, update, delete or
transaction during the audit. Existing APIs and source collection behavior are
unchanged; there is no broker request or new persistence format.

The [redacted actual-archive proof](../evidence/w3-scanner-source-prefix-audit-20261020.json)
records a successful audit of the existing October 15 W2 capture. Its source
path is already bound by W2 acceptance evidence. The original archive was
opened read-only with an empty WAL checked, backed up to a temporary working
copy, and left unchanged. The working copy's schema opening preserved exact
event-row digest/count equality. The main archive digest was checked before
and after; no original migration or evidence write occurred. The report binds
the current code, temporary report and audit-driver digests and preserves the
actual requested/returned/missing denominators. No freshness or new live
observation is asserted for this historical capture.

Independent read-only review found no actionable findings and passed all 60
focused discovery tests. These cover unfinalized raw records, malformed/count-
conflicting manifests, shared-source deduplication, distinct sweeps, future
cutoffs/horizons, wrong sources, explicit unknown kinds, storage/raw corruption,
empty prefixes and zero-write enforcement. The generic public-only Cloud
`gemma4:31b` sanity check was advisory; its wall-clock wording was not adopted:
enumerating every stored record in a prefix does not prove continuous real-world
collection. No local code, archive or account data was exported.

This completes the source-event denominator increment, not the formal natural
candidate adapter. Existing schemas do not bind formal origin, candidate
enrollment or the accepted downstream stage census. `natural_candidate_ids`
therefore remains unknown, not an empty set. Engineering/historical records
cannot become natural witnesses; `NOT_SIP_EQUIVALENT` and runtime refusal remain.
W3-06 still lacks accepted actual source/execution mapping; W3-07 actual order
cycles and W3-08 accepted dependencies/final freeze remain open. No W3 parent
is marked complete, and no support message, order or formal paper was started.

Final validation: **1,668 full offline tests passed**, with one existing
websockets legacy deprecation warning. Ruff check and format (136 files), strict
mypy (138 files), independent focused review and diff checks passed. The actual
archive report's code digests and request = returned + missing identity were
checked before saving the redacted evidence. No affected environment fault
occurred. Commands used from the repository:

```text
rtk proxy env PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider --basetemp /tmp/alpaca-w308-scanner-census-final tests
rtk proxy .venv/bin/ruff check src tests scripts/run_paper_engineering.py scripts/qualify_risk_inputs.py
rtk proxy .venv/bin/ruff format --check src tests scripts/run_paper_engineering.py scripts/qualify_risk_inputs.py
rtk proxy .venv/bin/mypy --strict --cache-dir /tmp/alpaca-w308-census-mypy src tests scripts/qualify_risk_inputs.py scripts/run_paper_engineering.py scripts/reconcile_paper_orders.py scripts/configure_paper_credentials.py
```

## Alternative parent-reduction route — 2026-10-20

The user asked to resolve the remaining blockage. A distinct route was checked:
replace a partially filled Bracket parent's total quantity with its cumulative
filled quantity, instead of canceling the parent and therefore its group.
No operation was submitted. Five bounded primary-source/issue searches,
coordinator inspection and verified public-only Cloud `gemma4:31b` analysis did
not establish that this transition is supported for retail equity brackets.

The [retail replacement contract](https://docs.alpaca.markets/us/reference/patchorderbyorderid-1)
permits whole-share quantity amendments in general. It does not establish
equality with cumulative fills, group preservation, child resizing/activation,
or the result of additional fills racing with this particular reduction.
The documented successful-HTTP/venue-replacement distinction remains in force.

The [options error tables](https://docs.alpaca.markets/us/docs/options-trading-overview#error-responses)
explicitly scope their replacement restriction `qty must be > filled_qty` to
options. The coordinator checked that scope directly. It is not a retail-equity
Bracket contract and cannot prove either acceptance or rejection of the proposed
equity transition. The 2021 [partial-sell replacement discussion](https://forum.alpaca.markets/t/quantity-error-when-replacing-partially-filled-sell-order/4329)
and [issue 165](https://github.com/alpacahq/Alpaca-API/issues/165) are historical
problem reports, not a supported buy-parent operation or present-day guarantee.

The [create-order interface](https://docs.alpaca.markets/us/reference/postorder)
documents an absolute take-profit limit price; no native actual-entry-fill-
relative target expression was established. Even a supported parent truncation
would still need proof of the partial interval and the accepted actual-average
target/close behavior. A placeholder target, per-fill regrouping, client-side
trailing emulation or speculative quantity PATCH is not adopted as a solution.

This check corrects scope assumptions rather than changing executable policy.
No new safe dispatch path, current-session E0, source-condition acceptance or
formal enrollment follows. All remaining W3 acceptance items retain their full
scope. No code or broker state changed; document links/scope and diff checks are
the relevant validation, with no new pytest claim. The absence of a supported
public mapping is an unresolved feasibility limit, not proof that an
undocumented backend operation is impossible or a demand to contact support.

## OCO-first alternative design — 2026-10-20

The user requested finding a way to resolve the issue. The
[concrete OCO-first proposal](w3-oco-first-execution-proposal.md) changes the
architecture: establish native grouped exits for newly owned quantity, then
amend take-profit prices rather than convert an existing standalone stop.
This uses a documented order class and its wash exception, but does not establish
the complete partial-entry, multi-group, amendment or close lifecycle.

The provisional-target upper bound is proved by exact monotonic rounding and
checked on deterministic rational inputs in the
[feasibility record](../evidence/w3-oco-first-feasibility-20261020.json). It also
retains an early-exit counterexample against current P11 and a deadline-price
topology counterexample. Neither mathematical result grants broker or strategy
acceptance. Independent local review verified the proposal's scope, inequality,
counterexamples and lack of authority; it did not claim to prove native behavior.

Current P11's `TARGET_BEFORE_ENTRY_TERMINAL` guard remains unchanged. This is a
separately labeled potential route with a genuine early-exit policy difference,
not a verified solution or drop-in implementation. Initial/late-fill protection,
exact fractional residuals, disjoint group reservations, stop continuity during
target replacement and a complete deadline disposition still require evidence.
No whole-task support/contact prerequisite is introduced. No production code,
order permission, broker state or accepted strategy was changed. Document and
artifact checks are the relevant validation; no new pytest run is claimed.

## OCO-first separate research acceptance — 2026-10-20

The user selected **“Allow a separate OCO-first research candidate”** after the
concrete early-target policy difference was presented. The immutable
[research manifest](../evidence/oco-first-execution-research-v1.json) pins
`OCO_FIRST_EXECUTION_RESEARCH_V1`, candidate
`HYBRID_500_IEX_OCO_FIRST_RESEARCH_V1` and the original observation-age parent
contract. Its SHA-256 is
`fa6e29ede5a92428308ad19ba794be5e6e3f0b2a3c216818a54f3ffc9a112d80`.
This accepts the separate research candidate and provisional target execution
before entry termination/reconciliation. It does not accept a Ross-equivalent
rule, the original P11 timing, a second cash/risk budget, runtime or orders.
Original candidate bytes and `TARGET_BEFORE_ENTRY_TERMINAL` remain unchanged.

The candidate keeps the original entry limit and structural stop. Every entry
execution contributes to the final average, including executions whose shares
already exited through a provisional target. Later fills remain owned facts;
they never authorize a replacement entry to restore exited quantity. Group
allocations must use disjoint exact lot portions. Unknown acknowledgements hold
their allocation; fractional residuals are not rounded; over-exit evidence is
retained as a signed position and a sticky incident. These are research
semantics, not evidence of broker reservations or executable coverage.

Source qualification, actual candidate entry-risk binding, provenance and durable
group ownership, initial/late-fill protection, target-amendment stop continuity,
exact residual eligibility, safe close/crash cleanup, isolated order cycles and
release/final protocol freeze remain separate acceptance gates. All authority
fields except research-semantics acceptance are false in the pinned manifest.
`NOT_SIP_EQUIVALENT` remains in force.

The four-page native-order documentation inventory remains a limited source
scope. In particular, an SDK signature omitting group fields does not establish
their absence from REST: the previously pinned
[retail protection contract](../evidence/alpaca-retail-protection-contract-20261016.json)
records `group_id`/`group_role` in Order and OrderLeg. Group identifiers, nested
shape and acknowledgement alone still do not prove stop preservation or
continuous executable protection. No source absence or broker guarantee is
inferred from the narrower inventory.

### Declared allocation and target model

The new [pure research model](../src/alpaca_ross_bot/oco_research.py) validates
the exact manifest and parent bytes before constructing a candidate. Immutable
declared executions bind account reference, symbol, entry scope and execution
identity. Identical observations deduplicate; conflicting identity reuse fails.
Allocations name exact portions of observed entry lots; portions may split one
lot across groups without exceeding its original quantity. Exited shares never
become available for another allocation. Unknown acknowledgements keep their
reservation and cannot release/replay it. A declared acknowledgement is not
proof that a broker accepted a group or that its stop is live.

The model preserves the identity
`entry = exits + signed reserved residual + unallocated` with exact rational
arithmetic. It retains fractional residuals without rounding and declared
over-exits without clipping a negative position. Reverse inventory, group
over-exit, entry-limit violations and invalid reconciled target topology retain
incident evidence and block new group allocation. The final target uses every
entry execution's quantity and price; an early exit cannot substitute the
remaining inventory's average. A late entry invalidates a previously complete
terminal census until a new exact census reconciles it. Repeating the old
census cannot restore finalization.

[Twenty-nine new tests](../tests/test_oco_research.py) cover these properties,
including cross-account/symbol/entry-scope rejection, parent/manifest mutation,
fractional executions, both exit roles, exact cent ceiling and the original
P11 isolation. The implementer's related component run passed 106 tests.
Independent read-only verification passed the 29 focused tests and checked 112
additional split/allocation/exit states for conservation; it found no actionable
issue. The coordinator inspected the frozen implementation and verified pinned
manifest/parent integrity. Original incident and parent contract files have no
working-tree changes.

These checks validate declared arithmetic and reducer behavior. The module is
not a source adapter, durable journal, crash-recovery owner or replacement for
the canonical account-risk allocator. It implements no order/stop-cancellation,
unknown-reservation release, price-amendment dispatch or close operation. The
original P11 guard still rejects early targets. Source qualification, actual
entry-risk binding, durable provenance and all broker/protection/cleanup gates
remain unaccepted. W3-06/07/08 parents remain open, and no account access, order
or formal paper start occurred for this increment.

Final validation of the frozen code: **1,697 full offline tests passed**, with
one existing websockets legacy deprecation warning. Ruff check and format
(138 files), strict mypy (140 files), independent focused verification and diff
checks passed. The full suite used tool-level escalation for the previously
isolated sandbox asyncio limitation; no repository workaround or new environment
fault was introduced. Commands from the repository:

```text
rtk proxy env PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider --basetemp /tmp/alpaca-w306-oco-research-final tests
rtk proxy .venv/bin/ruff check src tests scripts/run_paper_engineering.py scripts/qualify_risk_inputs.py
rtk proxy .venv/bin/ruff format --check src tests scripts/run_paper_engineering.py scripts/qualify_risk_inputs.py
rtk proxy .venv/bin/mypy --strict --cache-dir /tmp/alpaca-w306-oco-research-mypy src tests scripts/qualify_risk_inputs.py scripts/run_paper_engineering.py scripts/reconcile_paper_orders.py scripts/configure_paper_credentials.py
```

## OCO execution identity bound to the shared research risk slot — 2026-10-20

The separate candidate can now opt into the existing
`AccountRiskOwner.reserve_research_decision` with the exact pinned
`OcoResearchContract` and its required observation-age parent contract. The
entry/trend/signal version and signal ID remain those of the parent entry
reducer. A strict nested execution-research binding records the new full
candidate and execution manifest identity separately. This does not rename
parent observations, accept source inputs or represent the original P11 as
allowing early targets.

The binding and existing cash/loss-risk reservation commit in the same original
account-risk event. There is no second slot, changed sizing formula, changed L/S,
new order intent or consumed P06 attempt. Reusing an existing parent reservation
identity with the new execution metadata records a conflict and vetoes the
request; it cannot retrofit the held reservation. A positive retried result must
match the complete held declaration, not only its signal ID. Immutable replay
checks the execution binding's exact field set, value types, pinned artifacts
and false operational acceptance flags. Default declarations omit the new field.

The new `test_oco_risk_binding.py` and related entry/risk tests passed 54 focused
cases. Independent read-only verification passed 74 related tests and rejected
13 additional nested-identity mutations, including missing/extra fields and
boolean/integer substitutions, with no actionable findings. Scoped Ruff and
strict mypy passed. The tests include atomic commit-failure rollback, unchanged
parent identity/prices, shared-slot exclusion and conflict/restart reconstruction.
This is a declared research binding; actual source/entry-risk acceptance,
protection, cleanup and formal release remain unaccepted. No account access or
broker operation occurred.

## Durable declared OCO allocation owner — 2026-10-20

The new `OcoJournal` reuses the existing private journal transaction rather than
creating another account-risk allocator or database. Three narrow wrappers
separate `ALPACA_OCO_RESEARCH` events and deduplication from existing lifecycle,
incident and risk evidence. The first immutable binding pins account, entry
scope, symbol, L/S and the exact research contracts; a competing constructor
cannot substitute another binding. Only an empty model can initialize a scope.

Every view, reopen and mutation verifies the immutable event replay, projection
and scoped deduplication under the same SQLite write lock. Canonical rational
strings preserve exact quantities. Receipt timestamps do not change retry
identity. Conflicting operation bytes are retained as evidence, keep original
model facts, latch a durable conflict and block new allocation. Already-recorded
conflict retries are idempotent through a private sentinel caught only after
locked verification. Other errors propagate; there is no cache/history repair.
Unknown acknowledgements and reservations survive physical close/reopen.

Twenty-five new synthetic tests cover these guarantees, two-connection allocation
and constructor races, append/commit failure rollback, corruption of raw bytes,
hashes, schemas, rational encodings, projections and deduplication, and early-exit
plus late-fill reconstruction. The implementer's combined component run passed
70 tests. Independent verification passed 68 related tests and a separate
two-scope physical-reopen experiment: identical execution/group names remained
isolated with exact reservations and zero intents. No actionable finding was
reported. Scoped Ruff and strict mypy passed; the coordinator inspected direct
implementation and tests.

The owner persists declared facts, not broker-qualified executions or stop
coverage. Source provenance and atomic linkage from the shared risk reservation
to this group's binding remain unaccepted; the two implementations do not imply
that missing integration exists. The pinned research manifest and original P11
remain unchanged. No dispatch, stop cancellation, reservation release, close or
formal-paper authority was added. Actual source/condition qualification,
initial/late-fill protection, broker group reservations, sibling-stop continuity,
safe cleanup/close, isolated API cycles and final protocol freeze remain open.

## Order-group interface recheck — 2026-10-20

Independent primary-source retrieval and coordinator verification checked the
[October 8 changelog](https://docs.alpaca.markets/us/changelog/2026-10-08-order-group-db02af7)
and pinned/current official schema. The changelog documents group identifiers
across replacements and role metadata. The inspected Trading API 2.0.1 schema
contains no group-addressed path; current `master` was byte-identical to the
previous pinned revision. The exact scope, hash and request properties are in
[the public-source receipt](../evidence/w3-oco-group-api-recheck-20261020.json).
These observations do not establish group mutation or sibling-stop continuity,
and absence from these artifacts does not prove an undocumented operation
impossible. No broker request or new capability acceptance follows.

An initial sandbox public-fetch DNS failure was isolated as `ENV_FAULT` for that
shell operation. The affected route stopped; the same read-only public fetch
then succeeded with tool-level escalation and was independently verified. No
global networking or application change was made; unaffected offline work
continued safely. The affected public retrieval scope is resolved.

### Combined validation of durability and shared-risk binding

Final frozen-code regression passed **1,736 offline tests**, with one existing
websockets legacy deprecation warning. Ruff check and format (141 files), strict
mypy (143 files), the separate independent risk/durability reviews and diff
checks passed. The full suite used tool-level escalation for the previously
verified sandbox asyncio limitation, without an application workaround. No
protected account inputs or broker state were accessed. Commands:

```text
rtk proxy env PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider --basetemp /tmp/alpaca-w306-oco-durable-final tests
rtk proxy .venv/bin/ruff check src tests scripts/run_paper_engineering.py scripts/qualify_risk_inputs.py
rtk proxy .venv/bin/ruff format --check src tests scripts/run_paper_engineering.py scripts/qualify_risk_inputs.py
rtk proxy .venv/bin/mypy --strict --cache-dir /tmp/alpaca-w306-oco-durable-mypy src tests scripts/qualify_risk_inputs.py scripts/run_paper_engineering.py scripts/reconcile_paper_orders.py scripts/configure_paper_credentials.py
```

W3 completion remains unproven. The next engineering dependency is source-bound,
atomic linkage of the original risk reservation and actual execution evidence
into the OCO scope; declared metadata plus a durable model do not establish it.
Full source/condition admission and supported protection/close semantics are
also still required before actual isolated order cycles or formal preregistration.
W3-06/07/08 parents therefore remain open with their original acceptance scope.

## OCO local entry receipt and candidate incident integration — 2026-10-20

This increment completes local component linkage for the separately accepted
`HYBRID_500_IEX_OCO_FIRST_RESEARCH_V1`; it does not accept the full W3-06/07/08
parents. The original P11 and both pinned research manifests are unchanged.
`NOT_SIP_EQUIVALENT`, source/runtime/protection/release refusal and engineering
sample exclusion remain in force.

### Original reservation and execution provenance

The new [entry evidence adapter](../src/alpaca_ross_bot/oco_entry_evidence.py)
derives a receipt from the immutable, decision-visible source prefix and the
existing canonical account-risk owner. It requires a positive held reservation,
qualified current observations, exact account/symbol/entry/contract identity,
original L/S and reserved quantity, and agreement between per-execution activity,
lifecycle and held fills. It uses execution quantity and price, not cumulative
quantity as a new fill. Original parent entry IDs and the original shared risk
budget are preserved. A zero/veto reservation attempt cannot become the receipt's
origin: the adapter verifies the actual transition that created the positive
held slot. A later occupied-slot attempt cannot replace that origin either.

[OcoJournal.bind_entry_census](../src/alpaca_ross_bot/oco_journal.py) verifies the
prior model, captures the source horizon and appends the receipt and projection
under the existing private write transaction. Retries reuse the original receipt
and horizon. Changed requests retain original facts and latch a conflict; prior
entry facts cannot shrink or change. Newly observed entry fills invalidate a
previous terminal census until exact reconciliation. Receipt availability cannot
precede its decision or cited source visibility; both writer and immutable replay
reject backdating. Regression cases cover the positive-origin and causal-time
faults identified during independent review.

The receipt explicitly says `LOCAL_REPORTED_ENTRY_CENSUS_NOT_SOURCE_ADMISSION`.
Its intent origin is `ENGINEERING_TEST`, with `formal_sample_eligible=false`.
No entry intent, risk slot, send attempt or runtime permission is created by
binding this evidence. Positive cases use source-shaped synthetic engineering
journals; no positive nonflat broker qualification was performed this increment.
The supported seam requires whole-fill simple-equity ENTRY observations.
Fractional individual source executions and native exit/group ownership remain
unsupported; the separate declared arithmetic model still retains exact
fractional quantities without implying source acceptance.

### Explicit incident opt-in and six integrated fault cases

The [incident policy](../src/alpaca_ross_bot/incident_state.py) accepts this full
candidate only with both exact pinned contracts. Its inherited entry/data/risk
binding remains separate from its explicit execution-research binding. Missing,
mixed, forged or authority-enabled bindings fail closed. Default original and
parent policy serialization and behavior remain compatible; an existing incident
scope cannot be silently upgraded. Only the provisional target-before-entry-
terminal reason changes under the new opt-in. Unknown acknowledgements,
protection, cancellation, durability, closing, reverse inventory and double-exit
obligations remain enforced. All operational acceptance flags remain false.

[Six integration cases](../tests/test_oco_execution_integration.py) join the local
source-entry receipt, canonical reservation, durable allocation book and explicit
incident policy: unknown acknowledgement retry, active-session disconnect,
failed derived-entry append after a new canonical fill, cancel/fill race,
physical close/reopen and both declared exit roles filling. Rollback preserves
previously committed raw source/risk evidence and cannot clear incident duties.
Over-exits remain signed and block further allocation. Exits, groups and
acknowledgements in these cases are declared, unqualified facts, and incident
observations are assembled explicitly in the test. This is component/replay
integration, not an automatic runtime incident observer, native protection proof
or actual broker order-cycle evidence.

### Validation and remaining acceptance

The final frozen implementation passed 1,820 full offline tests, with one
existing websockets legacy deprecation warning. Ruff check, format check (145
files) and strict mypy (147 files) passed. Independent source recheck passed 51
related tests and verified rejection of the original backdated-receipt
counterexample without state changes. Independent incident review covered 64
parent/OCO combinations; independent integration review passed all six cases.
No actionable review finding remained in this supported component scope.
Commands from the repository:

```text
rtk proxy env PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider --basetemp /tmp/alpaca-w306-entry-incident-final tests
rtk proxy env PYTHONDONTWRITEBYTECODE=1 .venv/bin/ruff check --no-cache src tests scripts/run_paper_engineering.py scripts/qualify_risk_inputs.py
rtk proxy env PYTHONDONTWRITEBYTECODE=1 .venv/bin/ruff format --check --no-cache src tests scripts/run_paper_engineering.py scripts/qualify_risk_inputs.py
rtk proxy env PYTHONDONTWRITEBYTECODE=1 .venv/bin/mypy --strict --cache-dir /tmp/alpaca-w306-entry-incident-mypy src tests scripts/qualify_risk_inputs.py scripts/run_paper_engineering.py scripts/reconcile_paper_orders.py scripts/configure_paper_credentials.py
```

The environment permission profile changed to read-only repository access during
this increment (`ENV_FAULT`, permission-profile scope). Affected writes stopped;
reviewed exact file patches and final Git mutations used tool-level escalation.
The global permission policy was not changed or bypassed. Checks kept generated
caches in `/tmp` or disabled them. The full suite also used approved execution
for the previously isolated sandbox asyncio shutdown limitation. Original
operations were verified; unrelated working-tree edits were preserved.

A known recovery boundary remains explicit: after terminal-flat completion has
moved the canonical held slot to completed history, a newly discovered late
original-entry fill is preserved and vetoed by the source owner. This adapter
refuses qualification rather than resurrecting a completed slot. Supported
recovery/quarantine for that state remains open; pending-entry late-fill tests do
not prove it. Automatic observation wiring, native exit/group provenance,
fractional source execution handling and actual source/condition admission also
remain unaccepted. Initial/late-fill protection, broker group reservations,
sibling-stop continuity during target replacement and safe timed close still
need supported execution evidence. Actual isolated API cycles and protected
cleanup follow those gates. W3-08 additionally needs accepted dependencies,
natural-candidate enrollment/reviewer provenance and final version/release freeze.
The [protocol draft](w3-paper-protocol.md) now explicitly separates this candidate
and engineering receipts without changing existing coverage minima or starting
a window. No account access, broker write, formal paper or live activity occurred
for this increment. W3-06/07/08 parents remain open.

## W3-06 source-derived incident observations and completed-scope quarantine — 2026-10-20

The separate OCO-first research candidate now has a local source-derived incident
observation path. This completes the reported-source monitor increment, not the
original W3-06 parent: actual source/risk admission and supported native
protection/close acceptance remain unmet. The original P11 and both pinned
research manifests are unchanged; `NOT_SIP_EQUIVALENT` remains in force.

The new [execution incident adapter](../src/alpaca_ross_bot/incident_execution.py)
reads the canonical account-risk owner and lifecycle at the same decision time
and exact source horizon. References identify original immutable events and
hashes. Current held ownership or historically reconstructed completed ownership
provides explicit roles; unknown/native group roles are not inferred. Baseline
comes only from recorded baseline evidence. Stale, unavailable or disagreeing
positions remain unknown. Disconnects, pending cancellation, daily stops,
foreign/unowned evidence and fractional source limitations retain explicit
quarantine. Intent-origin classification requires a visible risk-prefix census
reference: an intent created after the horizon cannot rewrite an old receipt.

[IncidentJournal.observe_execution](../src/alpaca_ross_bot/incident_journal.py)
derives and commits the receipt and projection under the existing private write
lock. The first source horizon remains fixed on retries. Changed requests retain
the original receipt, latch a conflict and cannot replace old facts; already
recorded conflicts deduplicate too. Availability must follow the original
receipt decision. Replay rederives the receipt at its original horizon. Atomic
append failure rolls back only the derived event/state, leaving committed source
and risk history intact. Schema 3 retains the receipt and latched quarantine;
original schema-1/2 paths remain compatible. Manual `OBSERVE` and source-derived
`BOUND_EXECUTION` cannot mix in either direction within one incident scope,
including during replay. This prevents declared protection flags from becoming
apparent source evidence and preserves the prior scope on refusal.

The pure adapter cannot attest durability. Only the existing atomic journal
writer marks its committed observation durable, and no successful projection is
returned on failed persistence. Source admission, data/reconciliation/recovery,
native protection, dispatch, runtime and formal-sample authority remain false.
A subscribed stream without a cleared gap is not reported as connected. Closing
due is not guessed: its calendar qualification remains explicitly missing. This
is deterministic local observation derivation, not a continuously deployed or
release-qualified supervisor.

The completed-slot regression records a terminal-flat round, then a later
original-entry fill. The monitor retains entry/exit/reported-position quantities
and the original role provenance, while the canonical risk owner keeps
`held=None` and its unchanged completed history. It does not resurrect a slot,
create an intent, release a reservation, cancel protection or grant recovery.
Quarantine retains the discrepancy for reconciliation/escalation. This completes
safe observation of that boundary, not qualified allocation recovery or native
protection for the newly discovered quantity.

Validation of the frozen code: 1,852 full offline tests passed, with the
existing websockets legacy deprecation warning. Ruff check and format (148 files)
and strict mypy (150 files) passed. Independent read-only verification passed all
32 new source/journal cases and found no actionable issue in the four frozen
implementation/test files. Cases cover original-horizon retries, origin ancestry,
changed requests, causal timestamps, physical reopen, rollback, both mode-mixing
directions, stale/invisible evidence, unknown position, fractional retention,
explicit stop role without protection acceptance and completed-scope late fills.
Commands from the repository:

```text
rtk proxy env PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider --basetemp /tmp/alpaca-w306-source-incident-final tests
rtk proxy env PYTHONDONTWRITEBYTECODE=1 .venv/bin/ruff check --no-cache src tests scripts/run_paper_engineering.py scripts/qualify_risk_inputs.py
rtk proxy env PYTHONDONTWRITEBYTECODE=1 .venv/bin/ruff format --check --no-cache src tests scripts/run_paper_engineering.py scripts/qualify_risk_inputs.py
rtk proxy env PYTHONDONTWRITEBYTECODE=1 .venv/bin/mypy --strict --cache-dir /tmp/alpaca-w306-source-incident-final-mypy src tests scripts/qualify_risk_inputs.py scripts/run_paper_engineering.py scripts/reconcile_paper_orders.py scripts/configure_paper_credentials.py
```

A bounded generic event-sourcing analysis ran through the configured Cloud route
and returned `gemma4:31b`; no repository documents, code or protected account data
were sent. Its checklist did not authorize reconciliation or broker actions.
The full offline suite used tool-level escalation for the previously isolated
sandbox asyncio limitation, with temporary caches and no repository workaround.
No new environment repair, private-account access or broker operation was needed.

W3-06 remains open. Required acceptance still includes actual qualified W3-02
inputs, native protection of initial/racing fills, broker group reservations and
exact residual eligibility, sibling-stop continuity during target amendment, and
a supported protection-preserving timed close. Native exit/group source binding,
qualified completed-slot recovery and continuous runtime verification also remain
unaccepted. Synthetic success and local quarantine cannot supply those contracts.
No order, cancellation, account reset, live mode or formal paper start occurred.

## Native OCO capture temporal-consistency validation — 2026-10-22

`native_oco_capture.py` adds finite GET-only account, nested-order and paginated
per-order FILL collection in the existing private, already-bound journal.
`native_oco_evidence.py` validates exact source bytes, identity, chronology,
group topology and capture-local quantity agreement. Fractions and signed
remaining quantities survive interpretation; replacement links block a claim
of complete history. Duplicate conflicts, failed requests and incomplete page
budgets remain evidence rather than empty-success substitutes. Both exit roles
having fills is not itself an accounting failure when quantities reconcile.

Public schema basis: Alpaca's Trading API 2.0.1 schema at commit
`2a973a046607c42742bf141184b71f1990fa1809`, file
`src/alpaca_mcp_server/specs/trading-api.json`, SHA-256
`e67cf32d58cd5f918c593925e966bf737eff1e0f03f15c7c0ba467368eaa6d42`.
Its group IDs/roles and replacement links identify reported groups; they do
not promise ownership, parent-child client-ID equality, complete replacement
history or atomic stop continuity. Locally required fields are stronger
evidence requirements than the broker schema's optional fields.

The existing, uncommitted native OCO read-only collector and interpreter were
reused rather than rebuilt. The interpreter now keeps source timestamps bound
to the original containing HTTP observation: a later decision time cannot clear
an order/fill timestamp conflict, and a fill after an order snapshot cannot
explain that earlier snapshot merely because quantities match. Raw facts remain
preserved; these are local observation/clock uncertainties, not a claimed broker
clock violation or exchange timing guarantee.

Focused validation passed 78 tests in `tests/test_native_oco_evidence.py` and
`tests/test_native_oco_capture.py`, including seven temporal regression cases.
Ruff check, format check and strict mypy passed for those four implementation
and test files. The full offline suite subsequently passed 1,930 tests, with
the existing websockets legacy deprecation warning. Independent final review
found no actionable issue in the five implementation/test files and separately
passed all seven temporal regressions without repeating the full suite.
Repository-wide Ruff check and format check passed (152 formatted files).
Strict mypy passed all 154 source/test/script files. Full validation commands:

```text
rtk proxy env PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider --basetemp /tmp/alpaca-native-oco-final-20261022 tests
rtk proxy env PYTHONDONTWRITEBYTECODE=1 .venv/bin/ruff check --no-cache src tests scripts/run_paper_engineering.py scripts/qualify_risk_inputs.py
rtk proxy env PYTHONDONTWRITEBYTECODE=1 .venv/bin/ruff format --check --no-cache src tests scripts/run_paper_engineering.py scripts/qualify_risk_inputs.py
rtk proxy env PYTHONDONTWRITEBYTECODE=1 .venv/bin/mypy --strict --cache-dir /tmp/alpaca-native-oco-global-mypy src tests scripts/qualify_risk_inputs.py scripts/run_paper_engineering.py scripts/reconcile_paper_orders.py scripts/configure_paper_credentials.py
```

These checks used synthetic evidence only. Capture-local pagination and
quantity consistency do not establish local order ownership, broker ACK
provenance, continuous stop protection, actual strategy admission, runtime
authority or W3-06/07/08 completion. No account connection or broker action was
performed for this validation.

## Historical OCO prefix for request provenance — 2026-10-22

The next native request/ACK integration must not derive old request terms from
today's OCO allocation cache. `OcoJournal.read_verified_at` now verifies current
immutable events, projection and deduplication, then replays only the specified
entry-scope prefix whose events were available at the decision time. It returns
the original model/source receipt without creating a request, ACK or broker
operation. Existing schemas and operational authority flags are unchanged.

Outside a transaction, a deferred read transaction pins one SQLite snapshot
across current-integrity verification and historical replay. Inside a caller's
transaction it neither starts a nested transaction nor commits/rolls back the
caller's work, including on failure. A WAL two-connection test commits a later
fill during verification and checks that the reader still returns the original
snapshot. Other focused cases cover source-bound partial/terminal receipts,
later target/quantity changes, unknown reservations, scoped conflicts, foreign
scope isolation, invalid horizons/clocks and corrupted current evidence/cache.
Historical reads do not repair state or clear current vetoes.

Seventy focused journal/entry-evidence tests passed, together with scoped strict
mypy and Ruff/format checks. Independent final review found no actionable issue
and passed 17 historical-reader cases. Reviewed source SHA-256 is
`216d11bfc0dbb607854d6274766684d0478846eb31791e82f5f3f991efcc4bef`;
test SHA-256 is
`98d3e02248273c902b8910a7c2196e26ded32823da66ca2bb6620cd10eeb46d9`.
Full regression passed 1,947 tests, with the existing websockets legacy
deprecation warning. Repository-wide Ruff check/format (152 files) and strict
mypy (154 files) also passed. Full regression used the previously approved
execution route for the isolated sandbox asyncio limitation, with no application
workaround. Commands:

```text
rtk proxy env PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider --basetemp /tmp/alpaca-oco-history-full-20261022 tests
rtk proxy env PYTHONDONTWRITEBYTECODE=1 .venv/bin/ruff check --no-cache src tests scripts/run_paper_engineering.py scripts/qualify_risk_inputs.py
rtk proxy env PYTHONDONTWRITEBYTECODE=1 .venv/bin/ruff format --check --no-cache src tests scripts/run_paper_engineering.py scripts/qualify_risk_inputs.py
rtk proxy env PYTHONDONTWRITEBYTECODE=1 .venv/bin/mypy --strict --cache-dir /tmp/alpaca-oco-history-full-mypy src tests scripts/qualify_risk_inputs.py scripts/run_paper_engineering.py scripts/reconcile_paper_orders.py scripts/configure_paper_credentials.py
```

The integration contract in
`w3-oco-first-execution-proposal.md` now
specifies immutable request/attempt/correlation boundaries; the native request
registry, actual dispatch/ACK ownership, protection and close/runtime gates
remain unimplemented or unaccepted. W3-06/07/08 parents remain open. No account
access or broker action occurred.

## Prepared native OCO request provenance — 2026-10-22

The next W3-06 increment adds a separate private request-provenance namespace,
reusing the existing OCO allocation book and canonical account-risk owner.
Preparation derives exact quantity, provisional target and stop from a verified
historical allocation, then requires a fresh source-bound entry/risk census at
the same evidence horizon. It pins the original positive reservation, allocation,
entry-binding and owner references, request bytes/hash and candidate manifests.
It cannot create an ordinary dispatch-compatible order intent, reserve another
risk slot, send an order, record an ACK or accept broker protection.

An identical retry keeps its original receipt and source horizon even after
later observations; this is historical provenance, not refreshed clearance.
Noncolliding changes to an existing group retain the original receipt and latch
an explicit conflict. Simple/native client-ID uniqueness is checked under the
same SQLite write lock in both directions. Independent review additionally
found that the changed-command branch needed the same collision protection.
The repaired branch rejects an unrelated claimed ID before any append, while
preserving idempotent reads of the same group's already-recorded conflict.
Replay also rejects a cross-key alias. Three new regressions passed, and final
independent review found no remaining actionable issue and passed five selected
non-race cases on the repaired bytes.

Public request basis: the official [OCO documentation](https://docs.alpaca.markets/us/docs/orders-at-alpaca)
and pinned Trading API 2.0.1 schema already identified above. The prepared shape
uses the documented standalone OCO example's nested take-profit/stop-loss fields
and omits top-level `limit_price`. The generic create schema's prose is less
specific about that omission; no broker-tested acceptance is claimed. The
existing entry's `day` time in force is retained; no GTC/extended-hours policy
is added. Source retrieval's sandbox DNS fault was isolated and resolved by
tool-level approval for the same read-only public fetch; no application or
global networking changes were made. Resolved source-retrieval fault packet:

```text
status: ENV_FAULT (resolved)
category: sandbox_network_dns
evidence: curl exit 6 for the public pinned schema; approved identical GET succeeded and its hash was verified
affected_scope: public schema retrieval only
safe_to_continue: public-source verification and offline repository work
recommended_action: none outstanding; retain tool-level escalation rather than an application/network workaround
```

The final full regression passed 1,973 tests, including all 26 new synthetic
request-provenance cases, with the existing websockets legacy deprecation
warning. Repository-wide Ruff check/format passed (154 files) and strict mypy
passed (156 files). Tests cover source-derived exact terms, no extra budget or
send-compatible intent, stale/unbound input vetoes, frozen retries, physical
restart, cache/raw corruption, write-then-fail rollback, scope isolation, both
directions of simple/native ID collision and actual two-connection races.
Reviewed source SHA-256 is
`b79bfaecf8a1abee75cee30f2739acb5434fd2c49313fbb4121de7699922e00f`;
Journal SHA-256 is
`f9e879e862faee1f6eac405d8dc0d68288ab884f3999c03128b38f610786b219`;
test SHA-256 is
`cb0015ff92704fc56cefff25a32ad2f2e7d569e3742249c0c63ad1734341b272`.
The full suite used the approved execution route for the previously isolated
sandbox asyncio limitation, without an application workaround. Commands:

```text
rtk proxy env PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider --basetemp /tmp/alpaca-native-request-full-20261022 tests
rtk proxy env PYTHONDONTWRITEBYTECODE=1 .venv/bin/ruff check --no-cache src tests scripts/run_paper_engineering.py scripts/qualify_risk_inputs.py
rtk proxy env PYTHONDONTWRITEBYTECODE=1 .venv/bin/ruff format --check --no-cache src tests scripts/run_paper_engineering.py scripts/qualify_risk_inputs.py
rtk proxy env PYTHONDONTWRITEBYTECODE=1 .venv/bin/mypy --strict --cache-dir /tmp/alpaca-native-request-full-mypy src tests scripts/qualify_risk_inputs.py scripts/run_paper_engineering.py scripts/reconcile_paper_orders.py scripts/configure_paper_credentials.py
```

Actual send-attempt/ACK provenance, native source-group ownership, continuous
initial/late-fill protection, sibling-stop continuity, timed close/runtime,
W3-07 real cycles and W3-08 final registration remain open. No account access,
broker request, cancellation, reset, paid data or formal paper start occurred.

## Prepared-request GET association and reported source stages — 2026-10-22

This increment reuses the accepted local request, risk and discovery components;
it does not repeat broker research or collect new market data. The separate
native correlation namespace binds the original PREPARED request and historical
source prefix to one retained nested GET observation. Exact account/client ID,
terms, capture clocks and root/stop/group identities are checked; first
assignments survive later conflicts, and changed commands latch conflicts.
Capture-local residual arithmetic retains fractional/signed facts without
claiming continuous protection, broker reservation correctness or ownership.
Historical reads still require current immutable/cache integrity. No ordinary
order intent, extra risk slot, send marker or ACK is created. Raw native lookup
by client ID is one GET without undocumented nested parameters or automatic
retry; the existing simple-order sender is unchanged.

Independent native review found no actionable defect on the frozen increment;
four selected historical/conflict/rollback checks passed. Root integration
checks passed seven cases; native lookup plus existing simple HTTP checks passed
35 cases. The twenty correlation unit cases were covered by the earlier focused
runs; these synthetic durations are not protection-latency acceptance.

`paper_source_stages` reuses the existing hash-verified scanner denominator and
reports decision-visible source/screen stages. All outward event references
carry a declared journal scope; that declaration is not authenticated enrollment.
Independent review identified and verified repairs for two omissions: empty
screen rows now remain explicit missing census evidence, and nested discovery
references now retain scope. Five independent selected regressions passed;
the complete focused stage suite passed 36 cases.

The [redacted archive compatibility report](../evidence/w3-source-stage-compatibility-20261022.json)
checks the new adapter on a read-only backup of the already audited October 15
archive through sequence 13801. Original database/WAL hashes and prior
denominators were preserved: 14398 requested, 13479 returned, 919 missing.
Unmapped derived events are explicitly retained, not counted as enrolled
candidate episodes. No new capture or broker request occurred. Natural candidate
IDs remain unknown; source admission, enrollment, formal eligibility and runtime
authority remain false.

A bounded generic public-only Cloud analysis ran with the configured remote
`gemma4:31b`; no repository, credential or account material was exported.
Unsupported temporal/origin claims in its output were rejected. Model output
was not treated as broker evidence or operational authority.

Actual W3-02 qualification, native send/ACK and execution ownership, first/late
partial-fill protection, sibling-stop continuity, safe close and continuous
runtime remain open. W3-07 still needs actual isolated protected order cycles;
W3-08 needs prospective episode/origin/enrollment, ownership-backed performance
inputs, independent review and final freeze. No parent acceptance is claimed.

The next actual-input seam was independently located without new collection:
`entry_strategy.InputHealth` and trade/quote condition eligibility still consume
offline attestations; `market_input_checks` reports condition names but retains
`CONDITION_POLICY_UNQUALIFIED`. Passing observation admission is component-only,
not strategy eligibility. Reuse `AccountRiskOwner`'s existing one-prefix
funds/equity/E0/fee/lifecycle join; do not rebuild that owner. Its research
reservation does not turn a declared strategy decision into raw-market
qualification. Current-session private E0 status was not read or inferred in
this audit.

Integrated validation completed: repository Ruff check and format passed (160
files formatted); strict mypy passed 162 files. The single full offline pytest
run passed 2050 tests in 407.02 seconds, with only the existing websockets legacy
deprecation warning. The approved execution route handled the previously
isolated sandbox asyncio limitation; no application workaround was added.
Command:

```text
rtk proxy env PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider --basetemp /tmp/alpaca-native-association-stages-full-20261022 tests
```

Frozen production SHA-256 values: native correlation
`d5a54a1df302a609a3367afcf46903773cf22727e167c541554e95c79c78c1e3`;
native requests `ea75d7fbcbfd5d51956ab31719c56d9d58abe47f0b83efa3e8e26907ddb49ee5`;
journal `f897037127884033bfeaa6de492d1f0bfafc2e7e3a2e1ea712cc4d28df853388`;
HTTP `daed65c40bd6a579ed5d05097ade7bc3acefe1c753d202236d5df98cf5017885`;
source stages `cec6dae4cc0ede641b1ec205e8f27d65fc903d5e7277d2e6cc4f4f1a12ea13df`.
The archive compatibility report hash is
`74e20ff863f4dd45e4543261719c937606cb6f6022329fbbcd0107d160d4a9f6`.

## Actual-trigger qualification source audit — 2026-10-22

After committing the validated increment as `e2b4d3a`, independent public-source
retrieval narrowed the unresolved upstream admission facts rather than
reimplementing the risk owner or rerunning saved captures. The coordinator
checked the official IEX specification directory, TOPS v1.66 and UTP input
v4.0a. The [source-scoped admission inventory](w3-data-admission-proposal.md#iex-normalization-and-completeness-evidence-boundary)
records the precise versions and limitations.

Native TOPS has sequenced delivery and gap fills, but no inspected public
interface established that Alpaca forwards these guarantees or maps native
quote flags to its condition/tape fields. UTP's R definition is SIP-scoped,
not a verified IEX normalization. Historical pagination does not itself supply
original-delivery or revision-finality evidence. These are specific unknowns,
not a claim that the provider cannot offer supporting evidence. No whitelist,
source switch, provider completeness assumption or new risk owner was adopted.
There was no account access, new capture, broker write or formal paper start.

## W3-08 draft fidelity review matrix — 2026-10-22

The [protocol's concrete review rubric](w3-paper-protocol.md#draft-per-rule-fidelity-rubric--not-frozen-or-assigned)
now maps P01–P13 to independent calculations and source evidence, with the
observation-age and OCO-first differences separated from their parents. It
specifies scoped evidence references, prospective origin/enrollment, masked
decision-time packages, independently sealed expected calculations before
unmasking, per-rule MATCH/MISMATCH/ABSTAIN and append-only adjudication.
Exit reviews use their own action instants; later fills cannot leak into entry
expectations. Missing or unresolved evidence cannot become a positive witness.

The coordinator compared the matrix against the authoritative P01–P13 table
and both accepted research manifests. No numerical rule, enabled cell, manifest
or acceptance gate changed. Reviewer identities/independence, the masking
mechanism, independent calculator, adjudication owner, natural episode/stage/
enrollment contract and executable release/final seal remain pending. This is
a completed draft-review specification increment, not reviewer appointment,
formal enrollment, W3-08 acceptance or a paper start. Documentation-only changes
were checked for whitespace, local links and all thirteen rule rows; the prior
2050-test run was not repeated because no executable bytes changed.

## Observed ledger and engineering roundtrip attribution — 2026-10-22

`paper_ledger_sources.inspect_paper_ledger_sources` now reads a bound private
account capture and canonical risk/lifecycle evidence in one read snapshot at a
fixed visible horizon. It retains each canonical activity variant, all source
occurrences, original timestamp text/submicrosecond remainder and exact amounts.
Raw decimal JSON numbers remain numeric and exact; they are not coerced to float
or disguised as broker string fields. Invalid numeric quantity shape remains an
unqualified observation rather than a silently repaired execution.

For simple ENGINEERING_TEST orders only, observed completed roundtrips require
the original positive canonical reservation, explicit exit bindings, exact
client-to-broker identity joins, terminal lifecycle and matching fill censuses.
Balanced cash or quantities alone cannot attribute a roundtrip. The raw capture
must match the owner's actual source selection; newer captures cannot borrow
older positive ownership. Unknown fees, conflicting identities, fractional,
future and unbound activities remain visible and cannot gain inferred ownership.
Caller-declared scope labels do not authenticate a namespace or formal origin.

Independent coordinator review found and verified two repairs: ordinary daily
stop latches must not hide properly attributed completed losses, and unknown
raw JSON numeric fields must retain exact numeric type without crashing standard
serialization. The unchanged risk policy still blocks new trading; identity/
clock/source conflicts still prevent attribution. A generic public-only remote
`gemma4:31b` analysis ran without repository/account export. Its invented temporal
relation between a fill and a reconciliation timestamp was explicitly rejected;
it supplies no ownership or operational authority.

Validation on frozen bytes: 18 focused unit cases passed in 8.11 seconds and two
independent source-shaped integration cases passed in 2.83 seconds. Strict mypy,
Ruff check and format checks passed for the three affected files. Integration
checks show that a flat unbound pair cannot become a roundtrip, a historical
completed prefix survives later unbound inventory without adopting it, current
unbound inventory refuses attribution, and reads do not mutate journal state.
Units cover three losses with a business-stop latch, nanosecond and decimal
precision, duplicate occurrences/conflicts, unsupported fee attribution,
fractional/future rows, stale/changed capture, account/horizon/clock/scope errors,
caller transaction preservation and immutable/cache corruption. Commands:

```text
rtk proxy env PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider --basetemp /tmp/alpaca-ledger-source-independent-20261022 tests/test_paper_ledger_sources_integration.py
```

Source SHA-256:
`96d7ae8606df93b016238344bd5a1ebc7993ec861a69e461d67fbc0783efc047`;
unit tests `da3a48735e2357861efe2f48078fb1bad988909b2b72f3ff3a0c636b6a25345b`;
independent integration tests
`e7b4845765bb199024c06c648d9df9f4d205ad67084d5783d73ed7c2f5b791b9`.
The prior full 2050-test result is the unchanged baseline, not a claim that this
new increment ran through a new full suite. Focused tests cover the new isolated
reader; existing production modules and manifests were not modified.

Focused worker commands (no rerun was needed after the frozen hashes matched):

```text
rtk proxy env PYTHONDONTWRITEBYTECODE=1 .venv/bin/pytest -p no:cacheprovider -q tests/test_paper_ledger_sources.py
rtk proxy .venv/bin/mypy --strict --cache-dir /tmp/alpaca-paper-ledger-mypy src/alpaca_ross_bot/paper_ledger_sources.py tests/test_paper_ledger_sources.py
rtk proxy .venv/bin/ruff check --no-cache src/alpaca_ross_bot/paper_ledger_sources.py tests/test_paper_ledger_sources.py
rtk proxy .venv/bin/ruff format --check --no-cache src/alpaca_ross_bot/paper_ledger_sources.py tests/test_paper_ledger_sources.py
rtk proxy env PYTHONDONTWRITEBYTECODE=1 .venv/bin/mypy --strict --cache-dir /tmp/alpaca-ledger-source-independent-mypy tests/test_paper_ledger_sources_integration.py
```

This remains observed engineering attribution, not accepted inputs for complete
formal performance. Native OCO ownership, natural study origin/enrollment,
execution-order/precision conversion, account-day completeness, late-post
finality, fees/window snapshots and the final `paper_performance` adapter remain
open. Source-admission/formal/runtime/dispatch flags remain false. W3-06 actual
input/protection and W3-07 protected actual cycles remain open; no parent
acceptance, broker/account access, order or formal paper start occurred.

## Injected native OCO precall and raw-outcome provenance — 2026-10-22

The critical-path audit found a concrete implementable transport-evidence seam
without changing strategy/data/protection policy. `NativeOcoAttempts` binds one
committed pre-call intent to the original prepared request, exact body/hash,
account/CID/group and immutable source prefix. Only the newly committed original
intent may invoke the explicitly supplied fake callback. There is no default
transport, credentials, CLI or PaperOrderHttp wiring. All observations are fixed
as INJECTED_TEST_CALLBACK_NOT_BROKER_PROOF; a caller cannot select actual origin.

Raw status/body bytes or controlled unknown/error categories are stored in a
separate outcome transaction. Duplicate calls return original evidence without
another callback; changed commands retain original references and sticky
conflicts. Crash, timeout, invalid completion clock or outcome-write failure
retains the consumed fence and cannot release allocation. Raw matching reuses
the existing native topology parser but grants no ACK or execution ownership;
responses containing fills remain unqualified without a source execution census.
Existing ordinary-order intent/send/cancel machinery is unchanged.

Independent review identified an outcome-side transaction boundary: a callback
or clock may open its own transaction after the initial caller check. The new
guard refuses outcome persistence without committing or rolling back that
external work; the original committed fence remains consumed. Regression cases
cover both callback and clock variants. The public-only remote `gemma4:31b`
analysis ran without repository/account export; its claims that signed broker
receipts are required or that an ACK proves ownership/protection were rejected.

The focused implementation run passed 24 cases in 172.08 seconds. Two independent
integration cases passed in 22.96 seconds: another connection sees the committed
precall before callback, and outcome write-then-fail rollback followed by physical
reopen never replays the callback. They also preserve original risk/lifecycle
state, UNKNOWN allocation acknowledgment and ordinary native-CID refusal.
Production was frozen before the combined regression. The final combined run
passed 2096 tests in 587.57 seconds, including the final test-only typing edits,
with only the existing websockets legacy deprecation warning. Repository-wide
Ruff check/format passed (166 files); strict mypy passed 168 files. This accepts
the injected engineering increment only, not actual transport or W3 parents.
The approved execution route handled the previously isolated sandbox asyncio
limitation without an application workaround.

Final combined validation commands:

```text
rtk proxy env PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider --basetemp /tmp/alpaca-native-attempt-full-20261022 tests
rtk proxy env PYTHONDONTWRITEBYTECODE=1 .venv/bin/mypy --strict --cache-dir /tmp/alpaca-native-attempt-full-mypy src tests scripts/qualify_risk_inputs.py scripts/run_paper_engineering.py scripts/reconcile_paper_orders.py scripts/configure_paper_credentials.py
rtk proxy env PYTHONDONTWRITEBYTECODE=1 .venv/bin/ruff check --no-cache src tests scripts/run_paper_engineering.py scripts/qualify_risk_inputs.py
rtk proxy env PYTHONDONTWRITEBYTECODE=1 .venv/bin/ruff format --check --no-cache src tests scripts/run_paper_engineering.py scripts/qualify_risk_inputs.py
```

Frozen SHA-256 values: native attempts
`263ad33fe4d2d8b05adbf9e5fc078a818353c1ff28877555d372ba64fa531870`;
journal `d2e0253efb85778e13311f8151e2056c54f426af12c6464f2324633201f2d5e8`;
focused tests `f40acd3521dbbbfb6c7377fd541353b7c76171c201eeffca09ec6918a55d9288`;
independent integration tests
`1e2875ac07746b766d3d04bc20debc5fb0aa89ba4d37da7f3ed9e24fd403b64f`.

Actual-market qualification, real authenticated native POST/ACK ownership,
first/late-fill protection, sibling-stop continuity and safe timed close/runtime
remain open. W3-07 actual protected cycles and W3-08 final registration remain
open. No real account, network callback, order, cancellation or paper start was
used for this increment. This evidence validates a needed injected-test boundary,
not the requested W3 end state or an enabled sender.

## Source-admission requirement scope correction — 2026-10-22

A separate read-only requirements audit compared the October 19 accepted
observation-age manifest's retained requirements with the later source research
notes. It identified requirement inflation: native TOPS flags/clock equivalence,
absolute lossless delivery and a vendor-issued completeness watermark were not
accepted as universal prerequisites. The [controlling-scope correction](w3-data-admission-proposal.md#controlling-acceptance-scope--correction-2026-10-22)
withdraws that implication without changing either accepted manifest.

The valid next work is an independently reviewed policy for the selected
Alpaca-reported conditions, timestamps, historical ranges/pagination and
point-in-time revisions. From-open reconstruction, unknown-condition/known-gap
vetoes, required bars and the accepted age limits remain mandatory. No regular-
only shortcut, first-locally-observed substitute, finality delay or new whitelist
was adopted. Actual entry qualification remains unaccepted until its policy and
evidence pass; source-native unknowns no longer create an extra blanket refusal.
Existing research-component runtime flags are not a new native-equivalence
requirement and are not being toggled by this audit. This documentation
correction requires no order, new capture or broker contact.

## Reported-price eligibility decision package — 2026-10-22

The [concrete source-policy proposal](w3-data-admission-proposal.md#concrete-reported-price-policy-proposal--pending-acceptance)
is now independently reviewed as a decision package, not accepted execution.
It explicitly separates three new choices: mapping documented minute-price
eligibility to intraday strategy events, a known extended-hours-condition/RTH
conflict veto for the affected symbol/derivation, and extending the existing
valuation-only regular quote profile to entry. Existing ages, raw history and
unknown-condition vetoes remain unchanged. The review corrected an overbroad
timestamp-tie refusal: only order-sensitive ambiguous derivations are blocked;
commutative lows need no invented event ordering. It also made the proposed
RTH conflict veto explicit rather than implying inherited acceptance.

Independent final review found no remaining material omission preventing a
user decision in this scope. Exact condition matrices and reconstruction
mechanics still require implementation review and validation after policy
acceptance. The observation-age and OCO-first manifest SHA-256 values remain
`f5439d49ac9354abbe56b7b1fcb603c915942dee647efedebf48060e63b49e8c`
and `fa6e29ede5a92428308ad19ba794be5e6e3f0b2a3c216818a54f3ffc9a112d80`.
`rtk git diff --check` passed. This documentation-only increment reuses prior
source research and test evidence; no test rerun, capture, account operation,
policy acceptance or W3 parent completion is claimed.

## Declared review receipt workflow — 2026-10-22

The W3-08 local review receipt chain is implemented in
`src/alpaca_ross_bot/declared_review.py`, with only additive Journal namespace
wrappers. PACKAGE → EXPECTED → UNMASK → COMPARISON preserves exact bounded
strict-JSON bytes and original receipt clocks. Expected records bind declared
calculator/version/digest, worksheet and reviewer statements. Unmask receipts
pin a scoped bot reference and exact observed-document hash. Comparisons retain
declared per-rule text equality, mismatches and abstentions without computing
trading rules. Exact retries verify source/projection/dedupe consistency under
the write lock; changed steps persist a conflict and invalidate the current
verdict, while earlier prefixes retain original results.

Independent review corrected retry-cache bypass, missing pre-unmask declaration
binding, future local reference admission and ambiguous current conflict
results. Five separate integration tests verify cross-connection visibility of
committed seals, physical reopen/original retries, immutable historical results,
forged external-reference boundaries and rollback after an injected write error.
Caller-owned transactions are refused rather than rolled back by this writer.
No remaining material finding was reported within this declared-only scope.

Final combined verification:

```text
rtk proxy env PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider --basetemp /tmp/alpaca-declared-review-final-20261022 tests/test_declared_review.py tests/test_declared_review_integration.py tests/test_journal.py tests/test_paper_report.py
rtk proxy env PYTHONDONTWRITEBYTECODE=1 .venv/bin/mypy --strict --cache-dir /tmp/alpaca-declared-review-final-mypy src/alpaca_ross_bot/declared_review.py src/alpaca_ross_bot/journal.py tests/test_declared_review.py tests/test_declared_review_integration.py
```

The combined suite passed **98 tests in 1.89 seconds**; strict mypy passed all
four files. Scoped Ruff and format checks passed. Existing unrelated full-suite
evidence is reused, not represented as a new full-suite run. Final SHA-256:
module `c9573ccee742ebe037fa6f7a85740e361d3cf331d821f03724eedd5b3a062555`;
Journal `cb70fe047168f151ca7071882f12712211909989009923efbde0939374bd5025`;
focused tests `bac914c9136d2992847a6441f9dfe2a8ebf79cb02461a74f9d80a520b51e0a02`;
independent integrations `c45f80d2a172fe64be7c8385370febff003827685cba0154ab26b7256736412a`.

Cloud `gemma4:31b` responded to a public generic append-only review-workflow
question; no local documents or private/account inputs were exported. A missing
optional `python` alias affected one fixture-update command; using the existing
project `.venv/bin/python` verified the operation without global changes.

This is durable local recorded order, not proof of actual masking, reviewer
independence, external-source authenticity or unseen natural enrollment. All
such certifications, formal eligibility and runtime authority remain false.
Actual package construction/masking, independent calculations, reviewer
assignment, prospective origin/enrollment, executable dependencies and final
freeze remain open. No source-policy acceptance, account operation, broker
action or formal paper start occurred; W3-06/07/08 parents remain open.

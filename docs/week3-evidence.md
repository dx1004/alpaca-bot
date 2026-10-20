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

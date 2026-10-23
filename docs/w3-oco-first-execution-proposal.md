# OCO-first execution feasibility proposal

Status: **USER_ACCEPTED_SEPARATE_RESEARCH_CANDIDATE**, 2026-10-20
America/Los_Angeles. The user selected “Allow a separate OCO-first research
candidate”. The [pinned research manifest](../evidence/oco-first-execution-research-v1.json)
defines `OCO_FIRST_EXECUTION_RESEARCH_V1` and candidate
`HYBRID_500_IEX_OCO_FIRST_RESEARCH_V1`, accepted from
`OCO_FIRST_EXECUTION_RESEARCH_PROPOSAL_1`.
Acceptance covers the separate candidate and provisional early-target behavior;
it is not an executable release, protection/close qualification or permission
for broker requests, formal paper or live trading. The original candidate and
its P11 guard remain preserved. Existing protection requirements remain in force.

## The change in architecture

Start with native OCO protection for already owned quantity instead of creating
a standalone stop and later trying to convert it into OCO. Subsequent target
changes would amend the existing OCO take-profit leg. No deliberate cancellation
of an established valid stop is part of this proposed path.

This is an independently accepted research direction with unresolved contracts
and a material exit-timing difference. It must not be represented as an
equivalent implementation of original P11 or as a Ross rule.

## Source-supported parts and limits

The [wash-protection guide](https://docs.alpaca.markets/us/docs/user-protection)
exempts OCO/complex orders from its ordinary wash check. The
[order guide](https://docs.alpaca.markets/us/docs/orders-at-alpaca#oco-orders)
supports OCO exits for existing positions and price amendment. These are useful
building blocks, not proof of the complete proposed partial-entry lifecycle.

The [replacement reference](https://docs.alpaca.markets/us/reference/patchorderbyorderid-1)
does not promise successful venue replacement from HTTP acknowledgement. Its
unchanged-attribute wording concerns the replaced order, not a specific guarantee
for its sibling stop. Advanced stop/target separation restrictions also constrain
a marketable target amendment. No documented equity reduce-only guarantee is
assumed. Multiple disjoint OCO reservations and exact fractional-residual support
remain qualification requirements, not established facts.

## Proposed lifecycle for evaluation

1. Keep the original source/risk/entry qualification and one-entry-attempt policy.
   On an observed first fill, durably request cancellation of the unfilled entry
   remainder and own all racing fills under the canonical execution ledger.
2. For new owned quantity, propose an OCO with stop `S` and provisional target
   `U = ceil_cent(3L - 2S)`, using the accepted entry limit `L`. Record intent
   before sending, and reconcile unknown acknowledgements before any retry.
3. A later fill would use a separate, disjoint owned-quantity allocation instead
   of resizing or canceling an established stop. Never round a fractional
   residual up or down to make it fit an unverified order interface.
4. Once entry is terminal and all entry executions reconcile, derive actual
   average `F` and final target `T = ceil_cent(3F - 2S)`. Propose amending each
   remaining group's take-profit to the same `T`; retain every valid stop.
5. A timed close could amend an existing take-profit to a marketable limit only
   when supported price constraints and observed liquidity permit it. Preserve
   protection on rejection/uncertainty. This is **not** a complete deadline
   disposition: when bid is at/below `S`, a limit above `S` cannot be marketable.

These are research steps, not an enabled dispatcher or fully qualified incident
policy. An early provisional exit does not authorize a replacement entry: own
and reconcile any racing original-entry fills, retaining the one-attempt rule.
The final average includes every entry execution, not just shares still held.

## What the arithmetic proves

If every entry execution respects `price <= L`, then `F <= L`. Monotonic cent
ceiling gives `U >= T`. Thus a sell-limit execution at or above `U` is not below
the eventual gross target price bound. This proves a price inequality only;
it does not authorize that execution time, prove allocation safety or preserve
broker protection.

The [exact arithmetic evidence](../evidence/w3-oco-first-feasibility-20261020.json)
contains a deterministic rational-value check and two counterexamples. It has no
account inputs, broker calls or acceptance authority. The general proof is the
monotonicity argument, not an assertion that finite cases establish all behavior.

## Irreducible differences from the current policy

- A provisional target can execute before entry termination/reconciliation.
  This behavior is now accepted for the separate research candidate only.
  The original incident owner explicitly rejects that state with
  `TARGET_BEFORE_ENTRY_TERMINAL`. A better exit price does not make the timing
  equivalent. An early group exit can coexist with later racing entry fills.
- First-fill detection, persistence, submission and broker acceptance take time.
  New late fills likewise need protection. This proposal does not eliminate
  those intervals or classify them as verified continuous coverage. It does not
  impose an invented promise of zero network latency either.
- Separate groups require exact fill-level ownership, reservation and recovery.
  Planned integer sizing does not prove that each partial execution is integer.
- Target-only replacement still needs evidence that its companion protection
  remains valid through replacement/rejection/fill races.
- A marketable-limit amendment cannot cover every deadline/price topology, halt
  or liquidity state. Retaining a stop is not the same as proving an intraday
  liquidation path; no silent overnight-holding adaptation is accepted.

## Qualification sequence and stop conditions

| Stage | Required evidence | Current state |
| --- | --- | --- |
| Algebra and policy comparison | Exact bound, early-exit and close-topology counterexamples | Completed; not equivalent to current P11 |
| Independent research version | Separate candidate/provisional early-exit decision; other qualification retained | User accepted research scope only; source/protection/close remain unqualified |
| Deterministic owner/model | Durable group allocation, timeout recovery, no double reservation and all six incident groups | Local source-derived incident receipts, durable entry allocation and six synthetic fault groups verified; native protection/exit mapping, qualified recovery and continuous runtime acceptance remain open |
| Broker mapping | OCO during pending entry/cancel, group reservations, exact residuals, target amendments and stop preservation | Unverified |
| Bounded isolated API component experiment | Component-specific account/source prerequisites, reviewed request manifest, independently safe supported cleanup and restart path | Full strategy/data acceptance is not a universal prerequisite for every component observation; this document authorizes no dispatch or unprotected partial position |
| Formal paper preregistration | Accepted release and all existing W3/W4 requirements | Blocked by prior stages |

The provisional early-exit decision has been made for this separate candidate.
It does not close broker/cleanup/data gates. Implementation must bind the new
research manifest explicitly; it cannot enter the original runtime by calling
the changed behavior an engineering-only change. This exit-allocation book is
not a second cash/loss-risk allocator, and no entry-risk version binding is
inferred from sharing the parent research inputs.

### Controlling protection scope and component-test dependency — 2026-10-22

P11 requires protection of actual partial fills at frozen S and reconciliation
of racing entry fills; P13 preserves valid protection under uncertainty. The
user's rejection of a cancel-confirm-submit protection gap remains controlling.
No numerical activation grace period or zero-network-latency promise follows
from these requirements. Lack of a specified latency does not approve an
unprotected interval.

Atomic standalone-stop conversion and atomic growth of an established stop
belong to the earlier architecture, not every accepted OCO-first allocation.
Disjoint allocations may avoid those operations. They still need a supported
initial/late-fill protection mapping and exact residual/reservation eligibility.
Likewise, the incident contract explicitly requires handling double exits;
it does not require proof that broker races can never occur. Observed stop
coverage, arithmetic and matching group fields do not prove earlier continuity.

Do not require full W3-06 acceptance before every independently safe component
experiment needed to gather W3-06 evidence. Each such experiment needs its own
reviewed account/source scope, request/stop conditions, crash/restart handling
and supported protection-preserving cleanup before execution. No current
enabled broker-writing path is established here. Deliberately creating an
unprotected partial position remains prohibited; a component result cannot
qualify P11 or bypass the W3-07 full-cycle dependencies. The actual unresolved
mapping remains initial/late-fill protection, residual/group reservations,
sibling-stop preservation during amendment, and safe close/crash cleanup.

The research-only [allocation model](../src/alpaca_ross_bot/oco_research.py)
and [synthetic tests](../tests/test_oco_research.py) now implement exact lot
portions, unknown-acknowledgement retention, signed over-exit incidents,
all-entry averages and terminal-census invalidation. Its observations and
acknowledgements are declared inputs, not source-qualified facts. A private
[durable adapter](../src/alpaca_ross_bot/oco_journal.py) now preserves the model,
unknown reservations and identity conflicts across restart. The shared risk
owner separately records an explicit execution-contract opt-in while retaining
the parent entry signal and original budget. A local entry receipt now binds the
original positive held reservation and immutable source-execution census under
the private journal lock. Explicit candidate incident opt-in and six synthetic
integration cases are verified. Receipt origin remains engineering-only and
source admission remains false. A separate GET-only native OCO collector and
interpreter now retain account-bound nested group and per-execution activity
observations, including transport failures, bounded pagination and temporal
conflicts. These observations are not yet bound to local exit intent/ACK
ownership; the allocation book's native exit/group facts remain declared.
The [prepared-request adapter](../src/alpaca_ross_bot/native_oco_requests.py)
now pins exact OCO request bytes to the existing source-bound entry, positive
reservation and disjoint allocation at one verified historical prefix. It keeps
original provenance on retry and separates noncolliding identity conflicts from
attempts to reuse another request's client ID. The ordinary intent primitive
also checks these private native identities under its existing write lock.
Preparation creates neither a dispatch-compatible intent nor a send attempt;
source-to-ACK ownership and broker acceptance remain unqualified.
continuous runtime supervision, qualified completed-slot recovery and actual
broker protection/close acceptance remain open. There is no broker dispatcher;
every operational authority remains false. Local source-derived incident receipts
now retain scoped lifecycle/position facts and quarantine completed-scope late
fills without restoring risk or accepting native protection. Manual and
source-derived observations cannot mix in one scope. Implementation verification
and its limits are recorded in
[Week 3 evidence](week3-evidence.md#w3-06-source-derived-incident-observations-and-completed-scope-quarantine--2026-10-20).

No support submission is required by this proposal. Only the new research
acceptance scope is granted. Credentials, runtime authorization, the original
production gates and broker state remain unchanged.

## Native request/ACK provenance integration contract — 2026-10-22

This is the next implementation contract, not an accepted broker mapping or
authorization to send requests. Reuse the existing OCO allocation book and
canonical account-risk slot; do not create a second sizing or risk owner.

1. A private request record must predate dispatch and bind the exact candidate
   manifests, account, entry scope, positive reservation origin, source-bound
   entry census and disjoint group allocation. Derive quantities and provisional
   prices from that frozen prefix, never from the caller's claimed position.
   Preserve exact request bytes and their client ID. Freeze a historical owner
   view; later fills, target changes or terminal reconciliation must not rewrite
   an earlier request. A historical read must verify current immutable/cache
   integrity and reject unavailable prefix evidence without mutating state.
   Historical absence of a later conflict is not current clearance: all current
   source/risk/conflict/protection vetoes still apply to any future dispatch.
2. Persist a single dispatch-attempt marker before any future transport call.
   A prepared request is not a dispatch attempt; a matching GET is not proof
   that this process sent it. Keep request provenance, attempt, HTTP response,
   observed broker identity and protection acceptance as separate facts.
   This implementation stage does not provide a dispatcher.
3. Correlation requires fresh same-account authentication, exact root client ID,
   symbol, side, requested quantity, order type/class, time in force and prices.
   Bind root/stop IDs and group ID from the retained nested response, without
   inventing equality or derivation rules for the child client ID. A historical
   correlation receipt pins the original request, response/capture hashes,
   source horizon and decision time; later observations cannot silently upgrade
   or reinterpret it. Replacement links require qualified ancestry, not summed
   snapshots. Unknown status does not imply an accepted/live stop.
4. A timeout, missing ACK, HTTP rejection or lookup miss retains the reservation
   and uncertain attempt. It cannot permit another POST. Changed request or
   broker identities under an existing key must retain the original evidence
   and latch a conflict across restart. Distinct requests cannot share a client
   ID, broker root/stop ID or broker group assignment.
5. Only a verified mapping may associate immutable execution activities with
   local lot portions. Deduplicate exact activity IDs; retain conflicting,
   fractional and signed over-exit facts. Do not round them or release the
   account-risk slot from capture-local quantity agreement. Recovery of a
   completed scope must still preserve the existing quarantine boundary.

Required integration evidence includes lost ACK, duplicate/conflicting identity,
cross-account/scope collision, changed/future/backdated source references,
late-fill replay, physical restart, two-connection races and transaction
rollback. A matched group still does not prove initial/late-fill stop coverage,
sibling-stop continuity or safe timed liquidation. Those remain separate W3-06
requirements before W3-07 actual order cycles; none is waived by this contract.

### Prepared-request to GET association implemented — 2026-10-22

`native_oco_correlation.py` now records an immutable association receipt against
the original prepared request and a retained nested GET capture. Historical
prefix reads, first broker assignments, changed-command conflicts and rollback
are preserved independently of later observations. Quantity comparisons describe
only the captured fields: a reported residual after target fills is not proof
of continuous stop coverage, broker reservations or execution ownership.

`PaperOrderHttp.read_native_oco_by_client_id` provides a raw, one-request GET
lookup without changing the ordinary simple-order sender. Association explicitly
keeps `send_proven=false` and `ack_proven=false`. Native dispatch-attempt and raw
POST/ACK provenance, qualified execution ownership, protection continuity and
safe close/runtime acceptance remain open. No dispatcher or broker authority is
introduced by this increment.

### Injected attempt boundary implementation scope

`native_oco_attempts.py` implements the contract's durable attempt and
raw-outcome boundary with an explicitly injected test callback. It has no default
network transport, credentials, CLI or broker-client wiring. Every observation
is labeled `INJECTED_TEST_CALLBACK_NOT_BROKER_PROOF`; callers cannot relabel it
as actual broker evidence. The marker describes committed pre-call intent,
not proof that any bytes reached a broker. Raw response matching grants no ACK,
ownership, protection or runtime acceptance. Unknown/crashed outcomes consume
the attempt without replay or allocation release. Caller-owned transactions
must be rejected before callback execution, rather than committed on the
caller's behalf. The existing ordinary-order sender remains separate.

The integrated validation is recorded in
[Week 3 evidence](week3-evidence.md#injected-native-oco-precall-and-raw-outcome-provenance--2026-10-22).
This completed engineering increment does not implement real POST/ACK adoption
or qualify actual protection, close or runtime behavior.

## Targeted protection research follow-up — 2026-10-22

The user asked to research the remaining partial-fill, continuity and
close/crash gaps. This follow-up reuses completed engineering and prior source
inventory; it creates no new trading policy or experiment authority.

| Specific route | Current primary-source support | Exact unresolved contract |
| --- | --- | --- |
| OCO for newly owned partial quantity while entry cancellation resolves | The [retail protection guide](https://docs.alpaca.markets/us/docs/user-protection) explicitly exempts complex OCO orders from the ordinary wash check. | Exact pending-entry admission, disjoint group reservations and every actual residual quantity still need qualification. The simple stop-sell rejection table is not a universal OCO prohibition. |
| Fractional residual | The [fractional guide](https://docs.alpaca.markets/us/docs/fractional-trading) and order-type table support several simple DAY order types. | Simple fractional support does not prove fractional OCO class support; conflicting create-reference wording is not silently resolved in favor of admission. Do not round residuals. |
| Amend existing OCO target only | The [order guide](https://docs.alpaca.markets/us/docs/orders-at-alpaca#oco-orders) supports price replacement. | The [PATCH reference](https://docs.alpaca.markets/us/reference/patchorderbyorderid-1) does not establish sibling continuity or final replacement from HTTP success. Preserve old protection and reconcile old/new identities and executions. |
| Close with an existing protective OCO | [Single-position close](https://docs.alpaca.markets/us/reference/deleteopenposition-1) creates a closing order and accepts fractional quantity precision. | It does not document a reservation bypass or protection-preserving handoff. Bulk cancel-then-close is not a continuity solution. |

### New admission counterexample affecting initial and late fills

The advanced-stop rule uses both the OCO target and current market price as
sell-stop bases. A newly submitted fixed stop must be at least one cent below
the applicable base. For an explicit hypothetical broker market base 9.49,
fixed S=9.50 and target 11.00, target separation is valid but market-base
separation fails. A partial entry can therefore be observed after price has
already crossed S without a valid new fixed-S OCO admission under the documented
constraint. Raising its target does not repair that failed market-base check.
This affects protection of a new lot, not just end-of-day liquidation.

The [exact rational cases](../evidence/w3-protection-route-research-20261022.json)
also show a nonempty conditional marketable-target region and a cent-grid
counterexample below S+0.01. The broker's market base is an explicit hypothetical
input, not asserted equal to an IEX bid. These are documented-constraint checks,
not actual broker observations or proof that every PATCH revalidates identical
constraints. They refute universal admission through this route, not every
possible broker architecture.

### Recovery behavior and evidence to qualify next

| Observed interruption | Required behavior retained | Evidence still needed |
| --- | --- | --- |
| Initial OCO POST timeout/crash | Keep durable allocation/attempt; do not repeat POST from timeout or lookup miss. | Actual authenticated send/response ancestry and exact group/execution mapping; existing injected tests are reused, not promoted. |
| Target PATCH unknown or rejected | Preserve valid stop, retain original/future replacement identity and reconcile; HTTP success is not completion. | Old/new order lineage, both legs and execution stream plus GET observations through replacement/fill races. A successful sample does not establish all-time continuity. |
| Late entry after an early exit or completed scope | Retain exact ownership, invalidate terminal census and quarantine rather than restore an entry budget. | A supported protective action for every new residual, including price already through S; local ownership arithmetic cannot supply it. |
| Deadline or crash cleanup with reserved protective quantity | Do not cancel valid exits to force a second independent close; do not assume DELETE is atomic or immediately flat. | Supported close/reservation behavior, recovery of unknown results and flat/orphan reconciliation. No overnight adaptation is inferred. |

An isolated target-amendment experiment is a possible *component* investigation,
not presently a runnable full-cycle test. Before dispatch it needs an already
qualified, owned protected seed, source/account checks and a reviewed safe
cleanup/restart envelope. Do not manufacture the seed through an unprotected
partial entry or treat repeated rejection probes as positive protection proof.
First/late-fill admission when price is already through S is the critical
research item; another fake dispatcher, a successful target amendment or a
whole-share-only observation cannot close it. No broker request was made and
no contact with Alpaca was sent or made a universal prerequisite.

## Crossed-stop new-fill disposition decision — reviewed acceptance, 2026-10-23

Proposal ID: `CROSSED_STOP_UNCOVERED_RESIDUAL_EXIT_PROPOSAL_1`.
Status: **ACCEPTED_FOR_SEPARATE_IMPLEMENTATION_AND_VALIDATION_ONLY**.
The user authorized confirmation conditional on a clean review on 2026-10-23.
The review corrected the session-eligibility omission below; independent final
review is recorded in Week 3 evidence. This accepts the explicitly disclosed
uncovered interval solely for this independent emergency-disposition version,
not as equivalent continuous protection. The original candidate and the pinned
OCO-first manifest remain unchanged. Bind a separate version before implementing
this behavior; this decision is not an executable release or broker authority.

### Source result and excluded shortcuts

The advanced-order check constrains stop_price itself; changing a new OCO's
stop-limit execution price does not establish admission at crossed fixed S.
For a working limit buy at L, the [wash table](https://docs.alpaca.markets/us/docs/user-protection)
rejects a new sell limit when its price is at or below L. A sell limit above L
is not an immediately executable exit when bids are below S<L. Trailing-stop
exemption supplies no independently frozen-S threshold. The
[retail create schema](https://docs.alpaca.markets/us/reference/postorder)
lists sell_to_close intent but documents no wash/reservation exemption or
reduce-only guarantee; SDK acceptance is only syntax evidence.

The [single-position close endpoint](https://docs.alpaca.markets/us/reference/deleteopenposition-1)
does not supply those guarantees either. Published Alpaca staff explanations
([2020-12-03](https://forum.alpaca.markets/t/cant-sell-my-aal-stock/3512),
[2025-08-27](https://forum.alpaca.markets/t/alpaca-py-apierror-insufficient-qty-available-for-order/17712))
describe available quantity after outstanding sell reservations. They support
investigating a genuinely available uncovered residual, not canceling old
protection to sell it. The later thread's multiple-OCO conclusion is another
user's reply, not an additional staff guarantee. No public native route was
found proving immediate fixed-S protection of the new quantity while the buy
remainder is unresolved. This is a bounded research finding, not proof that
every possible broker architecture is impossible.

### Concrete independent emergency rule for review

1. On a source-observed new owned fill that lacks verified protection, use a
   fresh source-qualified reported IEX bid at or below frozen S as the proposed
   emergency trigger. This is an explicit incident-policy choice, not proof of
   Alpaca's native stop election or equality with its market-base price. Missing
   or stale quotes remain unknown; do not invent a crossing. Once triggered,
   retain the incident across later rebounds. Stop new exposure and retain S.
   Preserve every valid protective order for older quantities. Do not lower S, create a
   trailing substitute, round residuals, reset an attempt or release risk.
   Unverified protection triggers an incident; it does not establish freely
   closeable or unallocated shares.
2. Retain the existing durable entry-cancel request and unknown-result rules.
   Do not infer terminality from a cancel acknowledgment, timeout or lookup
   miss. Continue owning all racing and late fills.
3. Before a residual-close intent, require fresh same-account evidence of a
   terminal entry, no other conflicting working/uncertain buy, reconciled
   per-execution ownership, current position and existing/unknown exit
   allocations. Isolate the exact new uncovered quantity using the canonical
   ledger. Broker-confirmed available quantity must accommodate it; a mismatch
   is an incident, not permission to cancel older exits or guess a smaller lot.
   Exclude every portion held by an existing or uncertain exit allocation,
   including UNKNOWN ACK groups, until exact reconciliation resolves that
   reservation. Broker available quantity cannot override local reservations.
4. Propose only an explicit-quantity simple market sell for that uncovered
   quantity, with its own durable client ID, original lot attribution and
   single-attempt marker. This is a candidate broker mapping, not already
   verified admission. Do not use whole-position/percentage liquidation.
   Require fresh official calendar/clock and source-qualified asset/session
   eligibility at any future dispatch: this route is restricted to the regular
   session in which a market order is eligible to execute. Closed, unknown,
   stale or halted states retain the incident; queued next-session orders are
   not emergency-disposition success. Do not infer extended-hours eligibility
   or an overnight policy. A pre-send check cannot guarantee execution before
   the session closes; that boundary remains a qualification/failure case.
5. Preserve partial exits and unknown acknowledgments across restart; reconcile
   the original client/order identities before further action. Newly observed
   late fills invalidate an earlier complete census and require fresh disjoint
   attribution. Never resubmit an uncertain close or spend its reserved shares
   twice. Insufficient availability, ownership conflict, halt or rejection
   retains incident status and existing valid protection; it is not recovery
   clearance or automatic authority to alter those orders.

### Accepted material difference and retained qualification gates

This rule **does not give the new quantity broker-stop coverage while waiting
for entry cancellation/reconciliation and market-exit execution**. Published
interfaces supply no maximum duration or execution-price guarantee for that
interval; a process crash can prolong it. Existing protected quantities remain
protected, but that does not make the new quantity continuously protected.
The rule therefore changes the retained protection acceptance for this emergency
state. It cannot be implemented as a mere clarification or marked equivalent to
P11. There is no approved grace period or automatic overnight policy.

This conditional user acceptance permits a separately pinned research/implementation version
of this emergency rule, with the exposure interval explicitly accepted as a
research-policy difference. Actual broker admission, safe cleanup, source/risk
binding and release still require independent validation; acceptance authorizes
no orders or formal paper. The original continuous-protection candidate stays
blocked on its own mapping. The independent version also stays broker-disabled
until its version binding, deterministic fault tests, broker eligibility and
all remaining source/risk/protection/release gates pass. Review acceptance does
not establish that any actual market exit will be admitted or filled.

### Independent declared implementation verified — 2026-10-23

The pinned `CROSSED_STOP_RESIDUAL_EXIT_RESEARCH_V1` manifest is
[separate from its unchanged OCO parent](../evidence/crossed-stop-residual-exit-research-v1.json).
`crossed_stop.py` reuses the existing OCO lot book and private Journal to persist
a sticky declared crossing, exact disjoint residual preparation, a single local
attempt fence and declared partial exit executions. Original and conflicting
retries remain idempotent across restart; UNKNOWN OCO and emergency reservations
cannot be reused. A latched incident blocks new OCO allocations without changing
old groups or their acknowledgments. A changed parent census after preparation
blocks its attempt; its old reservation remains held, with no automatic release.
The target-only `FINAL_TARGET_TOPOLOGY_INVALID` condition does not prevent an
exact risk-reducing emergency proposal; the parent incident, S and invalid target
remain unchanged, and ownership/quantity incidents still veto.

This is a **declared-input research component**, not a source-qualified or
dispatch-compatible order path. Freshness/session/ownership declarations are
explicitly labeled and cannot grant source acceptance. No new freshness TTL or
network transport is introduced. The exit execution overlay is not an accepted
broker execution/risk-release owner. Actual source and risk-version binding,
native admission, executable crash cleanup, session-boundary behavior and release
remain required; old protection is never canceled by this component. See the
[implementation evidence](week3-evidence.md#crossed-stop-independent-decision-and-durability-implementation--2026-10-23).

### Immutable source-referenced crossing implemented — 2026-10-23

`CrossedStopJournal.observe_source` now derives the crossing without caller
bid/freshness/ownership flags. It joins the existing source-bound entry and
original positive shared-risk slot to the complete decision-visible risk prefix,
its selected raw IEX quote/metadata, and the account-bound official calendar.
The receipt retains the exact original horizon, quote index/hash, entry census
hash and risk/reservation/calendar references. Both official open and close are
used; early closes and New York timezone conversion are not replaced by a fixed
session end. New local quotes can invalidate the older selected quote.

Source and declared modes cannot mix within one scope. In particular, a source
trigger cannot reach the declared preparation/attempt API by supplying boolean
clearance. Source risk veto and daily-stop diagnostics are retained, not cleared;
a fresh crossing can be recorded as an incident without authorizing new risk.
Equal cumulative quantity is insufficient: current entry execution IDs, exact
quantities/prices and timestamps must still match the bound census. A changed
or advanced census requires rebind before a new observation.

This completes the source-reference observation connection only, verified with
synthetic immutable captures. It does not accept whole-source admission, native
exit ownership/reservations, fractional entry seams, the emergency version's
operational risk binding or real broker disposal. No actual account/capture was
accessed. [Evidence and retained gates](week3-evidence.md#crossed-stop-immutable-source-trigger-binding--2026-10-23).

### Explicit same-risk-slot policy binding implemented — 2026-10-23

The existing `AccountRiskOwner` now accepts an explicit pinned
`CrossedStopContract` opt-in for its currently held positive OCO-origin slot.
This records the independent emergency identity, exact original signal hash,
frozen L/S/quantity and immutable prior-risk/binding references. It does not call
the reservation allocator, change fees or quantities, clear old reasons/vetoes,
consume another signal, create an order intent or grant runtime authority.
Binding adds `REFRESH_REQUIRED`; normal source refresh and all other gates remain
necessary. A historical command retry does not claim that a completed slot is
currently bound. Concurrent identical-policy attachments retain the first receipt.

Source-trigger receipts can now join that explicit policy reference to the same
original reservation and entry evidence. Optional fields are absent for earlier
unbound source prefixes, preserving their replay shape. Source observations still
cannot use the declared preparation API to bypass unaccepted exit ownership or
source/risk qualification. This closes an implementation connection, not the
operational risk/protection acceptance gate. [Validation and limits](week3-evidence.md#crossed-stop-explicit-shared-risk-policy-binding--2026-10-23).

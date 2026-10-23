# W3-08 — Four-week paper pre-registration

Status: **PRE_REGISTRATION_DRAFT — NOT ACCEPTED FOR LAUNCH**, 2026-10-15.
Owner: the user; operational incident notification: Codex. This document does
not start paper, authorize an order or close a prerequisite. W3-08 acceptance
requires the dependencies in [TODO](../TODO.md) and an independently reviewed,
fully populated freeze record. Subsequent W4 release gates still precede launch.

The user selected **engineering reliability and strategy-rule execution
consistency** as the primary objectives. Profit and loss are reported separately;
neither profit nor a profitability threshold can make this study pass.

## 1. Scope, version and calendar freeze

- Proposed calendar window: **2026-10-26 through 2026-11-20**, inclusive, using
  `America/New_York` exchange-local dates. The [development plan](development-plan-8-weeks.md)
  targets development completion on 2026-10-23. These are proposed dates, not
  evidence that a release will be ready. Before observation begins, freeze the
  exact exchange-calendar artifact, sessions, open/close instants and UTC
  conversions, including DST and shortened or closed sessions. Never assume
  that a weekday is an exchange session or every session has the same length.
- If prerequisites miss the proposed start, record **NOT_STARTED** and register
  a new complete four-week window before starting. Do not label a later partial
  window as the originally planned study. No late starts, make-up sessions,
  outcome-dependent extensions or stitching of releases are allowed.
- One accepted executable candidate and its exact enabled setup/timeframe scope
  run in one isolated formal-paper state namespace. Preserve the Ross comparison
  baseline separately. A hybrid is assessed against its own specification and
  must never be reported as Ross-equivalent.
- Current input scope is Alpaca Basic/IEX, Alpaca paper and separately qualified
  Alpaca news. Every decision, order and report carries **NOT_SIP_EQUIVALENT**.
  The census describes observed coverage, not all-market discovery. Neither
  Webull nor paid SIP may be introduced during this window.


The proposed window now has a source-checked
[expected calendar artifact](../evidence/w3-paper-calendar-20261026-20261120.json):
20 scheduled full regular sessions, checked 2026-10-16 against NYSE/Nasdaq
exceptions. New York hours are 09:30–16:00; UTC hours are 13:30–20:00 on
October 26–30 and 14:30–21:00 on November 2–20, following the November 1
DST transition. November 11 remains a scheduled equity session. This is a
draft expected calendar, not the final release freeze or a promise against
emergency closure. Revalidate broker calendar/clock and exchange notices before
launch, then bind the final calendar digest; a missed start still requires a
new complete four-week preregistration.

An authenticated [broker calendar check on 2026-10-20](../evidence/w3-paper-calendar-broker-check-20261020.json)
matched all 20 expected session dates and UTC open/close instants, including the
November DST transition. Its raw response remains in the existing bound private
journal. This supplies an actual as-of broker observation for the proposed
window; preregistration, release freeze and pre-launch revalidation remain
required. The October 16 expected artifact and its digest were preserved.

The freeze record must bind SHA-256 digests or immutable version identifiers for:
the protocol; executable strategy and risk specifications; source/rule registry
and attribution labels; accepted candidate manifest; enabled paths and disabled
paths; executable code/build and dependency lock; data/clock/calendar/condition
policies; account capabilities, fees, funding and reservations; incident policy;
database schema; fidelity oracle and review rubric; engineering scenario cases;
and the report computation. Record the actual runtime build/config digests at
startup and every restart; mismatch prevents new exposure.

Paper cost policy selected by the user on 2026-10-16: reserve conservative
regulatory fee bounds from the reviewed official 2026-10-12 ordinary-retail NMS
schedule, even when Alpaca paper posts no regulatory charge. Bind the reviewed
policy digest and calculator version at release freeze. Preserve the original
broker fee ledger and raw paper P&L separately from the modelled charge ledger
and cost-adjusted paper P&L. Report their reconciliation explicitly; do not add
the same fee twice or present a modelled amount as broker-posted. Prospective
entry/stop bounds are reservations, not incurred charges. Replace them with
execution-derived daily fee-type aggregates only through the accepted ledger
reconciliation contract. This selects the study's cost convention; account
applicability, complete executions/charges, risk input qualification and actual
broker fees remain separately verified facts. The fee bound is not a slippage
or maximum-loss guarantee. No return threshold is introduced.

Also freeze the account identity reference, verified paper endpoint, reconciled
$500 starting state, no-borrowing boundary, zero inherited engineering inventory,
no orphan orders, and initial risk/capital-stage state. Store account details
and raw broker evidence only in protected local storage; the public record uses
opaque references and digests. Engineering test activity cannot enter the
formal ledger or its starting equity, loss streak, samples or returns.

Account policy confirmed 2026-10-16: preserve the existing paper account and
its history; obtain a dedicated USD 500 paper account for this study. The
[official paper documentation](https://docs.alpaca.markets/us/docs/paper-trading),
checked 2026-10-16, supports creating an additional paper account using the
dashboard account menu's **Open New Paper Account**, then generating keys for
that new account. No deletion of the existing account is required by these
instructions. Documentation alone did not establish the current balance
control or available slots. The user confirmed on 2026-10-16 that the dialog allowed USD 500 and
the dedicated account was created. GET-only evidence captured at 19:31:34 UTC
verified a distinct account, ACTIVE/USD flags clear, cash/equity USD 500 and
empty positions/open orders; see the dedicated-account verification in
[Week 3 evidence](week3-evidence.md). This intraday snapshot is not the final
pre-session E0 or complete funds/fees/release qualification. Do not implement
separation
by resetting the existing account, substituting a local virtual balance, or
merely renaming a state directory. Bind the dedicated account's own verified
identity and credentials to its protected state; reject credentials bound to a
different account. Creating a separate account does not waive the empty-order,
reconciliation, actual funding, fee-policy or pre-session E0 gates above.

## 2. Preconditions and remaining registration fields

Before W3-08 is accepted, attach evidence of an accepted executable candidate,
W2-08 and W3-02; before formal launch also attach W3-06, W3-07 and the complete
W4/G03–G06 release acceptance. A draft or research specification cannot satisfy
an executable acceptance. The [accepted intraday research specification](no-leverage-pullback-research.md)
remains subject to its independently documented data, fee, risk and execution
qualification; this protocol introduces none of its missing semantics.

Registration remains incomplete until the following fields have actual values:

| Required field | Completion evidence |
| --- | --- |
| Window ID, start/end, calendar digest and session list | Immutable pre-launch record |
| Candidate/build/risk/data/incident versions and digests | Accepted release manifest |
| All enabled setup/timeframe cells and evaluation instants | Enumerated coverage matrix |
| Natural candidate identity, stage and deduplication definition | Accepted scanner/signal event contract |
| Input availability and permitted clock-age rules | Accepted source-scoped data contract |
| Six engineering scenario fixture IDs and expected assertions | W3-06/W3-07 evidence references |
| Independent reviewers, oracle/rubric and case enrollment | Frozen fidelity review record |
| Account baseline and protected evidence location | Reconciled pre-launch snapshot |
| Report fields, denominators and close/cleanup path | Reviewed computation and incident policy |

No unresolved field can be filled from observed paper returns. If an accepted
candidate differs from the current research candidate, revise and independently
review this draft before preregistration; never silently transfer its parameters.

The separately accepted
[OCO-first research candidate](../evidence/oco-first-execution-research-v1.json)
has a material provisional-target timing difference and is not an accepted
executable release. A freeze selecting it must bind its execution manifest
separately from the inherited entry/data/risk identities. Its fidelity oracle
must distinguish permitted provisional exits from original P11, include early
exit followed by late entry, verify the final target against all entry fills,
and check disjoint group ownership and protection-preserving incident duties.
The natural-candidate identity contract must explain group/episode attribution;
group IDs alone cannot establish extra natural candidates or a complete
filled-to-flat lifecycle. Engineering-purpose entry receipts remain excluded
from natural witnesses. Existing coverage minima and failure precedence remain
unchanged, and this note does not freeze any missing field or start a window.

## 3. Natural census and minimum coverage

Keep an append-only census of **every natural candidate** identified under the
frozen input/scan contract, including duplicates by stable identity, omissions
known to the scanner audit, data-unmonitored matches, missing inputs, vetoes,
non-signals, accepted signals, submissions, fills and unfilled/canceled orders.
Do not retain only traded or profitable cases. Record every planned session,
including no-signal days, outages and unavailable periods. Supply denominators
at each stage; missing observation is not a negative strategy example.

Let `K` be the number of distinct enabled setup/timeframe cells in the frozen
matrix. For each cell, require at least **one distinct naturally observed
entry-decision witness and one distinct naturally observed no-entry witness**,
each with complete decision-time inputs and independent fidelity review.
An entry witness must reach the accepted strategy/risk decision, even if the
broker does not fill it. A no-entry witness must demonstrate an applicable
frozen rule or veto; missing data alone cannot prove a setup's negative branch.
Thus the minimum is `2*K` distinct complete natural candidate traces, plus
at least **one naturally triggered, owned filled-to-flat broker lifecycle**
across the study. Count a candidate once; repeated scan observations are not
independent witnesses. Freeze `K` and the explicit cells before the window.

These are minimum **engineering coverage witnesses**, not a powered statistical
sample or evidence of an error-rate bound. Report actual counts and exposure
for every rule/transition and leave unobserved behavior explicitly unvalidated.
Rare fault branches are tested separately below; do not induce a trade or alter
a rule to reach a quota. If no signal, no fill or any required cell is missing,
the corresponding primary objective is **INCONCLUSIVE**. Do not lower minima,
substitute engineering fixtures or extend the window after seeing the shortfall.

## 4. Point-in-time evidence and independent fidelity holdout

For each decision retain raw-evidence references, source/feed, provider event
time, local first receipt, normalization/availability time, revisions/corrections,
required bars/quotes/trades/news, account/reservation snapshot, rule IDs and all
version digests. Use only inputs demonstrably available by the decision instant.
Later bars, revised news or subsequently repaired gaps cannot rewrite a decision.
Unavailable first-seen or clock semantics remain unavailable, never estimated
from a later download. Apply only accepted freshness/eligibility limits.

The holdout is the **entire prospectively enrolled natural candidate census**
for the frozen window, enrolled at first observation before knowing its return.
Cases exposed during research/development, including historical teaching cases,
cannot be relabeled as unseen. Prior study inputs can test engineering only;
new prospective cases cannot retroactively validate those historical cases.

An independent reviewer uses the frozen rubric and decision-time evidence with
bot verdicts and subsequent prices/P&L masked, records the expected eligible,
ineligible or unavailable verdict and exact risk/size/exit calculations, then
unmasks the bot trace. Preserve all disagreements and abstentions. Use an
independent deterministic calculation or reviewed manual calculation rather
than treating the production implementation as its own oracle. Missing evidence
is an abstention and cannot count toward either natural witness quota.
Freeze the rubric before enrollment; no paper-driven relabeling is permitted.
The developer may monitor operational health but cannot use holdout assessment
or paper returns to tune this release. A semantic ambiguity that changes an
enabled decision is a failed gate, not permission for a favorable interpretation.

### Draft per-rule fidelity rubric — not frozen or assigned

This matrix maps the existing [P01–P13 policy](no-leverage-pullback-research.md#decision-package-independent-intraday-adaptation-2026-10-15)
to review evidence; it adds no threshold, enabled cell or executable acceptance.
Bind the complete selected manifest chain. For the separately selected
[observation-age candidate](../evidence/iex-snapshot-observation-age-research-v1.json),
apply its changed P03/P13 acquisition-age basis, including required catalog and
member pages. Cumulative-volume source update time remains unknown; recent
receipt cannot restore the original source-age claim. Unchanged parent rules
retain their definitions.

| Rule | Independent calculation or transition | Required evidence and comparison |
| --- | --- | --- |
| P01 — account/stage | Reconstruct frozen E0, actual account capability, whole-share/no-borrowing and single open/reserved-position constraints. | Bound account/date/E0, funds, owned inventory and canonical reservations; a stated starting balance is not account/risk qualification. |
| P02 — screen | Apply the pinned scanner price/RVOL/ranking/tie-break and non-veto preference rules independently. | Complete relevant catalog/request/return/missing census, raw current/prior/history inputs and contract digests; reported scanner results are comparison evidence, not the oracle. |
| P03 — monitored set | Rebuild reserved-symbol-first allocation, ranked remaining slots, completed-sweep admission and acknowledged handoff; use the selected age contract. | Raw request/member/catalog clocks, capacity, subscription ACKs and from-open first-trade/setup/low continuity. DATA_NOT_MONITORED is not screen rejection; repaired gaps do not prove continuous coverage. |
| P04 — features | Reconstruct expected RTH minute membership, completed open-anchored buckets and prescribed 100-bar EMA seed/recurrence using exact rational arithmetic. | Official calendar, raw as-observed minutes/daily closes, completion boundaries and revision availability; missing bars/minutes and later revisions cannot supply earlier features. |
| P05 — setup | Calculate daily/hourly comparisons, freeze the hourly pair, check the first eligible RTH trade and replay above-to-inclusive-zone arming. | Ordered eligible trades from open and exact indicators; IEX prices are not consolidated official opening/closing prices. Missing setup history cannot be inferred. |
| P06 — trigger/invalidation | Replay below-EMA21 invalidation, armed crossing above the previous completed bar high and the one-attempt rule consumed by durable intent reservation. | Eligible trade order, prior-bar identity, original intent horizon, cancellation/ACK and racing fills. Rejection/cancellation does not restore the allowance; cancel request is not terminal. |
| P07 — entry clock | Apply the prescribed one-/five-minute intervals, exact endpoints/calendar clipping and trigger discard at the switch. | Session open/close, precise decision clock and completed-bar boundaries; no invented enabled cell or endpoint tolerance. |
| P08 — limit | Calculate the next whole cent strictly above the trigger high; compare both triggering trade and contemporaneous ask with that limit. | Exact prices/grid, accepted condition policy and source/local clocks. Above-limit means the specified skip, never chase or market fallback. |
| P09 — stop/risk | Reconstruct the then-known RTH low, floor-to-cent frozen stop, valid topology and prescribed E0-based all-in loss cap. | From-open low history, limit/stop inputs, charge-policy revision and remaining-risk evidence; a later low cannot rewrite the stop, and stop execution does not guarantee the cap. |
| P10 — quantity | Solve both parent funds/loss inequalities for the largest feasible positive integer quantity, and verify the next integer is infeasible. | Exact spendable funds/holds, pending/open reserves, entry/stop charge bounds and original reservation prefix. Unknown charges/funds or no feasible integer rejects; an OCO allocation is not a new budget. |
| P11 — exits | For the selected execution version, calculate owned entry/exit quantities, target basis, cancellation duties, topology and calendar-close-minus-five-minute obligation. | Per-execution facts, terminal entry census, exact ownership, ACK/replacement chains, continuous protection and approved close path. Formula or ACK success does not prove protective continuity or safe close. |
| P12 — daily stop | Calculate net session P&L relative to E0, valid-bid open marks/accrued charges, daily allowance, pending/open reserves and remaining capacity; replay round-trip streak, latch and permitted date reset. | Complete owned ledger, funding/conflicts, marks/ages, fees, flat-to-flat attribution and orphan reconciliation. Zero does not reset streak; later profit cannot erase a dated latch; unknown marks/flows cannot grant capacity. |
| P13 — data/incidents | Test selected contract ages, positive valid quote topology/sizes, condition policy, continuity and ownership vetoes. | Original clocks with retained precision, bar/revision lineage, feed/subscription state and incident ownership. Unknown evidence cannot validate eligibility; vetoing exposure must not abandon existing valid protection. |

If the selected version is [OCO-first](../evidence/oco-first-execution-research-v1.json),
bind that manifest and its observation-age parent separately. Independently
calculate provisional `ceil_cent(3*L - 2*S)` and final
`ceil_cent(3*F - 2*S)`, deriving final `F` from **all** reconciled entry
executions. Early provisional target execution is permitted only for this
separate candidate; original P11 is preserved. Review early exit followed by
late entry, exact disjoint owned lot portions and signed residuals, with no
re-entry to replace exited quantity. A late fill after terminal census
invalidates finalization; unknown ACK retains allocation; fractional residuals
remain exact and unqualified rather than rounded. First/late-fill protection,
sibling-stop continuity during target amendments, broker reservations and safe
timed close each need their own accepted evidence. Their unresolved gates cannot
become MATCH from arithmetic, simulation or request/ACK binding alone.

Every review must retain this immutable output/checklist; references include
declared journal scope, exact sequence and payload hash, never naked sequence IDs:

1. **Identity/origin:** review/case/session IDs; selected candidate, data, risk,
   execution and build digests; enabled-cell reference or explicit unknown;
   natural/engineering/research-exposed classification and its evidence.
   Retain original first observation, enrollment record/availability and
   pre-launch freeze. Unverified origin/enrollment stays unknown and cannot
   contribute a natural witness. Repeated source observations are not episodes.
2. **Masked package:** package digest, fixed horizon, reviewed decision instant,
   raw-parent ancestry and each input's source/event, first-receipt,
   processed/available clocks, missing/excluded inputs and corrections. Mask bot
   verdicts, bot-calculated risk/size answers and information unavailable at that
   instant, including later prices/P&L. Exit/incident reviews use separate
   packages at their own action instants: then-known fills may enter those
   packages, never the earlier entry decision. A retrospective result's payload
   cutoff does not make its later computation available earlier.
3. **Expected record sealed before unmasking:** reviewer identity and
   independence/conflict declaration; rubric/independent-calculator version and
   digest; calculation worksheet and precise seal time/receipt. For each rule
   record applicability evidence, exact operands/units/rounding and expected
   transition/verdict or reasoned abstention. Retain independent entry
   eligible/ineligible/unavailable verdict and applicable risk, size, limit,
   stop, target, ownership/residual and incident/close calculations. Production
   outputs cannot supply expected values.
4. **Post-seal comparison:** unmask time and bot-trace reference; per-rule
   `MATCH`, `MISMATCH` or `ABSTAIN`, expected/observed values, first divergent
   transition and evidence. Non-applicability requires its rule-based reason and
   is not an extra witness. Missing evidence, unresolved source gate or
   undecidable rule yields ABSTAIN, not MATCH. Preserve disagreements, impacted
   cell/candidate/protocol verdict and append-only explanations; never replace
   the sealed expected record after seeing the bot answer or return.
5. **Disposition:** second-check/adjudication reference, unresolved findings and
   masking/enrollment/freeze violations. Apply the existing failure rules; no
   favorable exclusion, relabeling or paper-driven rule change repairs the
   original holdout. P&L remains descriptive and outside fidelity verdicts.

Reviewer assignments/independence approval, masking/package mechanism,
independent calculation implementation, adjudication responsibility, accepted
identity/stage/enrollment contract, enabled cells, executable dependencies and
final rubric/version/hash seal remain **PENDING**. This draft appoints no
reviewer, certifies no independence, starts no window and admits no exposed
archive as formal holdout.

## 5. Six mandatory engineering scenarios, separate from natural samples

All six scenario groups must pass against the exact release before launch.
Any scheduled in-window recovery exercise must also be named before launch,
use the approved incident path, and avoid creating market orders solely for a
scenario. Keep fixture/injected events in a separate journal and report.

| Group | Required evidence and invariant |
| --- | --- |
| Lost submit/exit response | Unknown outcome retained; reconcile by durable identity before any retry; no duplicate exposure/exit |
| Disconnect and reconnect | New exposure vetoed during untrusted continuity; preserve valid protection; owned state reconciled before resumption |
| Disk/persistence failure | No unjournaled new intent/send; preserve known broker protection; explicit incident and recovery evidence |
| Cancel/fill race | Cancel request is not terminal; every racing fill remains owned and protected; no oversized cleanup |
| Partial-fill crash/restart | Durable cumulative quantities rebuild; no double-counting or lost protection; correct remaining entry/exit ownership |
| Both exit legs fill/reverse inventory | Explicit over-exit/reverse-position detection; only approved disposition states; no guessed automatic corrective order |

Expected outputs, variant fixtures and assertion IDs must be frozen from the
accepted W3 contracts. Synthetic passes cannot stand in for W3-07 actual API
evidence, and actual engineering orders never count as natural strategy cases.

The [October 16 fixture binding](../evidence/w3-engineering-fixture-binding-20261016.json)
now maps all six groups to nine existing test functions and their exact source
hashes. The current related regression passed 121 tests. This is a draft
engineering evidence index, not the final release freeze: synthetic acceptance
flags do not prove production acceptance, the release build must be bound and
retested, and actual W3-07 API evidence remains absent. The review assignments,
oracle, executable versions and other registration fields above remain open.

## 6. Monitoring, incident and restart rules

Daily monitoring is read-only except actions explicitly permitted by the frozen
incident policy. Report feed/news health, expected versus observed availability,
latencies, skipped periods, candidate-stage counts, ownership, protection,
reconciliation and version identity. Never change the strategy based on returns.
Availability and latency are descriptive unless a source-scoped acceptance
limit was frozen; do not invent an uptime SLA after observation.

An ordinary approved restart keeps the original window, event journal and
sample clock. Verify versions, account binding, durable order state, current
inventory/protection and all data/continuity gates before new exposure.
Downtime and unrecoverable gaps remain in the same denominator and session log.
No clean-state restart, erased incident, retroactive fill or replacement day.

A defect affecting strategy, data qualification, risk, orders or evidence
integrity stops new exposure and invokes the approved protection/reconciliation
path. Mark the original study **ABORTED/FAILED** and preserve all evidence.
A repair requires a new release, independent affected-gate review and a new
predeclared complete four-week window; it cannot repair this study's pass.
A purely observational correction may retain the window only after evidence
proves that decisions, inputs, orders and interpretation are unchanged; append
the correction and its review rather than overwriting the original record.

## 7. End-state and decision rule

At the final planned session end, stop new entries under the frozen release,
retain/reconcile every owned intent/order/fill, and complete only the already
approved end-of-session/incident cleanup path. Never liquidate foreign inventory
or assume that a stop request means flat. Unresolved inventory, uncertain orders
or protection status remain an open incident and prevent a completed report.
Preserve the raw ledger, hashes, replay inputs and final account reconciliation.

- **FAIL:** any primary invariant breach, enabled rule mismatch, look-ahead,
  mixed version, unauthorized action, undeclared behavioral change or required
  scenario failure. A later profitable result cannot override failure.
- **INCONCLUSIVE:** no prior failure but incomplete window/evidence, unresolved
  abstentions affecting required coverage, unmet natural witnesses or no natural
  filled-to-flat lifecycle. Separate NOT_STARTED from an incomplete started run.
- **PASS, bounded:** the entire frozen window and all registered engineering
  groups complete, every natural decision/order is auditable, independent rule
  comparison has no unresolved discrepancy, required natural witnesses exist,
  and final owned inventory/orders are reconciled under the accepted policy.
  The claim covers this observed IEX-scoped release/window, not full-market
  Ross fidelity, future reliability, statistical error rates or profitability.

Report gross/net paper P&L, costs, drawdown and trades descriptively with charge
and mark assumptions, alongside availability, counts, mismatches, incidents and
unobserved branches. Paper fills do not establish real execution quality. No
result enables live trading, leverage, a subscription or a strategy switch.

## Draft computation implementation — 2026-10-20

`src/alpaca_ross_bot/paper_report.py` implements deterministic declared-input
coverage and outcome computation for this draft. It accepts no P&L or returns
argument. A positive result is `DRAFT_CRITERIA_MET`, never an accepted paper PASS;
calendar, preregistration, source/reviewer attestations and release freeze remain
explicitly unverified, and runtime authority remains false.

Inputs distinguish unknown session census from an explicit zero-candidate census,
reconcile every natural identity against its declared session and deduplicate
repeated traces globally. Complete period records, independent-review summaries,
decision-visible source times, all enabled cells, six scenario groups, natural
owned filled-to-flat evidence and final reconciliation are checked together.
Every natural decision must be auditable, including cases beyond the minimum
witness quota; a quota cannot excuse missing inputs or risk decisions elsewhere.
Failure takes precedence over incomplete coverage. Fixtures and exposed research
cases cannot fill natural witness quotas. NOT_STARTED, IN_PROGRESS, ABORTED and
incomplete/completed declared runs remain distinct.

This pure evaluator does not authenticate opaque references or enroll a real
holdout. A source adapter must still provide the complete stage census and
verify raw provenance, review independence/masking, enrollment clocks, frozen
versions and calendar. Descriptive broker/modelled-fee ledgers and P&L reporting
remain separate from this verdict computation. The implementation does not fill
the missing final registration fields, accept a release or start the window.

`src/alpaca_ross_bot/paper_performance.py` separately calculates descriptive
gross roundtrip results, broker-original costs and modelled regulatory costs
from explicitly declared, scoped ledgers. Daily rounding uses the pinned fee
policy; identified SEC/TAF/CAT postings are replaced once, other costs retained,
and generic fee rows remain unclassified. Funding is excluded from P&L and
requires study review. Exact fractional executions are supported without FIFO
assignment or guessed per-trade fee allocation. Flat cash observations must
match the declared window endpoints; incomplete ownership, census, attribution
or cash conservation blocks complete P&L claims.

Drawdown uses only supplied marked account-equity samples with their basis and
observed span. Missing or insufficient curves remain unknown, and sampled
drawdown is not a continuous maximum. This is a pure draft calculation, not an
authenticated ledger adapter or actual study report. Source verification and
runtime authority remain false; no descriptive value enters the verdict above.

The source-backed discovery denominator is separately audited by
`scanner_discovery_audit.audit_scanner_event_census`. It enumerates stored
`alpaca_scanner_data` events through a fixed sequence horizon and decision cutoff,
including unfinalized catalog/snapshot records, malformed manifests and
explicitly excluded event kinds. Valid manifests retain their own denominators;
shared raw batch/symbol identities count once in aggregate observations.
Missing snapshots are not no-entry decisions, and unreferenced records are not
proof of failed requests. This audit checks evidence identities and hashes,
without modifying the journal.

The event schemas do not establish formal origin, natural candidate identity,
prospective enrollment or complete wall-clock coverage. This output therefore
cannot fill `natural_candidate_ids`: that field remains unknown until the
accepted stage/origin/enrollment evidence adapter exists. It cannot convert
engineering or historical captures into formal witnesses or close release gates.

### Reported source-stage adapter — 2026-10-22

`paper_source_stages.inspect_paper_source_stages` reuses the discovery audit and
adds reported screen-stage observations from a bounded hash-verified prefix.
Each outward reference includes the caller-declared journal scope, sequence,
hash and availability time; the scope is not authenticated by this adapter.
Empty screen rows are explicit missing census evidence, never a zero-candidate
session. Unmapped derived events and missing inputs remain visible.

The existing October 15 archive was checked through a read-only backup without
new market collection. Its original database/WAL bytes and prior observation
denominators were preserved. This is compatibility evidence, not formal
enrollment or strategy admission. `natural_candidate_ids` remains unknown;
formal origin/episode/enrollment evidence, an ownership-backed performance
ledger adapter, independent review assignments and final freeze remain open.

### Observed ledger and engineering roundtrip sources — 2026-10-22

`paper_ledger_sources.inspect_paper_ledger_sources` retains per-activity evidence
from a bound account capture at one visible immutable prefix. It preserves
exact amounts, original timestamp text and submicrosecond remainder, raw-field
variants and their occurrence locations. Caller-declared journal scope is
carried by source references without claiming namespace authentication.

For the supported simple engineering contract only, completed roundtrips may
be linked through the canonical positive risk reservation, explicit bound
exits, reconciled client/broker identities and matching complete observed fill
censuses. Unbound, conflicting or disagreeing executions remain visible and
cannot gain ownership through flat cash or balanced quantities. An ordinary
daily-stop latch does not hide a proven completed losing roundtrip; it still
blocks trading under the unchanged risk policy.

This is not yet an input adapter for `paper_performance`: no formal study origin,
execution-order contract, full account-day finality, fee attribution or native
OCO ownership is invented. Generic fee rows remain unclassified; observed
pagination exhaustion is not posting finality. Native/formal ownership, complete
study/day censuses, verified window snapshots and the final performance-input
conversion remain open. No observation becomes a natural witness, and no
runtime or order authority is granted.

## Method references

Checked by the coordinator 2026-10-15; source publication/version dates are not
asserted here. [COS preregistration guidance](https://www.cos.io/blog/preregistration-plan-not-prison)
supports separating declared plans from deviations;
[Lakens on sample-size justification](https://lakens.github.io/statistical_inferences/08-samplesizejustification)
supports justifying sample goals by the actual inference rather than a universal
number. [Bailey et al. on backtest overfitting](https://www.davidhbailey.com/dhbpapers/backtest-prob.pdf)
motivate keeping selection/tuning separate from evaluation. These methods do
not validate a trading rule or supply this protocol's engineering thresholds.

# W3 data admission proposal — observation-age alternative

Status: **USER_ACCEPTED_CONTRACT_CHANGE — OBSERVATION_COMPONENT_VERIFIED**, 2026-10-19, Los Angeles.
Proposal ID: `IEX_OBSERVED_INPUT_ADMISSION_PROPOSAL_1`.

This is a separately reviewable alternative to the frozen research candidate's
source-age requirement. It does not amend that candidate, change executable
configuration or authorize a reservation, order or formal paper run. Adoption
needs a distinct version and its own acceptance evidence. After requesting a
recommendation, the user explicitly selected “接受，建立独立版本并验证”. This
accepts the data-contract tradeoff and authorizes implementation/validation;
it does not assert actual source admission, executable release or paper approval.

## Recommendation and exact tradeoff

Review this alternative while preserving the original source-age version.
The inspected Alpaca snapshot interface and pinned SDK models expose no
cumulative-volume last-update watermark. Waiting for authoritative evidence is
valid, but has no established completion date. The alternative uses a measurable
local observation-age contract and explicitly retains source-volume age as
unknown. It gives up the original 300-second source-volume-age guarantee; it
cannot be called equivalent or merely a terminology correction.

The 300-second criterion here belongs to the independently accepted hybrid's
data-admission requirements, not an identified Ross quotation. The proposal
does not change the screen's price/RVOL/ranking formulas, P01/P04–P12 strategy
definitions, no-borrowing rule, optional news/float roles, or coverage scope.
Changing when data is admitted can nevertheless change trading opportunities,
so adoption requires a new version rather than editing the frozen record.

## Proposed observable contract

| Input / gate | Proposed requirement | What it does not establish |
| --- | --- | --- |
| Current snapshot volume | Preserve the exact IEX cumulative full-NY-day value, symbol, bucket and immutable response bytes; record `volume_source_updated_at=UNKNOWN`. | No bound on aggregation/publication age or completeness. |
| Snapshot observation age | At admission, no more than 300 seconds from the earliest request start used by that symbol's snapshot/discovery assessment. Require ordered request/response/first-seen/processing clocks and all evidence visible at decision time. | A recent request or response is not a source-volume watermark. |
| Sweep refresh | Only a completed, identity-checked sweep can rebuild the pool. Each admitted member must independently satisfy its own input age; a late page or sweep completion cannot refresh an older page. | A sweep is not an atomic market snapshot or a complete returned universe. |
| Cached/repeated values | Preserve every observation's real acquisition identity. An old cached response cannot receive a new clock. A genuinely new identical response is another observation with unchanged content and unknown source age. | Unchanged volume neither proves staleness nor proves current aggregation. |
| Trigger trade/quote | Keep the existing 1,000-ms nonnegative provider-reported event-age and local receipt-age checks, valid bid/ask/size and immutable source references. Preserve time precision. | No exchange-clock latency or vendor delivery SLA is asserted. |
| Trade/quote conditions | Require a separately reviewed, versioned source policy. Metadata names alone cannot declare trigger eligibility; unknown cases remain unavailable. | This draft does not choose an unreviewed whitelist. |
| Minute/daily/history bars | Keep P04's provider-minute/full-NY-day definitions, exact expected calendars, 100-bar history and decision-time revision selection. Missing expected bars block evaluation. | A minute-volume sum is not substituted for cumulative daily volume. |
| Revisions | Retain corrections, cancels and updated bars in observation order. Unresolved reconstruction blocks new exposure; later revisions cannot rewrite past decisions. | No assumed finality deadline or guaranteed reconnect replay. |
| Monitored set | Keep the existing 30-symbol limit, complete subscription acknowledgments, reserved-symbol retention and gap vetoes. First RTH trade, setup path and LOD need verified history from open before admission. | A short diagnostic stream or acknowledgment does not prove continuity from open. |
| News/float | Retain optional, non-veto preferences for the selected independent screen. Missing or unqualified observations stay unavailable as preferences. | No mandatory catalyst, AI approval or Ross-equivalence claim is introduced. |

The 300-second acquisition-age definition above is a proposed new contract,
not an interpretation silently applied to existing records. The actual code
must define exact multi-page boundaries and verify this behavior before any
claim of implementation acceptance.

## Counterexample and residual risk

A synthetic pair of possible provider states illustrates the information gap:
both responses report the same daily bucket, volume 500 and receipt time 14:00.
In one world, the aggregate was updated at 14:00 and no new volume followed.
In another, it was updated at 13:50 and the current aggregate is already 600.
The observed fields are identical but source ages are 0 and 600 seconds. A
deterministic check confirmed these outcomes lie on opposite sides of 300.
These are invented mathematical inputs, not observed provider behavior.

The proposed observation-age gate admits that the second world cannot be ruled
out from these fields. A stale aggregate could change a screen match/rank,
monitored-set admission, or a missed opportunity even while trigger prices are
fresh. Retrieving more identical snapshots cannot prove the unknown source
watermark. This residual risk must be acknowledged in a separately accepted
research version and studied prospectively; a profitable paper result cannot
retroactively prove freshness.

## Acceptance and remaining validation

The user accepted this contract change on 2026-10-19. Its scope is an
independent research/data version; the original source-age contract remains
frozen. Implementation and tests must distinguish a passing observation-age
component from complete source admission. No release flag changes are implied.

The collector's journaled raw response and acquisition metadata form the local
trust boundary. Hashes detect changed recorded bytes; they do not authenticate a
network exchange independently of the collector. Reusing an old raw reference
cannot refresh acquisition time. Fabricating an entirely new raw capture is not
claimed to be detectable merely because its content matches an earlier response.

The following evidence remains required; the first item's user decision is now
satisfied, while implementation and actual-source qualifications are separate.

The independent component is implemented in
`src/alpaca_ross_bot/observation_admission.py`, pinned to
`evidence/iex-snapshot-observation-age-research-v1.json` as
`IEX_SNAPSHOT_OBSERVATION_AGE_RESEARCH_V1`. New tests exercise the actual collector
with synthetic network responses; they do not certify a real session's data.
Seventeen new tests and 1501 full offline regression tests passed; independent
review passed 69 combined tests with no material findings. Ruff/format and strict
mypy passed. This verifies the bounded observation-age component, not all items
below. See [implementation evidence](week3-evidence.md#independent-observation-age-contract--user-acceptance-2026-10-19).

1. Independent review of the exact metric, source interpretations and differences
   from the frozen candidate. Explicit user acceptance of a new data contract.
2. Source-only parsing/admission: callers provide references, not parsed prices,
   timestamps or `eligible=True`. Bind account/version and immutable sequence
   horizon where used by risk/entry state.
3. Tests for multi-page age, processing delay, clock reversal, copied-cache time,
   identical fresh responses, missing objects, revisions, unknown conditions,
   handoff acknowledgments and interrupted continuity.
4. Complete positive and refusal replay cases covering feature → entry → risk →
   capital linkage; label synthetic and genuine-input cases separately. Passing
   synthetic cases cannot establish an actual session's source eligibility.
5. Actual source-scoped observation/replay evidence for an admitted symbol's
   history and setup path. Today's finite SPY samples cannot satisfy this alone.
6. Preserve the independent protection, broker-cycle, release and four-week
   protocol gates. This proposal cannot waive them or start formal paper.

## Primary evidence rechecked 2026-10-19

- [Alpaca snapshot interface](https://docs.alpaca.markets/us/reference/stocksnapshotsingle)
  describes separate latest trade/quote and minute/daily/previous-daily bars.
- [Pinned v0.44.0 snapshot model](https://raw.githubusercontent.com/alpacahq/alpaca-py/v0.44.0/alpaca/data/models/snapshots.py)
  contains those components without a cumulative-volume update watermark;
  [bar model](https://raw.githubusercontent.com/alpacahq/alpaca-py/v0.44.0/alpaca/data/models/bars.py)
  identifies its timestamp as interval opening time.
- [Stock stream documentation](https://docs.alpaca.markets/us/docs/real-time-stock-pricing-data)
  documents minute-boundary daily-bar emission and separate update/correction
  messages; cadence is not an individual payload's emission timestamp.
- [Market-data FAQ](https://docs.alpaca.markets/us/docs/market-data-faq)
  describes condition-dependent aggregation. Its bar calculation rules are not
  a strategy trigger whitelist or permission to sum minutes as daily volume.

Absence applies to the inspected public interface, not a claim that Alpaca can
never provide additional evidence. The existing research acceptance artifact
and its blocked source-age contract remain unchanged.

## Actual-trigger binding seam — 2026-10-22

This is an implementation dependency inventory, not a new accepted condition
policy or a relaxation of session-history requirements. Reuse the existing
account-risk owner; do not introduce another allocator.

1. A reference-only admission receipt must identify the candidate version,
   policy version, immutable journal horizon and decision cutoff. Every trade,
   quote, historical bar and connection observation must resolve to retained
   bytes available by that cutoff. Caller booleans cannot establish eligibility.
2. Trade-price eligibility, minute/daily aggregation eligibility and quote
   eligibility are distinct policy decisions. The official
   [latest-trades endpoint](https://docs.alpaca.markets/us/reference/stocklatesttrades-1)
   excludes price-ineligible trades, including odd lots; it does not establish
   a complete trigger history or a quote whitelist. The
   [aggregation FAQ](https://docs.alpaca.markets/us/docs/market-data-faq)
   supplies aggregation semantics, not permission to treat all received trades
   as strategy triggers. No condition whitelist is adopted here.
3. Reconstruct the first eligible regular-session IEX trade, setup path and
   low-since-open only from an accepted historical/revision contract. The
   [stream schema](https://docs.alpaca.markets/us/docs/real-time-stock-pricing-data)
   documents trade IDs and correction/cancel messages, but no transport sequence
   counter. Do not interpret consecutive IDs, a quiet interval or an acknowledged
   subscription as proof of complete delivery. Connection observations describe
   local health; provider completeness remains a separate unresolved question.
4. Bind an admitted strategy decision to the existing risk owner's same-prefix
   source funds/equity/E0/fee/lifecycle snapshot. The current research declaration
   path remains non-authorizing until these upstream inputs are qualified.

Independent retrieval and coordinator inspection checked these public pages on
2026-10-22. A public-only remote `gemma4:31b` analysis confirmed the distinction
between observed connection health and unresolved completeness; its generic
claims do not authenticate actual data or prove that any stream is active.
No credentials, local code or account evidence were exported. No runtime gate,
strategy rule or original source-age contract changed. `NOT_SIP_EQUIVALENT`
remains mandatory.

### IEX normalization and completeness evidence boundary

Further first-party retrieval checked on 2026-10-22:

| Evidence | What it establishes | What it does not establish |
| --- | --- | --- |
| [IEX official specification directory](https://www.iex.io/resources/trading/market-data), TOPS v1.66 | Identifies the native IEX specification and direct-feed access process | Alpaca exposes the same transport guarantees or native fields |
| [TOPS v1.66](https://storage.googleapis.com/assets-bucket/exchange/assets/IEX%20TOPS%20Specification%20v1.66.pdf), updated 2021-10-19, pp. 3, 16–17 and 26 | Native IEX-TP has sequenced delivery/gap-fill support; quote messages have native status/session flags | A mapping from those flags to Alpaca quote condition arrays/tapes, or native sequence availability through Alpaca WebSocket |
| [UTP input v4.0a](https://www.utpplan.com/DOC/UtpBinaryInputSpec.pdf), July 2026, section 4.2 | Defines quote condition R within that SIP protocol | That SIP definition is a verified Alpaca-IEX normalization or a strategy whitelist |
| [Alpaca historical trades](https://docs.alpaca.markets/us/reference/stocktradesingle-1) | Query boundaries, feed selection and pagination interface | Immutable original delivery history, a complete-session watermark, or revision incorporation timing |

The absence claim is limited to inspected public interfaces. A finite successful
GET, metadata name or matching observed price cannot prove the missing mapping
or completeness guarantee. Native IEX protocol capabilities are a research lead,
not an available entitlement or permission to switch the selected Alpaca source.
No subscription, direct-feed connection, new condition policy or runtime gate
change is authorized or performed. A qualified policy for the selected Alpaca
interface remains necessary before actual-trigger admission. This does not
require proving native TOPS equivalence or obtaining a vendor completeness
watermark; local health alone also cannot replace the required reconstruction.

### Controlling acceptance scope — correction, 2026-10-22

The October 19 accepted manifest's retained requirements control this work.
They use provider-reported event/local-receipt age, a reviewed versioned source
policy and decision-time from-open reconstruction. The native-protocol unknowns
above are limitations on stronger claims, not additional universal prerequisites
that an October 22 research note may impose retroactively.

The next policy may rely on documented Alpaca-reported conditions, timestamp
semantics and historical query/pagination/revision evidence without asserting
native clock/flag equivalence, absolute lossless delivery or revision finality.
It must still reconstruct the first eligible RTH trade, setup path and low since
open from available evidence. First locally observed trade is not an authorized
substitute. Unknown conditions, unresolved known gaps, missing required bars,
clock conflicts and unavailable inputs still veto admission. Metadata names
alone do not select a whitelist; unsupported eligibility choices require user
policy acceptance. All accepted ages, candidate identities and runtime gates
remain unchanged. No positive actual-source qualification follows from this
scope correction.

The original cumulative-volume source-age baseline remains preserved and
unqualified. This correction concerns the independent V2 reported-input
requirements; it does not restore the original source-age claim.

### Selected-interface policy evidence inventory — 2026-10-22

| Published Alpaca interface | Supported policy input | Still requires explicit review |
| --- | --- | --- |
| [Latest trade](https://docs.alpaca.markets/us/reference/stocklatesttradesingle-1) and [aggregation FAQ](https://docs.alpaca.markets/us/docs/market-data-faq#how-are-bars-aggregated) | Latest trade excludes price-ineligible conditions; aggregation distinguishes open/close, high/low and volume by tape and minute/daily type, combining multiple codes strictly per field | Which price-update semantics define each strategy purpose; bar aggregation alone is not an adopted trigger whitelist |
| [Condition metadata](https://docs.alpaca.markets/us/reference/stockmetaconditions-1) and [latest quotes](https://docs.alpaca.markets/us/reference/stocklatestquotes-1) | Separate singular trade/quote metadata by tape; latest quotes reports feed-selected best bid/ask | The quote page supplies no explicit condition inclusion algorithm; names alone do not choose quote admissibility |
| [Historical trades](https://docs.alpaca.markets/us/reference/stocktradesingle-1) and [quotes](https://docs.alpaca.markets/us/reference/stockquotesingle-1) | Explicit feed/range/sort and inclusive boundaries; follow pagination tokens even on short pages | Overlap/deduplication, ties, revisions and known-gap handling; `asof` is symbol mapping, not historical knowledge time |
| [Stock stream](https://docs.alpaca.markets/us/docs/real-time-stock-pricing-data) | Reported nanosecond timestamps, quote sizes in round lots, late-bar updates and separate correction/cancel observations | Preserve exact availability and revision lineage; do not import native-clock or finality claims |

Independent retrieval checked these first-party pages, and the coordinator
rechecked the FAQ matrix. This inventory selects no new trading/quote policy.
The next concrete contract must separate source-documented facts from any
remaining eligibility choice, so only genuine policy decisions reach the user.

## Concrete reported-price policy proposal — pending acceptance

Proposal ID: `ALPACA_REPORTED_INTRADAY_PRICE_POLICY_PROPOSAL_1`.
Status: **DRAFT — USER POLICY ACCEPTANCE AND INDEPENDENT VALIDATION REQUIRED**.
This fills the currently undefined source-eligibility rule; it is not a Ross
rule, executable acceptance or an order authorization. Preserve the existing
observation-age/OCO-first parent versions and both accepted manifest bytes.

### Proposed source-eligibility decisions

1. **Intraday price events:** use the published Alpaca **minute** price-update
   rules, by reported tape and every reported trade condition, for the first
   eligible RTH trade, setup/invalidation/trigger prices and the RTH low. The
   published minute open/close and high/low flags agree; volume-only trades do
   not become price events. Apply the strictest applicable condition per field.
   This is a proposed strategy-use mapping of documented aggregation facts,
   not a claim that Alpaca prescribes trading triggers. Daily trend and prior
   daily close still use the unchanged observed provider daily bars; do not
   substitute minute rules for daily aggregation or recompute scanner volume.
2. **RTH consistency:** retain the calendar's reported-time RTH interval and the
   accepted provider-reported/local-receipt ages. A condition identifying
   extended-hours activity inside that RTH interval is a source/session conflict
   requiring resolution before the affected symbol/derivation can be admitted,
   even if minute aggregation could update a price. This conflict veto is a
   new conservative eligibility choice, not an inherited requirement or an
   all-symbol/session-wide latch. Unknown code/tape combinations, unresolved ordering or known
   history gaps remain unavailable. Preserve exact codes, including the space
   code on tapes A/B; do not trim or substitute tape-C codes.
3. **Entry quote profile:** explicitly require the existing reported-regular
   quote shape `c == ["R"]`, tape A/B/C and contemporaneous source metadata
   identifying R as `Regular Market Maker Open`, plus the unchanged quote age,
   positive bid/ask/sizes and bid-not-above-ask checks. Preserve reported size
   units; do not manufacture share depth. Reuse the source-verification checks
   of `ALPACA_IEX_REPORTED_REGULAR_BID_V1` without treating its earlier valuation
   acceptance as entry-policy approval. Extending this restriction to entry is
   a new proposed policy choice. It asserts no NBBO, firm liquidity, native IEX
   flag mapping or fill guarantee. Other quote conditions remain unavailable
   for this profile, not negative strategy witnesses.
4. **History/revisions:** require decision-visible raw trade history from open,
   complete observed query ranges/pages and monitored-stream overlap/revision
   reconciliation before deriving first trade, setup state or low. Bars alone
   cannot reconstruct trade order. Never substitute the first locally seen
   trade, assume consecutive trade IDs or infer missing minutes. Preserve raw
   occurrences, conflicts and availability times; an unresolved order between
   distinct price events with tied timestamps blocks an order-sensitive
   derivation unless every possible ordering produces the same result; a
   commutative low calculation alone does not require invented ordering.
   Corrections are not new live trigger prints, and later repairs never rewrite old decisions
   or restore a consumed attempt. Freeze source identity/deduplication/revision
   mechanics in the independently reviewed implementation; no finality delay,
   watermark or native lossless-delivery claim is added here.

### Review cases fixed before implementation

| Reported input | Proposed handling | Why it matters |
| --- | --- | --- |
| Nasdaq `@` plus `I`, price above trigger high | Retain volume-only observation; no price trigger | A high odd-lot print does not update the provider minute price |
| Condition B on tape C versus tape A/B | Apply the different published tape rules | A regular-sale-only shortcut would discard documented price events; a tape-blind rule can admit an average-price event |
| P/Z/4 in a minute price history | Not a minute price event under the documented rules | Daily high/low eligibility must not leak into intraday trigger/low rules |
| T inside the reported RTH interval | Unavailable source/session conflict pending resolution | Minute aggregation eligibility alone cannot settle the RTH inconsistency |
| Latest quote carries an unsupported condition | Unavailable quote; preserve the observation | No older qualifying quote may be cherry-picked to replace it |
| History begins after open or a distinct-price timestamp tie can change the setup result | No positive admission for the affected derivation | Neither a fresh quote nor a later download proves a missing or ambiguous setup path; order-independent calculations need no invented sequence |

Approval would authorize a separate versioned source-policy implementation and
validation of these choices. It would not qualify actual inputs, change risk
amounts/entry windows, approve a protection gap, enable broker calls or start
formal paper. The original Ross baseline and source-age baseline remain
preserved. A rejected or unresolved policy leaves the current source gate open;
metadata names alone will not be used to fill it silently.

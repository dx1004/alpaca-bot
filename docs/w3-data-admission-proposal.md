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
change is authorized or performed. A qualified mapping/history contract remains
necessary before implementing a positive actual-trigger admission path; local
health/provenance work cannot silently substitute for it.

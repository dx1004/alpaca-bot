# W3 data admission proposal — observation-age alternative

Status: **PROPOSED_FOR_REVIEW — NOT ACCEPTED**, 2026-10-19, Los Angeles.
Proposal ID: `IEX_OBSERVED_INPUT_ADMISSION_PROPOSAL_1`.

This is a separately reviewable alternative to the frozen research candidate's
source-age requirement. It does not amend that candidate, change executable
configuration or authorize a reservation, order or formal paper run. A future
adoption needs a distinct version and its own acceptance evidence. The user's
request for a recommendation is not recorded as acceptance of this proposal.

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

## Required evidence before adoption

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

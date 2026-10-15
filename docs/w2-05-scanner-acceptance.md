# W2-05 scanner implementation and acceptance

Review session: 2026-10-14 Los Angeles; authenticated probe date: 2026-10-15 UTC.
Status: **engineering increment verified; full W2-05 remains BLOCKED_RESEARCH**.

## Scope contract

Implement source-backed scanner infrastructure and deterministic observations for
the current Alpaca-only path. Keep the three Ross toolkit contexts parallel.
Webull and paid live SIP remain deferred. Preserve existing W3 changes. No order,
data purchase, live trading, formal paper evaluation, automatic strategy choice,
or retrospective reconstruction of unavailable evidence is authorized here.

The existing W2-05 dependency still requires an accepted source-scoped strategy
version and verified required data. Engineering tests cannot satisfy those
dependencies by themselves; the TODO checkbox stays blocked.

## Implemented and verified

`AlpacaScannerData.collect_sip_rankings` adds fixed-host, bounded read-only
collection of most-actives and stock movers. List size is mandatory; most-actives
also requires an explicit volume/trades metric. Documented 100/50 caps are
validated before network access. Exact bytes, SHA-256, request URL/query and local
request/receipt clocks enter the existing canonical SQLite journal before parsing.

`scanner_rankings.journal_sip_ranking_observation` independently verifies those
bytes, digest, source/feed, request parameters and local availability. It validates
the required fields against the pinned official SDK model, rejects duplicate JSON
keys, malformed lists, invalid symbols, duplicate symbols within a list, excessive
cardinality and invalid/nonfinite metrics. Provider ordering and separate gainers /
losers ranks are retained; no sorting, gap computation or strategy filtering is
inferred. Additional raw fields remain in the parent response.

The provider's `last_updated` is computation time, not a symbol trade or candidate
first-seen timestamp. Its exact nanosecond string is retained; envelope time is
explicitly a microsecond floor. A computation time later than raw local receipt,
including a one-nanosecond difference, prevents projection. Raw evidence and an
audited rejection remain. Valid derived events become replay-visible only after
processing. Transactional insertion keyed by raw sequence prevents duplicate
projection.

These observations use their own `alpaca_sip_screener` feed and provider-top-list
scope. They do not enter the IEX change/RVOL measurement function, create a Ross
candidate, supply missing float or imply rejection of omitted stocks. Every output
has `full_market_coverage=false`, unavailable premarket rank, blocked strategy
eligibility and an explicit `NOT_SIP_EQUIVALENT` label for any IEX enrichment.

## Current gate findings

| Requirement | Evidence | Disposition |
| --- | --- | --- |
| Limited ranking access and fields | Two authenticated fixed-endpoint GETs with `top=1` validated actual schemas; most-actives returned one row, movers two rows across its lists. The temporary journal and raw responses were deleted. | Observed technical access/schema pass only; capacity, coverage and freshness not accepted. |
| Full Ross candidate discovery | Alpaca lists are capped; movers shows the previous market day's results before its market-open reset. Most-actives session boundaries and completeness are not specified. | Cannot establish current premarket gap ranks, complete universe or Ross ranking recall. |
| Point-in-time share float | Neither examined Alpaca screener schema supplies share float. No independently accepted free provider with usable field coverage, availability/effective/revision clocks and account entitlement was established. | Missing float blocks qualification. Shares outstanding and dollar public float are not substituted. |
| RVOL | Toolkit describes comparison with a typically 30-day daily average; a separate Ross article describes comparison at the same time of day. Existing 30/50-day full-day ratios remain descriptive. | Preserve source contexts; do not designate one as the reproduced scanner algorithm or mix SIP ranking volumes into IEX ratios. |
| Source-scoped rules | Toolkit opening, detailed selection and worksheet profiles remain separate; range endpoints, prior-day continuation, market regime and account-stage applicability are unresolved where source evidence is insufficient. | No default version, silent rule merging or executable version acceptance. |
| News influence on scanning | Warrior's Five Pillars scanner admits stocks without news; the worksheet separately asks for a positive catalyst. | News presence is not one universal hard filter. W2-06 acceptance remains independent. |
| Historical and prospective evidence | No complete historical source-time universe, float and scanner-reference dataset was acquired. | New observations cannot validate old Ross cases or become an unseen historical holdout. |

## Primary-source record

Independent GPT-6.1 Sol Low retrieval was checked by the coordinator against
primary sources on 2026-10-14:

- [Alpaca most-actives](https://docs.alpaca.markets/us/reference/mostactives-1)
  specifies SIP-derived volume/trade rankings and the 100-result cap.
- [Alpaca movers](https://docs.alpaca.markets/us/reference/movers-1) specifies
  SIP derivation, 50 results per side, split-adjusted changes and the prior-day
  behavior before market-open reset.
- [Official SDK v0.44.0 screener models](https://github.com/alpacahq/alpaca-py/blob/v0.44.0/alpaca/data/models/screener.py)
  establish `most_actives`, `gainers`, `losers`, `market_type`, numeric row fields
  and ranking-computation `last_updated`. This resolves the earlier probe's
  response-schema research gap; it does not change that earlier probe's result.
- [Alpaca subscription plans](https://docs.alpaca.markets/us/docs/about-market-data-api)
  and [FAQ](https://docs.alpaca.markets/us/docs/market-data-faq) separate IEX Basic
  coverage from consolidated live SIP trade/quote/stream entitlements.
- [2025 v2 toolkit](https://cdn.warriortrading.com/warriortrading.com/assets/2025-Small-Account-Tool-Kit-1013.pdf),
  opening criteria, worksheet and detailed stock-selection sections, contains
  distinct contexts rather than one resolved $500/1x profile. Exact publication
  date and automated applicability remain unverified.
- [Ross technical-indicators article](https://www.warriortrading.com/technical-indicators/)
  describes same-time-of-day relative volume. Its applicability to the toolkit's
  scanner implementation is not established.
- [Warrior scanner documentation](https://support.warriortrading.com/support/solutions/articles/19000117763)
  describes the top-100 gap display and news-filter exception; it does not disclose
  a complete reproducible universe/filter/tie-handling implementation.
- [FMP documentation](https://site.financialmodelingprep.com/developer/docs) and
  [pricing](https://site.financialmodelingprep.com/developer/docs/pricing) were
  considered as a possible float research lead. A date field and a free tier do
  not establish this endpoint's free entitlement, first availability or immutable
  revision history. No FMP source or account was adopted.

A verified public-only Ollama Cloud `gemma4:31b` inference provided a development
proposal. The coordinator retained the separation/projection idea and rejected its
JSONL owner (the project already owns state in SQLite) and its suggestion to mark
truncation only at the maximum row count (every response is a limited top list).
No model supplied strategy authority. An independent quant-developer review found
the submicrosecond replay issue; the correction received a second review approval.

## Validation

Targeted scanner tests, deterministic replay, full pytest, Ruff, strict mypy,
formatting and scoped diff checks are recorded with final counts in
[Week 2 evidence](week2-evidence.md). Live validation proves only two small
authenticated responses against this schema. It does not prove a different
session's behavior, limits, license rights, complete discovery, strategy fidelity,
profitability or any permission to trade.

Completion requires source-scoped scanner specifications plus actual eligible
universe/float/RVOL inputs and their acceptance evidence. This task's acceptance
criteria have not been weakened to call a permanently blocked scanner complete.

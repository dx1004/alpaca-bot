# W2-05 scanner implementation and acceptance

Review session: 2026-10-14 Los Angeles; authenticated probe date: 2026-10-15 UTC.
Status: **engineering increment verified; full W2-05 remains BLOCKED_RESEARCH**.

## Closure work — 2026-10-15 Los Angeles

The user requested resolution of strategy specification, data eligibility and
candidate/rejection acceptance. Work is split across quantitative/source research,
trading-strategy interpretation, market-data engineering, development and
independent validation. The original acceptance criteria remain in force.

### Actual current inputs and deterministic checks

The [dated probe record](../evidence/w2-05-input-audit-2026-10-15.json) binds raw
captures by URL, receipt time and SHA-256. Raw market/reference responses are
retained locally under the ignored research archive named in that record; no
credential, account, position or order response is included.

- The two official Nasdaq directory files parsed as 5,629 and 7,660 rows. Creation
  labels are retained without assigning an undocumented timezone. Symbols and
  each ACT/CQS/Nasdaq alias remain separate; no punctuation replacement is used.
- A current Alpaca active-US-equity catalog contained 14,403 unique symbols.
  This proves receipt of this broker catalog, not a complete US-listing universe.
  Among 5,621 non-test Nasdaq labels, three did not occur literally in that
  catalog. Other-listed literal differences vary by namespace. These are
  reconciliation findings, not proven missing securities or accepted mappings.
- Bounded IEX historical requests returned 42 daily bars each for AAPL and QS,
  with exhausted pagination. Both contain the explicitly expected 30 sessions
  from September 1 through October 13 for an October 14 numerator. The calendar
  was separately obtained from Alpaca. This two-symbol sample is not a full
  universe sweep, historical holdout or live candidate evaluation.
- `scanner_input_audit.py` now checks dated daily observations, New York daily
  event boundaries, separate completion/availability clocks, explicit expected
  dates, same source/feed/scope/adjustment, pagination, zero denominators and a
  clocked numerator. It reports stable findings instead of silently inserting
  zeros or selecting the last available bars across unexplained missing dates.
  The directory parser retains byte hashes, aliases, collisions and test flags.

Alpaca daily volume includes eligible extended-hours trades. A daily-bar timestamp
labels its New York bucket start; it does not prove completion or immutable
finality. The source-only probe therefore reports completion unproven. A second
check using explicitly declared next-midnight bucket ends passes **input-contract
consistency only**; that assumption is not provider finality or Ross acceptance.
The October 14 numerator was acquired on October 15, so it is expressly an older
session observation, not fresh live RVOL. Source/event/availability times cannot
be backdated to claim the inputs were known in a prior trading decision.

### Free float and capitalization source: concrete candidate

The user confirmed having no float-data account and requested continued free
source research. The newly verified candidate is EODHD's
[Live v2 reference fields](https://eodhd.com/financial-apis/live-v2-for-us-stocks-extended-quotes-2025):
`sharesFloat` is separately documented from `sharesOutstanding`, and `marketCap`
is another field. The [official plan comparison](https://eodhd.com/pricing)
includes this endpoint in Free: 20 calls/day and 20 requests/minute, charged one
call per ticker, even in a batch. The temporary welcome bonus is not a recurring
capacity allowance. Paid Fundamentals is a separate endpoint and is not Free.

A successful public-demo AAPL response returned non-null integer float,
outstanding shares and capitalization plus USD currency and snapshot time.
This closes the previous absence of a documented, concretely observed free-float
field path. Demo access is limited to specified examples and does not establish
actual-user access, non-demo coverage, sustained capacity or float freshness.
Snapshot time is not float effective/revision time. Forward-only local first-seen
and changed-payload retention can establish knowledge time, but cannot invent
the provider's missing update semantics or a historical archive.

`eodhd_reference.py` now captures supplied response bytes in the existing journal
before parsing and independently projects only reference fields. It checks raw
provenance/digests, duplicate JSON fields, count/identity mismatches, omitted
symbols, field types and provider clocks. Missing float never falls back to
outstanding shares or capitalization. Per-raw-sequence/symbol transactional
projection is idempotent; later receipt of identical bytes is a new observation.
Snapshot time, first-seen and processed availability remain separate. All derived
values keep unknown float effective/revision time, unverified freshness and
actual-user entitlement, a null trusted-float field and blocked eligibility.
Reference capture/projection handles no tokens, issues no network requests and
does not integrate with a strategy.

The user subsequently reported completing EODHD free registration. A separate
local credential helper is now available in
`scripts/configure_eodhd_credentials.py`. It reads a hidden token only from an
interactive terminal and stores it outside Git at
`~/.local/share/alpaca-bot/eodhd-reference.json`, separately from paper credentials.
The storage module enforces an owned private directory/file, rejects symlinks
and unsafe metadata, bounds token-only JSON and prevents implicit overwrites.
The optional `--check` validates local storage/format only; it does not authenticate
with EODHD. No actual token was read or saved during development.

RTK captures standard error even with terminal input. The credential CLI now
requires terminal input and lets Unix `getpass` use its default controlling
terminal, so the hidden prompt is visible through RTK. Unsafe echo fallback still
aborts. Independent synthetic tests through both RTK and direct Python verified
prompt visibility, disabled echo during entry and restored terminal settings.

After the user configured the token, local format/privacy validation passed.
The [separate authenticated probe record](../evidence/w2-05-eodhd-personal-probe-2026-10-15.json)
binds one QS request and its locally retained raw bytes. It returned HTTP 200 with
matching QS identity, USD and positive integer float/outstanding/capitalization
fields. Independent validation checked hashes, shape, field consistency and clocks.
This establishes one-symbol field access only. The first sandbox attempt failed
DNS before HTTP; tool-level network permission resolved that isolated failure.
Only the single QS request reached EODHD, representing one documented ticker-call
unit. No remaining-quota balance was queried or inferred.

The 20-ticker/day limit remains a capacity blocker for any workflow requiring more
reference lookups. Batching does not reduce ticker charging; caching cannot prove
unknown float freshness. No automatic shortlist, refresh interval or quota-based
candidate omission is accepted. FMP and Finnhub still lack verified free float
entitlement/capacity for this scope; a larger advertised generic quota is not proof.

Direct HTTP API access is the selected integration path for the existing raw
capture/replay design. EODHD's
[official MCP documentation](https://eodhd.com/lp/eodhd-mcp-server) says data tool
calls use the same API quota and plan-controlled data access. MCP is an optional
AI-client interface, not a prerequisite or a solution to float freshness,
small-cap coverage or free-plan capacity. No MCP installation is needed here.

The archived public demo was also projected through a real local journal:
one reference observation, no schema diagnostics, duplicate projection suppressed,
and no trusted float or accepted candidate. An independent temporary-journal
check verified that replay cannot expose the derived event before processing.

The [personal-use terms](https://eodhd.com/financial-apis/terms-conditions) permit
private storage/analysis for qualifying nonprofessional use, subject to their
restrictions. No account was created or terms accepted for the user. FMP's
specific free small-cap float entitlement remains unverified. Alpha Vantage's
current Overview is premium; Finnhub's inspected schema does not establish a
free float field. None is substituted silently. A provider-backed float value
cannot be replaced by shares outstanding, market capitalization or SEC dollar
public float.

### What still prevents the original acceptance

| Required closure | Work now concrete | Remaining prerequisite |
| --- | --- | --- |
| Source-scoped strategy/scanner specification | The three Ross contexts remain parallel; numeric source statements and their qualitative exceptions are preserved. Martin's historical scans, newer interview and user-clarified entry rhythm remain separately attributed. | No single applicable $500/1x executable version or deterministic interpretation of the remaining discretionary choices has been accepted. Neither IEX convenience nor the new provider selects a version. |
| Required data | Actual broker catalog, two-symbol dated IEX history, public-demo float schema and a single authenticated QS float/cap response are verified. Explicit consistency failures now have deterministic reasons. | Broader identity/coverage, float update/freshness and sufficient daily capacity remain unaccepted. Twenty ticker calls/day cannot be assumed sufficient. Full discovery and source-scoped RVOL session/baseline acceptance remain separate. |
| Candidate/rejection evidence | Input-quality rejection paths can be tested without inventing a strategy; raw inputs and dated diagnostics are retained. | No qualified-candidate/rejection replay for an accepted strategy has occurred. A missing input is unavailable, not a failed trading rule. G03/G04 remain open. |

Local key configuration and one QS access check are complete. Further source
acceptance requires documented update semantics and evidence that required lookup
demand fits verified capacity, or a separately verified adequate source. Preserve
absent/null fields and rejections. No paid subscription, account creation,
live-price replacement, automated selection of 20 favored symbols, or trading
authority follows from this source discovery.

Validation for this increment: 103 new-module targeted tests passed. The current
workspace full suite passed 251 tests, with one existing upstream
`websockets.legacy` deprecation warning. Ruff, strict mypy and formatting checks
passed for the four new source/test files. Independent review verified both
modules and recomputed the actual directory/catalog/history counts, hashes and
exact rational ratios. It approved the bounded engineering scope, explicitly
withholding W2-05/G03/G04 acceptance. Unrelated unfinished W3 changes were
present during the workspace suite and remain outside this change set.

Credential-helper validation is separate: 36 new synthetic tests plus two existing
paper-credential regressions passed. Scoped Ruff, strict mypy and formatting checks
passed for the three helper/test files. These checks do not establish actual-user
entitlement or close W2-05/G03/G04.
Independent validation approved this bounded scope, including a real controlling
terminal with synthetic temporary credentials that verified hidden input without
token echo. It did not access the user's credential store or any provider API.

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

Supplemental-author follow-up: the user supplied a concrete Martin Luk video.
Verified firsthand dated scanner replies now appear in the
[supplemental research record](no-leverage-pullback-research.md#martin-luk-supplemental-source-review-2026-10-14).
This resolves the author's identity and adds candidate-source settings; it does
not close the table's data or operational-rule gaps. Original video metadata was
read, but its caption response was empty. A later user-supplied transcript and
visuals, current official vendor formulas, remaining timeframe uncertainties and
the original earlier-interview link are recorded in that same research document's
formula follow-up. The roughly four-minute audio trim is not an exact timestamp
mapping. Slide evidence is separate from authenticated spoken rules. These findings
reduce documentary gaps; they do not establish eligible live inputs or close G03/G04.

The [newer-interview reconstruction](no-leverage-pullback-research.md#martin-newer-interview-reconstruction-2026-10-15-utc)
adds 20 ASR/slide-scoped propositions, retained/changed-rule distinctions, five
explicit hypotheses, counterexamples and a source-time validation contract.
It supersedes using the old scanner settings or teacher parameters as implicit
defaults for Martin's newer candidate. Scan-workflow continuity is described in
the newer transcript, but exact parameter continuity remains unproven. This is a
research specification; no executable profile, data eligibility, replay outcome,
W2-05 acceptance checkbox or paper authority is created.

A subsequent [gap-closure follow-up](no-leverage-pullback-research.md#gap-closure-follow-up-author-evidence-and-vendor-semantics)
adds Martin's edited short-study source, scopes its April-2025-draft/January-2026
publication provenance and records indicator semantics from official vendors.
In particular, Alpaca's normal bar volume can differ from the hidden volume
used for base-bar VWAP. Bar-VWAP aggregation is not generally exact eligible-trade
AVWAP reconstruction. Vendor defaults cannot stand in for Martin's actual saved
settings, and short-study risk/exit rules do not freeze the newer long profile.
The closure table separates resolved documentary facts from unresolved
discretionary choices and feed eligibility; the original gate remains blocked.

Targeted scanner tests, deterministic replay, full pytest, Ruff, strict mypy,
formatting and scoped diff checks are recorded with final counts in
[Week 2 evidence](week2-evidence.md). Live validation proves only two small
authenticated responses against this schema. It does not prove a different
session's behavior, limits, license rights, complete discovery, strategy fidelity,
profitability or any permission to trade.

Completion requires source-scoped scanner specifications plus actual eligible
universe/float/RVOL inputs and their acceptance evidence. This task's acceptance
criteria have not been weakened to call a permanently blocked scanner complete.

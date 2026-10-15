# W2-05 scanner implementation and acceptance

Review session: 2026-10-14 Los Angeles; authenticated probe date: 2026-10-15 UTC.
Status: **independent IEX adaptation selected; original Ross/full strategy gates remain BLOCKED_RESEARCH**.

## Accepted independent screen — 2026-10-15

The user explicitly accepted `GENERAL_IEX_DAILY30_SCREEN_V1` for scanner and
replay acceptance. Its [machine-readable contract](../evidence/general-iex-daily30-screen-v1.json)
is `USER_ACCEPTED_ADAPTATION_NOT_ROSS_REPLICA`. The original Ross comparison
profiles and their unresolved reconstruction claims remain separate. This
supersedes the formula-choice blocker **for this independent screen only**;
the dated research reviews below describe the earlier state.

The filter uses $1 inclusive to $20 exclusive and at least 5× RVOL. RVOL is the
observed cumulative IEX daily volume for the current New York calendar day
divided by the mean raw IEX daily volume for exactly the preceding 30
calendar-confirmed trading sessions. The threshold uses integer multiplication,
without rounded-ratio decisions. Historical buckets must have reached the next
New York midnight and been received before evaluation; this is elapsed-bucket
and observed-version acceptance, not an assertion of immutable provider data.
Missing bars are unavailable, never zero-filled. The current cumulative volume
is intentionally partial-day; this is not same-time-of-day RVOL.

Matches rank by exact percentage change from the previous session's IEX daily
close, then symbol for ties. No 10% gain minimum, top-N cutoff or extra price
profile is introduced. News and float remain optional preferences. Neither
their absence nor EODHD's 20-call allowance vetoes this price/RVOL screen.

Discovery acceptance uses the exact active `us_equity` asset catalog returned by
Alpaca, including its literal identities, then requests every catalog symbol.
The independent discovery audit checks raw hashes, query/source identity,
clocks, exact partitions and explicit missing symbols. A completed request sweep
does not establish returned-data completeness. Returned snapshot objects do not
establish fresh fields, full-US coverage, historical membership, tradability or
SIP equivalence. All output retains `NOT_SIP_EQUIVALENT`.

The screen distinguishes `SCREEN_MATCH`, proven `RULE_NOT_MET`, and
`INPUT_UNAVAILABLE`. A valid observed price outside the mandatory range permits
a price-only rejection; missing or invalid price evidence cannot create that
rejection. Required inputs must match symbol/feed/raw/session and be available
by the recorded decision. Local raw capture precedes parsing; pagination must
terminate for each exact historical query. Later versions are new observations,
not retroactive replacements. Snapshot requests span time, so this evidence is
an offline observed-as-of screen, not a simultaneous market snapshot or accepted
execution-freshness contract.

Primary API semantics were checked against the official
[assets](https://docs.alpaca.markets/us/reference/get-v2-assets-1),
[calendar](https://docs.alpaca.markets/us/reference/legacycalendar),
[historical bars](https://docs.alpaca.markets/us/reference/stockbars), and
[market-data FAQ](https://docs.alpaca.markets/us/docs/market-data-faq).
Calendar early closes describe regular hours, not the full NY daily bucket;
`asof` controls symbol mapping, not knowledge-time availability. Token exhaustion
means that query had no further page at retrieval, not immutable market truth.

This component cannot authorize an entry, risk rule, order, formal paper study
or G03/G04 release. Full W2-05 remains open where it requires an independently
accepted executable strategy and qualified execution inputs. The three bounded
scanner deliverables should be assessed using the new contract, not blocked by
the still-unreconstructed Ross formula.

### Acceptance evidence

The [dated acceptance record](../evidence/w2-05-adapted-screen-acceptance-2026-10-15.json)
binds the implementation, test results and local raw archive. An actual
read-only sweep covered all 14,398 identities in the observed catalog. It
returned 13,479 snapshot objects and explicitly retained 919 omissions. Offline
evaluation recorded 31 `SCREEN_MATCH`, 7,386 `RULE_NOT_MET` and 6,981
`INPUT_UNAVAILABLE` outcomes; these sum to the catalog identity count. This is
actual captured-input evidence, separate from the synthetic threshold and error
fixtures. It is not a claim that every catalog symbol had qualified data.

Repeated replay reproduced the same result payload. Required raw inputs were
visible by the declared evaluation clock; the derived result remained invisible
until its own actual processing clock. Independent raw-data checks recomputed
a match, an RVOL rejection, a price-only rejection and exact identity partitions.
All requests were read-only Alpaca GETs; EODHD usage was zero. No order endpoint,
paid data, live-trading enablement or formal paper evaluation was involved.

## Closure work — 2026-10-15 Los Angeles

### Scanner contract review and remaining decisions

The current review removes an unnecessary condition: general stock-selection
criteria do not each have to explicitly name a $500 account. The
[$500 cash article](https://www.warriortrading.com/how-to-day-trade-with-500/)
supports low-float/trend-continuation study but supplies no complete numerical
intraday screen. Account-specific sizing, risk and execution still require their
own acceptance. The genuine scanner blocker is conflicting source contexts and
unfinished semantics, not the absence of "$500" beside each threshold.

Public-source retrieval and coordinator checks on October 15 distinguish these
reviewable profiles. This table is a research contract, not an enabled filter:

| Source context | Explicit selection content | Unresolved execution meaning |
| --- | --- | --- |
| [2025 toolkit opening, PDF pp. 1–2](https://cdn.warriortrading.com/warriortrading.com/assets/2025-Small-Account-Tool-Kit-1013.pdf#page=1) | Minimum $1, below $20; at least 5× RVOL; descending percentage change; news and float below 20m expressed as preferences. | RVOL window/session, non-veto preference handling, complete discovery/rank ties and observation clock. |
| [2025 toolkit detailed selection, PDF p. 8](https://cdn.warriortrading.com/warriortrading.com/assets/2025-Small-Account-Tool-Kit-1013.pdf#page=8) | At least 5× RVOL with a typically 30-day daily mean; at least 10% daily gain with a prior-day continuation exception; $1–20 preference and headline review. | Exact session/normalization, continuation detection, price exceptions and causal headline evaluation. |
| [2025 worksheet, PDF p. 4](https://cdn.warriortrading.com/warriortrading.com/assets/2025-Small-Account-Tool-Kit-1013.pdf#page=4) | $5–10; premarket top five plus positive catalyst; float below 20m hot / 10m cold. The price floor is motivated by leverage unavailable below $5. | Range endpoints, regime/catalyst definitions, premarket universe/ties and applicability of the leverage-dependent range to 1×. |
| [Later cash-account article](https://www.warriortrading.com/can-you-trade-in-a-cash-account/) | $2–20; at least 5× RVOL; float below 20m, narrowed below 5m when colder; linked $2,000 example. | Endpoints, RVOL window, regime and catalyst semantics; no precedence over the toolkit is established. |
| [General watchlist article](https://www.warriortrading.com/day-trading-watch-list-top-stocks-to-watch/) | Selection prose uses a 14-day volume average; displayed RVOL is a separate five-minute measure. Opening gap becomes fixed after 9:30, whereas change from prior close keeps moving. | These columns and teaching examples do not identify one reproduced toolkit scanner algorithm. |

The user selected the toolkit opening alone as the research focus on October 15:
$1 inclusive to $20 exclusive, 5× RVOL, and news/low float as preferences. The
detailed section, worksheet and cash article remain comparisons, not extra filters
added to it. This closes the research-profile choice and preference-role decision;
it does not freeze an executable strategy or settle RVOL/session/discovery gaps.
In particular, no 10% gain minimum, top-five cutoff, hot/cold switch or alternative
price range is inherited into the selected opening profile.

### Float update evidence and capacity acceptance

EODHD's [provider-maintained update reference](https://raw.githubusercontent.com/EodHistoricalData/eodhd-claude-skills/main/skills/eodhd-api/references/general/update-times.md)
(marked February 2026) describes daily US SharesStats updates with a typical
two-to-three-day lag and possible split delay. It explicitly calls much timing
guidance empirical and subject to change. It does not tie that schedule to Live
v2 `sharesFloat` or give a float-specific effective clock or maximum lag guarantee.
The [Live v2 schema](https://eodhd.com/financial-apis/live-v2-for-us-stocks-extended-quotes-2025)
still defines `timestamp` as a snapshot time. No two/three-day cache TTL or
snapshot-time freshness shortcut is accepted.

The exact unresolved provider questions are the Live v2 float source/pipeline,
corporate-action propagation, and an observable float clock or documented lag
bound. These questions are recorded for clarification; no message was sent to
the provider and no new authenticated API request was made during this review.

Capacity must be evaluated against an explicitly required lookup set after an
accepted source-scoped prefilter, only for evaluations actually using reference
fields. Missing preferred float remains an unavailable preference, not a failed
price/RVOL rule or an automatic stock rejection. More than 20 charged ticker observations exceed
the recurring free allowance; fewer can still exceed the actual remaining quota.
That balance remains unqueried. Welcome credit cannot prove recurring capacity.
No arbitrary top-20 cutoff, cache credit or estimated candidate count is accepted.
The pure capacity audit assesses one lookup per supplied identity; repeated
refreshes/retries cost additional units and are not a covered daily workload.
Its input balance is caller-reported, not a validated balance or runtime quota owner.

### Candidate/rejection acceptance sequence

1. Select the source profile, resolve required-versus-preferred criteria and pin
   the formula/session/exception semantics. Retain unresolved criteria explicitly.
2. Bind discovery scope, identities and required price/volume inputs to
   decision-time availability. Float/news preference observations keep their own
   availability and missing states; their absence is not a hard-filter rejection.
3. Measure required reference lookup demand without silently dropping symbols;
   assess recurring capacity and a separately evidenced remaining balance.
4. Replay both matching and nonmatching cases only for accepted rules/data.
   Keep `INPUT_UNAVAILABLE` distinct from a demonstrated `RULE_NOT_MET`; until
   prerequisites pass, report no qualified candidate rather than an invented
   strategy rejection. Historical gaps may require the predeclared prospective
   plan, never a backdated float observation or reused development holdout.

The [dated contract evidence](../evidence/w2-05-scanner-contract-review-2026-10-15.json)
records the user's focus and synthetic capacity cases. It also recomputes 14/30
full-day-mean hypotheses from the two already archived IEX samples with raw hash
and explicit session-date bindings. These are ex-post, all-eligible-hours numerical
comparisons: receipt occurred after the numerator session and provider finality
is unproven. They are not fresh scanner decisions or historical acceptance cases.
A synthetic counterexample crosses the 5× threshold under one window but not the
other, demonstrating why the windows cannot silently substitute for each other.
Neither hypothesis is accepted as the general profile's RVOL formula.

A further bounded check followed the actual links in the
[scanner manual](https://support.warriortrading.com/support/solutions/articles/19000117763)
(modified January 8, 2026): daily and five-minute RVOL columns link to the same
[generic terminology page](https://www.warriortrading.com/relative-volume-day-trading-terminology/).
That link does not specify the full calculation. The Ross-attributed
[technical-indicators article](https://www.warriortrading.com/technical-indicators/)
supports a same-time-of-day comparison but does not bind a lookback/session
definition to this toolkit opening. The remaining question is which column and
normalization apply, with which historical sessions, resets and adjustment rules.
No missing member-only reconstruction is required; these are explicit unresolved
formula semantics within the inspected public evidence.

Independent GPT-6.1 Sol Low retrieval supplied public evidence. A verified remote
Ollama Cloud `gemma4:31b` response was reviewed and rejected where it merged
profiles, reversed a hot/cold threshold, suggested snapshot-based float freshness,
or presented incomplete arithmetic. No model output supplied accepted filters.

Validation for this contract/capacity increment: 91 focused input/scanner/evidence
tests passed; the current workspace full suite passed 309 tests with one existing
upstream deprecation warning. Ruff, strict mypy and format checks passed for the
four changed source/test files. The full suite includes unrelated unfinished W3
changes, which remain outside this scoped work. None of these checks closes
W2-05/G03/G04.
Independent current-byte review approved the bounded contract/engineering scope,
reran the 91 focused tests and recomputed every capacity and RVOL comparison.
The 309-test full suite and static checks were coordinator-run.

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
| Source-scoped strategy/scanner specification | The user selected the toolkit opening research focus and news/float preference roles; other Ross contexts and Martin evidence stay separate. | The selected scanner still lacks accepted RVOL/session/discovery semantics and a complete executable strategy version. Other profiles' 10%/top-five/regime rules are not prerequisites for this selected focus. Account-specific risk/execution acceptance remains separate. |
| Required data | Actual broker catalog, two-symbol dated IEX history, public-demo float schema and a single authenticated QS float/cap response are verified. Explicit consistency failures now have deterministic reasons. | Required discovery/identity/price-volume/session availability still needs acceptance. Float-dependent preference enrichment additionally needs update semantics and adequate capacity; missing preferred float alone cannot reject a stock or fail price/RVOL. Twenty ticker calls/day cannot be assumed sufficient for that enrichment. |
| Candidate/rejection evidence | Input-quality rejection paths can be tested without inventing a strategy; raw inputs and dated diagnostics are retained. | No qualified-candidate/rejection replay for an accepted strategy has occurred. A missing input is unavailable, not a failed trading rule. G03/G04 remain open. |

Local key configuration and one QS access check are complete. Further float
preference-data acceptance requires documented update semantics and evidence that
its declared lookup demand fits verified capacity, or a separately verified adequate
source. This is not a universal hard float filter. Preserve
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
| Point-in-time share float | Neither examined Alpaca screener schema supplies share float. EODHD QS access is verified once, but broader coverage/update semantics are unaccepted. | In the selected opening research profile, missing preferred float is unavailable, not a hard-filter rejection. Any float-dependent preference assessment still needs eligible evidence. Shares outstanding and dollar public float are not substituted. |
| RVOL | Toolkit describes comparison with a typically 30-day daily average; a separate Ross article describes comparison at the same time of day. Existing 30/50-day full-day ratios remain descriptive. | Preserve source contexts; do not designate one as the reproduced scanner algorithm or mix SIP ranking volumes into IEX ratios. |
| Source-scoped rules | The user selected the opening research profile and its price bounds/preference roles. Other contexts retain their own unresolved endpoints, continuation and regime semantics without constraining this focus. | No cross-profile rule merging or executable version acceptance; selected-profile formula/data gaps remain explicit. |
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

Completion requires accepted source-scoped scanner specifications and eligible
discovery/price-volume inputs actually required by that selected profile, with
decision-time evidence. Float/news observations need eligible evidence when used
for their preference assessments; missing preferred inputs are not universal
qualification vetoes. The task remains incomplete pending its applicable
acceptance evidence.

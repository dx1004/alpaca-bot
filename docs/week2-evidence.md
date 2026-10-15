# Week 2 acceptance evidence

## Martin gap-closure and indicator semantics (2026-10-15 UTC)

The [follow-up closure table](no-leverage-pullback-research.md#closure-status-and-concrete-remaining-choices)
distinguishes additional firsthand short-study descriptions from the newer
long-profile unknowns and verified vendor semantics from unverified user settings.
The edited Martin study is registered as source inventory only; it has not been
added to the candidate's selected source IDs or runtime rules. Its 35% cap is not
imported over the newer interview's contrary evidence. A missing May post and
unreadable linked-video speech remain unresolved.

Coordinator exact-rational checks verified a synthetic VWAP non-equivalence
counterexample (normal-volume weighting 15; eligible-volume weighting 210/11)
and four hypothetical $500 risk/stop feasibility calculations. These are
mathematical checks, not observed market data, provider parity, replay acceptance
or accepted strategy parameters. No account/data purchase/order call occurred.

Locked/offline `pytest tests/test_evidence.py -q`: 9 passed. Local-link targets
and fragments, unchanged candidate source selection and `git diff --check` passed.
Independent quant/data review verified the guest-study provenance, official
Alpaca/TradingView distinctions and exact arithmetic, finding no actionable issue
in that scope. That reviewer did not run pytest or authenticate linked video/X
speech. No runtime code changed; no new replay, paper or W2-05 acceptance is claimed.

## Martin newer-interview reconstruction follow-up (2026-10-15 UTC)

The [authoritative research document](no-leverage-pullback-research.md#martin-newer-interview-reconstruction-2026-10-15-utc)
now records 20 separately attributed propositions from the supplied transcript
and slides, five competing hypotheses, changed/retained/unknown conditions,
development cases and negative cases, and a proposed source-time validation
sequence. The transcript SHA-256 and line positions bind the local attachment;
the approximate audio trim is not an authenticated YouTube time mapping.
Independent GPT-6.1 Sol Low information collection covered Martin evidence and
public teacher sources separately; actual public-only Cloud `gemma4:31b`
analysis and coordinator source checks preserved teacher/student attribution.
No attachment or browser screenshot was exported. A proposed original May X
post could not be retrieved; no missing wording was fabricated.

Deterministic checks verified exactly M01–M20, attachment digest, referenced
speaker-start line bounds, local links and the blocked execution boundary.
Locked/offline `pytest tests/test_evidence.py -q`: 9 passed; documentation diff
check passed. No runtime change or replay outcome is claimed. Independent
quant/model review matched all 20 propositions to the transcript and found one
hypothesis/falsifier mismatch. The corrected A1–A3 target individual case mappings
and distinguish retrospective interpretation from prospective usability. The
reviewer approved the correction with no remaining finding in scope; it did not
independently authenticate screenshots or teacher pages. All W2-05/strategy/data
release gates remain open and unrelated W3 changes remain untouched.

## W2-05 formula/source follow-up (2026-10-15 UTC)

The [Martin research follow-up](no-leverage-pullback-research.md#formula-follow-up-and-user-supplied-visuals-2026-10-15-utc)
records coordinator-verified TradingView volatility, turnover and average-volume
definitions, the earlier interview link, user-supplied slide/chart evidence and
the approximate audio-trim boundary. Independent GPT-6.1 Sol Low retrieval and
verified public-only Cloud `gemma4:31b` analysis were used; attachments were not
exported. Unknown original filter intervals, data coverage, point-in-time inputs
and strategy acceptance remain blockers. No runtime settings or TODO acceptance
checkbox changed. Locked/offline `pytest tests/test_evidence.py -q`: 9 passed.
Scoped documentation diff check passed. Unrelated W3 work remains untouched.

## W2-05 team follow-up — limited SIP ranking capture/replay (2026-10-14)

The implementation and unresolved acceptance dependencies are recorded in
[W2-05 scanner acceptance](w2-05-scanner-acceptance.md). Independent quant/news
and market-data primary-source retrieval, a verified public-only Cloud
`gemma4:31b` development proposal and coordinator source validation informed this
increment. The collector now captures exact bounded responses from the two
SIP-derived ranking endpoints; an independent journal-backed projector validates
the official `alpaca-py` v0.44.0 response contract, preserves provider ordering,
keeps source computation and local availability clocks separate and inserts a
limited-scope observation idempotently. It cannot qualify a strategy candidate,
generate premarket ranks, supply float, mix SIP volumes with IEX RVOL or establish
full-market scanner coverage.

Authenticated live schema validation used only two GETs with `top=1` in an
ephemeral local journal (probe date 2026-10-15 UTC). One most-actives row and two
mover rows passed the parser; raw payloads and the temporary journal were deleted.
Only redacted outcome/counts are recorded here. No account/order API, data purchase,
live trading or formal paper evaluation was used. Earlier generic probe statuses
remain historical; the pinned SDK and new field-level check resolve the narrow
response-schema research gap, not G03/G04 or W2-05's full acceptance.

Independent developer review identified future-provider-time leakage when a
one-nanosecond future timestamp was floored in the event envelope. The corrected
projector rejects such derived records, retains the raw response and audits the
rejection. Actual replay tests cover +1 ns, +1 second, delayed processing and
idempotency; a second independent review approved that correction. A read-only
worker's unavailable bare `python` command was isolated to that invocation;
resuming its original file inspection with `rtk read` succeeded. No global
installation or environment setting changed; project checks use `uv run` Python.

Final validation in the current working tree, using locked/offline dependencies:

```text
rtk proxy env UV_CACHE_DIR=/mnt/t/alpaca-bot/var/uv-cache uv run --locked --offline pytest -q
  -> 148 passed; one upstream websockets.legacy deprecation warning
rtk proxy env UV_CACHE_DIR=/mnt/t/alpaca-bot/var/uv-cache uv run --locked --offline pytest tests/test_replay.py tests/test_scanner_rankings.py -q
  -> 21 passed
rtk proxy env UV_CACHE_DIR=/mnt/t/alpaca-bot/var/uv-cache uv run --locked --offline ruff check src tests scripts
  -> passed
rtk proxy env UV_CACHE_DIR=/mnt/t/alpaca-bot/var/uv-cache uv run --locked --offline mypy --strict src tests
  -> passed, 46 source files
rtk proxy env UV_CACHE_DIR=/mnt/t/alpaca-bot/var/uv-cache uv run --locked --offline ruff format --check src tests scripts
  -> passed, 48 files
rtk git diff --check
  -> passed
```

W2-05 remains blocked on its original strategy/data dependencies. The unchanged
three source profiles do not resolve their RVOL, continuation, regime and
account-stage ambiguities; no verified point-in-time float source or complete
Ross discovery evidence was established. Synthetic tests and these two small
responses do not substitute for those inputs. Existing W3 changes were preserved;
the full-suite check does not grant W3 or paper release acceptance.

## W2-05 through W2-08 gate review (2026-10-13)

Week 1 dependency gate check: W1-05, W2-01, W2-02 and W2-04 are accepted, and W2-03's local transport/feed-health increment is checked in TODO. The Week 1 strategy and data gates are **not** accepted: both Ross and hybrid candidates remain research-only, G03/G04 remain open, and the current data matrix does not establish full-market SIP, point-in-time float, historical universe or IEX discovery eligibility. Therefore W2-05's current scanner path is blocked; no IEX-only full-market scanner or substitute thresholds were added. W2-05a remains deferred by the user.

### W2-06 raw news provenance increment (not strategy acceptance)

`src/alpaca_ross_bot/news_evidence.py` consumes a raw Alpaca news WebSocket frame only after the stream layer has journaled it as `RAW_FRAME`. It retains the provider fields and provider timestamps, captures local per-version availability, fingerprints exact canonical message content, preserves article-level first-seen time across exact repeats, and records changed versions as `REVISION_UNVERIFIED`. Created/updated ordering conflicts, provider timestamps later than local first-seen, malformed articles and unhealthy news transport state remain explicitly vetoed/blocked. Provider symbols are stored as reported, without ticker-symbol inference. Provider-created lag is diagnostic only: no source-approved age or catalytic threshold is chosen. Every record has `news_eligibility=BLOCKED_RESEARCH` and `market_reaction=NOT_EVALUATED`.

Independent GPT-6.1 Sol Low public-source retrieval and coordinator validation checked Alpaca's [streaming news schema](https://docs.alpaca.markets/us/docs/streaming-real-time-news), [historical news endpoint](https://docs.alpaca.markets/us/reference/news-3), and [historical news data description](https://docs.alpaca.markets/us/docs/historical-news-data), accessed 2026-10-13. The documented stream includes article ID, headline, summary, author, created/updated times, content, URL, symbols and source; REST news is sorted by updated date and paginated. Those references do **not** establish stable-ID revision behavior, revision history, replay/ordering/exactly-once semantics, publication-to-delivery bound, immutable coverage snapshot or per-symbol completeness. No public first-party Ross source found in this pass specifies machine-executable deduplication, news-age thresholds, catalyst/revision treatment, ticker mapping or market-reaction timing. These gaps block use as a catalyst pipeline. They do not prevent journaling provider facts in a fail-closed engineering layer.

Validation for the raw/provenance increment: `pytest tests/test_news_evidence.py -q` → 6 passed; full tests, deterministic replay, Ruff, strict mypy and formatting are recorded in the 2026-10-13 closeout below. The tests establish local engineering behavior only, not Alpaca source completeness, Ross fidelity, news eligibility or paper readiness.

### W2-08 replay/fidelity matrix

| Scenario | Evidence/test | W2 disposition |
| --- | --- | --- |
| DST and half-days | No accepted session-calendar source, session boundary semantics or candidate-version schedule. | `BLOCKED_RESEARCH`; cannot assert scanner/session behavior. |
| Trading halts | Stream supports raw status messages, but no accepted Ross/hybrid halt response, recovery, or entry-veto semantics. | `BLOCKED_RESEARCH`; no trade eligibility from transport capture. |
| Splits | No accepted point-in-time corporate-action source, adjustment policy or historical ticker/price transformation contract. | `BLOCKED_RESEARCH`; no adjusted series is presented as replay truth. |
| Missing float | W1-06 records point-in-time float availability as unknown; `test_float_provenance_and_short_rvol_history_stay_unverified` records missing/unverified provenance. Scanner is gated on an accepted strategy version. | `BLOCKED_RESEARCH`; no shares-outstanding or current-float substitution. |
| Old/late news | `test_news_capture_is_journaled_but_never_strategy_eligible` records provider-created lag; `test_old_news_age_bands_are_display_metadata_not_eligibility` exercises display bins and confirms they do not unblock articles. Replay tests keep events unavailable until local recorded availability. | Engineering timing passes; catalyst age/eligibility `BLOCKED_RESEARCH`. |
| Duplicate/revised news | `test_duplicate_does_not_refresh_first_seen_and_revision_is_unverified`; fingerprints and article state are journaled. | Exact byte-equivalent repeat retains first-seen; provider revision semantics and catalyst refresh remain `BLOCKED_RESEARCH`. |
| Conflicting/future news clocks | `test_timestamp_conflict_and_unhealthy_feed_are_vetoed` and `test_future_provider_timestamp_is_a_conflict`. | Conflict is recorded and remains ineligible. |
| Late/out-of-order events | `tests/test_events.py::test_late_source_timestamp_does_not_change_ingestion_order`, `tests/test_replay.py::test_out_of_order_source_times_do_not_reorder_ingestion_sequence`; future news versions are tested in `tests/test_replay.py`. | Recorded ingestion/availability ordering passes; this is not provider completeness proof. |

W2-08 acceptance is met as specified: implemented replay cases pass, and each unsupported strategy/data case is explicitly blocked with its missing evidence identified. This review does not unblock W2-05, W2-06 strategy eligibility, G03/G04, any strategy-order path, or formal paper evaluation. W2-07's later accepted component contract provides shadow-only code, not production inference connectivity or news approval.

2026-10-13 closeout checks (project-local offline cache):

```text
rtk proxy env UV_CACHE_DIR=/mnt/t/alpaca-bot/var/uv-cache uv run --locked --offline pytest -q  -> 84 passed, 1 upstream websockets deprecation warning
rtk proxy env UV_CACHE_DIR=/mnt/t/alpaca-bot/var/uv-cache uv run --locked --offline pytest tests/test_replay.py -q  -> 4 passed
rtk proxy env UV_CACHE_DIR=/mnt/t/alpaca-bot/var/uv-cache uv run --locked --offline ruff check src tests  -> All checks passed
rtk proxy env UV_CACHE_DIR=/mnt/t/alpaca-bot/var/uv-cache uv run --locked --offline mypy --strict src tests  -> no issues in 28 source files
rtk proxy env UV_CACHE_DIR=/mnt/alpaca-bot/var/uv-cache uv run --locked --offline ruff format --check src tests  -> 28 files already formatted
rtk proxy git diff --check  -> passed
```

### W2 follow-up component acceptance (2026-10-13)

The Week 1 dependency check remains unchanged: W1-05, W1-06 and W1-07 are accepted as capability-probe, inventory and blocked-gate-review tasks. Their evidence does not pass G03 or G04. The source-scoped Ross and hybrid versions are still not executable; Alpaca's official [movers](https://docs.alpaca.markets/us/reference/movers-1) and [most-actives](https://docs.alpaca.markets/us/reference/mostactives-1) endpoints use SIP, and the accepted W1-06 matrix still records IEX discovery and point-in-time float as unknown for this strategy. `NOT_SIP_EQUIVALENT` is mandatory.

#### W2-05 scanner measurement increment (not scanner eligibility)

`src/alpaca_ross_bot/scanner_observation.py` now journals measurements for an explicitly supplied, same-time IEX symbol set. It records price/prior-close source and timestamps, percent change, IEX feed identity, history availability/adjustment, float provenance status, and rank only within that supplied set. Thirty- and fifty-session volume ratios are exposed only as descriptive `session_volume_over_full_day_mean_not_time_normalized` measures; they are not Ross RVOL qualification. Every report says `NOT_SIP_EQUIVALENT`, `EXPLICIT_OBSERVED_SYMBOLS_ONLY`, `full_market_coverage=false` and `strategy_eligibility=BLOCKED_RESEARCH`. Future timestamps, feed mixing and duplicate symbols are rejected; missing float provenance is recorded as incomplete and remains blocked. The function does not discover symbols, apply candidate thresholds, validate float, or provide a trade path.

The public Ross materials reviewed for this project have differing price and float examples and do not resolve a single applicable $500/1x profile, a point-in-time universe, the exact RVOL formula/session normalization, or the scanner's full-market discovery contract. See the source-scoped gaps in [Ross public pullback research](ross-public-pullback-r0.md). Therefore the measurement projector is accepted as an engineering component only; W2-05 remains blocked. Tests: `tests/test_scanner_observation.py` covers rank scope, measurement basis, missing/unverified float, feed mismatch and future data.

Alpaca's official [Snapshots endpoint](https://docs.alpaca.markets/us/reference/stocksnapshots-1) accepts an explicit comma-separated symbol list and documents latest trade, quote, minute bar, daily bar and previous daily bar for each symbol; `feed=iex` is an explicit option. The [Assets endpoint](https://docs.alpaca.markets/us/reference/get-v2-assets-1) is a current asset master with status/class/exchange filters, not a point-in-time historical universe. These are potential observation inputs only. They do not promise complete fresh IEX observations or full-market Ross discovery, and the project has not made an authenticated REST call for this new scanner path.

`scanner_snapshot.py` now links a collected snapshot batch to its exact journaled raw response, verifies the response digest/requested symbols, and emits one idempotent event per returned symbol. Each event keeps that symbol's latest-trade source time in the event envelope and all provider bar/quote times and values in the raw snapshot; local first-seen and processing/availability remain separate. Future or malformed trade times are retained as conflicts/unknowns. The events explicitly have no ranking, retain `NOT_SIP_EQUIVALENT`, and remain `BLOCKED_RESEARCH`. This closes a per-symbol capture/replay integration gap only; it does not supply missing historical-volume/float inputs or accept scanner eligibility. Alpaca's [official OpenAPI snapshot schema](https://github.com/alpacahq/alpaca-docs/blob/master/oas/data/openapi.yaml) supports the response field names, but no cross-symbol synchronization guarantee is claimed.

Follow-up verification on 2026-10-13, locked and offline: snapshot/journal/data/news-recovery tests `26 passed`; full pytest `113 passed` (one upstream `websockets.legacy` deprecation warning); deterministic replay `4 passed`; Ruff check passed; strict mypy passed for 39 source files; Ruff format check passed for 39 files; `git diff --check` passed. All network calls were injected fakes; no Alpaca API request was sent.

#### W2-06 news metadata and W2-07 shadow evaluator

`news_evidence.py` now records Warrior's published headline-age display bands (0–2h, 2–12h, 12–24h, and over 24h) using the explicitly labeled approximation `Alpaca created_at` to local first-seen. Since the published ranges overlap at 2h, 12h and 24h and no boundary convention is documented, exact boundary timestamps produce `BOUNDARY_UNRESOLVED`; no cutoff is inferred. This is display metadata only; it does not represent an entry cutoff, catalyst determination or eligibility. The supporting first-party [Warrior scanner indicator documentation](https://support.warriortrading.com/support/solutions/articles/19000117763) and [news-layer evidence specification](news-layer-spec.md) explain this distinction. W2-06 remains blocked on source-scoped deduplication/revision, catalyst, delay, mapping and market-reaction semantics and verified source coverage.

W2-07's isolated component contract is accepted. `news_shadow.py` reads only one already-journaled Alpaca article whose formal eligibility is still blocked, sends a bounded compact payload through an injected model callback, validates exact evidence substrings and restricts symbols to provider symbols, and appends a separate idempotent `NEWS_AI_SHADOW` event. Timeout, errors and invalid output cannot change article eligibility; malformed or authority-shaped model output is rejected. There is no provider transport, account data, raw article content/URL, scanner, strategy, risk or execution interface in this module. This establishes a tested shadow component, not Ross news fidelity, production inference connectivity or approval of a future paper path.

The independent `news_market_context.py` helper joins one article version to pre/post `SCANNER_OBSERVATION` journal events only when the pre-event was available before article first-seen, the post observation follows it, and the exact provider symbol appears in both. It records source sequences, elapsed times and raw price/session-volume deltas with `NOT_SIP_EQUIVALENT`, `NOT_ASSESSED_TEMPORAL_ASSOCIATION_ONLY`, and `BLOCKED_RESEARCH`. It imposes no window, thresholds, remapping, catalyst decision or causal claim. Tests cover exact-symbol joins, delayed pre-news availability, unmatched symbols, sequence order and idempotency. The direct evidence is a deterministic event join, not proof that observed IEX prices represent a catalyst response.

The W2-08 review remains accepted under its stated rule: implemented synthetic replay and component cases pass, and unsupported DST/session, halt, split, float, strategy-news and full-market paths remain explicitly blocked in the matrix above. This is not a release or paper-evaluation gate pass.

Follow-up validation, locked and offline, in `/mnt/t/alpaca-bot`:

```text
rtk proxy env UV_CACHE_DIR=/mnt/t/alpaca-bot/var/uv-cache uv run --locked --offline pytest -q  -> 98 passed, 1 upstream websockets deprecation warning
rtk proxy env UV_CACHE_DIR=/mnt/t/alpaca-bot/var/uv-cache uv run --locked --offline pytest tests/test_replay.py -q  -> 4 passed
rtk proxy env UV_CACHE_DIR=/mnt/t/alpaca-bot/var/uv-cache uv run --locked --offline ruff check src tests  -> All checks passed
rtk proxy env UV_CACHE_DIR=/mnt/t/alpaca-bot/var/uv-cache uv run --locked --offline mypy --strict  -> no issues in 34 source files
rtk proxy env UV_CACHE_DIR=/mnt/t/alpaca-bot/var/uv-cache uv run --locked --offline ruff format --check src tests  -> 34 files already formatted
rtk git diff --check  -> passed
```

No Alpaca connection, account credential, order, paid data, live endpoint or formal paper evaluation was used.

### 2026-10-13 acceptance continuation: collection and evidence linkage

`scanner_data.py` adds a read-only, fixed-host REST collector for the current active US-equity asset master and caller-supplied IEX snapshot batches. It preserves the exact bounded response bytes, digest, query, local request/receipt times, parse result and response sequence in the journal. It rejects mismatched asset filters, malformed/duplicate asset symbols, duplicate/ambiguous requested symbols, unrequested response symbols and malformed JSON. Missing symbols are reported explicitly. Asset metadata is labeled as a current broker catalog, not a historical universe; snapshot coverage is limited to explicit symbols and always `NOT_SIP_EQUIVALENT`, `full_market_coverage=false`, `strategy_eligibility=BLOCKED_RESEARCH`. No request was sent to Alpaca; tests use an injected fake fetcher. This collector supplies evidence inputs only and does not discover or qualify Ross candidates.

`http_transport.py` consolidates the bounded GET implementation used by REST gap recovery and the scanner collector. It allows only HTTPS to the fixed Alpaca market-data and paper API hosts, rejects arbitrary/live trading hosts before network access, uses a monotonic request deadline and response byte cap, and only issues GET. The scanner asset query is metadata collection and does not enable a live trading endpoint. The remaining system resolver caveat for total wall-clock timing remains as previously documented for REST recovery.

The stream `ReceivedFrame` now carries its feed and the sequence assigned when `receive()` durably appends the raw frame. `append_received_frame` validates and reuses that existing journal event instead of duplicating it. The news wrapper requires a NEWS frame with a durable sequence, verifies exact raw bytes/feed/first-seen against the journal, and records the raw-frame reference on article evidence. Scanner measurements now separately record provider observation `as_of`, local `first_seen_at` and processing time; local availability cannot precede source observation. These changes prevent lost raw-frame provenance and remove the prior conflation of source time with local observation time.

`news_market_context.py` produces an idempotent temporal association from one journaled Alpaca article to pre/post exact-provider-symbol IEX observations. It verifies sequence, source-time and local-availability ordering; it records observed price and session-volume deltas only. It makes no ticker mapping, reaction-window choice, catalyst determination, threshold, or causal claim; result remains `NOT_ASSESSED_TEMPORAL_ASSOCIATION_ONLY` and `BLOCKED_RESEARCH`. This is not a Ross news rule, news-coverage proof, or strategy qualification.

The journal now offers a single-transaction conditional append keyed by a state record. It prevents concurrent workers from both inserting the same first-seen news fingerprint or market-context idempotency key; a losing concurrent news capture retains its already-journaled raw frame and adds a compact `NEWS_DUPLICATE_OBSERVED` record. Repeated content no longer overwrites the fingerprint's original first-seen state. A two-connection SQLite concurrency test verifies that only one conditional event is inserted.

Validation on 2026-10-13, locked and offline in `/mnt/t/alpaca-bot`:

```text
rtk proxy env UV_CACHE_DIR=/mnt/t/alpaca-bot/var/uv-cache uv run --locked --offline pytest -q  -> 109 passed, 1 upstream websockets deprecation warning
rtk proxy env UV_CACHE_DIR=/mnt/t/alpaca-bot/var/uv-cache uv run --locked --offline pytest -q tests/test_replay.py  -> 4 passed
rtk proxy env UV_CACHE_DIR=/mnt/t/alpaca-bot/var/uv-cache uv run --locked --offline ruff check src tests  -> All checks passed
rtk proxy env UV_CACHE_DIR=/mnt/t/alpaca-bot/var/uv-cache uv run --locked --offline mypy --strict src tests  -> no issues in 37 source files
rtk proxy env UV_CACHE_DIR=/mnt/t/alpaca-bot/var/uv-cache uv run --locked --offline ruff format --check src tests  -> 37 files already formatted
rtk git diff --check  -> passed
```

W2-08's acceptance review and the independently testable components are verified. W2-05 and W2-06 remain blocked at strategy eligibility by the documented Ross/hybrid profile and data/news semantics gaps; W2-03 component acceptance does not establish continuity or G04; G03/G04/G05 remain open. No Alpaca API request, account lookup, order, paid data, live trading, or formal paper evaluation was used.

### W2-05/06 source reconciliation (2026-10-13)

#### User decision: preserve conflicting Ross source versions in parallel (2026-10-13)

The user selected parallel source-version preservation for the conflicting Ross screening material. Keep each documented source/version's price, gain and relative-volume criteria as a separate research candidate with its own provenance and unresolved applicability; do not average, combine, or choose one as the $500/1× default. This authorizes parallel evidence tracking only. It does not accept a scanner rule, strategy version, Alpaca/IEX equivalence, or candidate eligibility. W2-05 remains `BLOCKED_RESEARCH` until each intended path has accepted source semantics and G03/G04 data eligibility; IEX observations continue to carry `NOT_SIP_EQUIVALENT`.

The scanner journal now carries three separate descriptive measurement records for each supplied observation: `ROSS_TOOLKIT_GENERAL_2025_SCREEN` (opening small-account screening), `ROSS_TOOLKIT_STOCK_SELECTION_2025_SCREEN` (detailed relative-volume/percentage-gain criteria), and `ROSS_TOOLKIT_WORKSHEET_2025_SCREEN` (challenge worksheet). The general record reports the $1 minimum, under-$20 preference, under-20M float preference, and a distinct human news-preference field. The detailed criteria record separately reports measured 5× RVOL and 10% daily-gain comparisons, the unresolved prior-day continuation exception, $1–$20 price preference, and catalyst-review requirement. The worksheet reports the $5–$10 price range, positive-catalyst requirement, 20M hot-market/10M cold-market float comparisons, and explicitly unavailable premarket rank and market-regime evidence. Range endpoints remain unresolved. Float comparisons preserve the numeric comparison but are labeled source-unverified. Every profile remains `BLOCKED_RESEARCH`; none produces a qualified candidate. RVOL is not time-normalized, and measurements do not establish $500/1× applicability or data eligibility. Tests verify source separation, missing-input labels, threshold measurements and fail-closed status. Current-catalog IEX limitations remain unchanged.

Independent GPT-6.1 Sol Low public-source retrieval, followed by coordinator verification of the first-party pages/PDF, confirms that the [2025 v2 Small Account Challenge toolkit](https://cdn.warriortrading.com/warriortrading.com/assets/2025-Small-Account-Tool-Kit-1013.pdf) contains distinct contexts: its opening small-account criteria discuss a $1 minimum, under-$20 preference, at least 5× relative volume, preferred news and float under 20M; its later worksheet specifies $5–10, top-five premarket gappers, a positive catalyst, and hot/cold-market float caps of 20M/10M. The detailed stock-selection section says 5× is typically calculated against a 30-day average and a minimum 10% daily gain has a prior-day continuation exception, but does not quantify that exception. It also says the catalyst is preferred and used to understand the move; causal headline review remains human-defined. A separate graphic includes a 50-day moving-average label, whose exact meaning is unresolved. These are source-scoped statements, not one reconciled $500/1× Alpaca rule profile; thresholds, section semantics and account-stage applicability must not be fused.

Warrior's [scanner support page](https://support.warriortrading.com/support/solutions/articles/19000117763), modified 2026-01-08, describes Top Gappers as the top 100 by percent gap versus prior close, updating every 30 seconds until 9:30 ET. It also says the Five Pillars list and alert do not enforce the news criterion, and the latter HOD momentum logic is not fully specified. This is evidence about Warrior's displayed product, not a published complete source universe or an Alpaca scanner specification. Alpaca's current [Trading API data tiers](https://docs.alpaca.markets/us/docs/about-market-data-api) describe Basic equities real-time as IEX, 30 streamed symbols, and Algo Trader Plus as all-exchange coverage. Therefore an IEX-only snapshot over explicitly supplied tickers cannot reproduce or claim Warrior's all-market ranking; retain `NOT_SIP_EQUIVALENT`.

For news, the [2025 toolkit](https://cdn.warriortrading.com/warriortrading.com/assets/2025-Small-Account-Tool-Kit-1013.pdf) says Ross checks the catalyst and then analyzes the daily chart. Warrior's [News Room guide](https://support.warriortrading.com/support/solutions/articles/19000131769-news-room-and-squawk-audio-alerts-wt), modified 2025-05-15, describes Day Trade Dash's News Desk posts and optional squawk; the [quote-widget guide](https://support.warriortrading.com/support/solutions/articles/19000129722-using-the-stock-quote-widget-and-cashtags-for-news-wt), modified 2025-07-24, displays headline age/color, posted timestamp and a link to the article/source, and explicitly cautions that news alone does not mean a stock should be traded. These establish a human headline-review workflow and UI metadata, not a deterministic sentiment, catalyst-quality, provider-equivalence, revision, ticker-mapping or allowable-age algorithm. The scanner support page further documents that news markers may trail alert scans by up to five minutes and list scans by their 30-second refresh; those UI delays are not provider delivery guarantees.

Accordingly the W2-05 price/change/RVOL observations remain measurements only; the W2-06 age bands and news-to-market event linkage remain descriptive only. Neither child can move to strategy eligibility from these sources. A public-only Ollama Cloud `gemma4:31b` review concurred that a complete $500/1× IEX scanner is unsupported; its suggestion that Warrior ranking weights were unspecified was rejected because the official scanner page specifies the top-100 gap ranking. No model output supplied a trading rule.

The bounded REST recovery path also captures historical Alpaca News pages. A source-attribution review found those records used the market-data `source_id` despite being journaled on the separate `alpaca_news` feed. News pages now use `source_id=alpaca_news`; the REST recovery contract test asserts both source and feed. This corrects provenance labeling only and does not establish entitlement, completeness, revision semantics, or news eligibility. Targeted verification: `pytest tests/test_rest_recovery.py -q` (4 passed), Ruff and strict mypy on the changed module/test, and `git diff --check` passed.

#### 2026-10-14 — read-only Alpaca SIP screener entitlement probe

The existing capability probe now includes Alpaca's official SIP-based `most-actives` and `movers` endpoints and supports `--sip-screeners-only`. That option selects exactly those two authenticated GETs and skips account, stock quote/trade, news and WebSocket checks. A valid JSON HTTP 200 is reported as `REACHABLE_SCHEMA_UNVERIFIED`, because Alpaca's public references do not document the response schema. It does not set `sip_equivalence_verified`, establish full-market scanner coverage, or qualify a Ross/hybrid candidate. HTTP 401/403/rate-limit/other responses remain separate statuses.

Alpaca documents `most-actives` as real-time SIP-ranked by volume or trade count (up to 100 results) and `movers` as real-time SIP-based (up to 50 gainers and 50 losers). The references do not specify Basic-plan entitlement for these endpoints. The movers page says that before the regular-session reset it returns the previous market day's results; it therefore cannot be treated as a premarket gap scanner. See Alpaca's [most-actives reference](https://docs.alpaca.markets/us/reference/mostactives-1) and [movers reference](https://docs.alpaca.markets/us/v1.4.2/reference/movers-1), reviewed 2026-10-14.

The account-scoped, narrowly configured probe returned `REACHABLE_SCHEMA_UNVERIFIED` for both endpoints using the locally stored paper credentials. This confirms the authenticated GETs returned valid JSON, but the probe intentionally discarded the response bodies; ranking records and response schema were not reviewed or retained. It establishes access to these two limited SIP-derived rankings only, not live SIP trade/quote/stream entitlement. `W2-05` remains blocked; no TODO acceptance checkbox changed. No account endpoint, order, purchase, or formal paper evaluation was used. Validation: `tests/test_capabilities.py` (7 passed), deterministic replay (4 passed), full suite (131 passed with one upstream deprecation warning), Ruff, strict mypy on 44 source files, format check on 46 files, and `git diff --check` passed. The existing AI news shadow evaluator already extracts source-linked factual spans and event types in an isolated, non-authoritative event; this follow-up made no change to news eligibility or AI authority.

## W2-01 — Event envelope and clocks (2026-10-05)

The immutable `EventEnvelope` in `src/alpaca_ross_bot/events.py` records source/feed/kind, an immutable payload reference, persistent ingestion sequence, optional source and provider timestamps, and required system first-seen, processing and decision-availability times. All timestamps must be UTC. Local chronology is `system_first_seen_at <= processed_at <= available_at`; sequence must be a positive integer, and journal slices must be contiguous in recorded order. `visible_at(decision_at)` checks only whether the event version was available by a decision time. It is not a market-data, news, or strategy eligibility decision. Conflicting external timestamps are retained and flagged instead of silently normalized or discarded.

Validation from `/mnt/t/alpaca-bot` using the project-local offline cache:

```text
rtk proxy env UV_CACHE_DIR=/mnt/t/alpaca-bot/var/uv-cache uv run --locked --offline pytest tests/test_events.py -q  -> 11 passed
rtk proxy env UV_CACHE_DIR=/mnt/t/alpaca-bot/var/uv-cache uv run --locked --offline ruff check src tests  -> All checks passed
rtk proxy env UV_CACHE_DIR=/mnt/t/alpaca-bot/var/uv-cache uv run --locked --offline mypy --strict src tests  -> no issues in 12 source files
rtk proxy env UV_CACHE_DIR=/mnt/t/alpaca-bot/var/uv-cache uv run --locked --offline pytest -q  -> 37 passed
```

W1's overall gate remains open; W2-01 depends only on the completed W1-03 evidence schema. No data feed, strategy order, or formal paper run was enabled.

## W2-02 — Append-only journal and transactional state (2026-10-05)

`src/alpaca_ross_bot/journal.py` stores each event envelope, its hash-checked raw payload and accompanying local state changes in one SQLite transaction. It requires the database path to be under the caller's runtime root, uses WAL with `synchronous=FULL`, assigns strictly consecutive sequence numbers and forbids updates/deletes to event rows with database triggers. Schema version 0→1 migration is transactional and unknown versions fail closed. This SQLite database is the canonical evidence/state owner; a compressed replay file can be exported later without becoming a second mutable authority.

Tests reopen the database after a committed write, inject a state-serialization failure after event insertion and verify rollback after reopening, reject sequence/payload mismatches, reject event mutation, and reject an outside-root path or unknown schema version.

```text
rtk proxy env UV_CACHE_DIR=/mnt/t/alpaca-bot/var/uv-cache uv run --locked --offline pytest tests/test_journal.py -q  -> 5 passed
rtk proxy env UV_CACHE_DIR=/mnt/t/alpaca-bot/var/uv-cache uv run --locked --offline pytest -q  -> 42 passed
rtk proxy env UV_CACHE_DIR=/mnt/t/alpaca-bot/var/uv-cache uv run --locked --offline ruff check src tests  -> All checks passed
rtk proxy env UV_CACHE_DIR=/mnt/t/alpaca-bot/var/uv-cache uv run --locked --offline mypy --strict src tests  -> no issues in 14 source files
```

No broker API, strategy gate, account state or paper order path was touched.

## W2-04 — Two replay timing modes (2026-10-05)

`src/alpaca_ross_bot/replay.py` exposes prospective and historical-research views over ingestion-ordered event envelopes. Prospective replay shows a version only after its recorded availability; conflicting external clocks and future source timestamps are omitted from decision views while remaining in the journal. Historical research may assume source timestamps for raw trade/quote observations, labeling each `ASSUMED_SOURCE_TIME`. Downloaded bars, news, revisions, ticker mappings and derived features require their own recorded availability. Historical frames are always `research_only`, and `require_recorded_timing()` rejects them even if a particular frame contains no assumed event. This proves only local timing, never market coverage or strategy fidelity.

Tests check no early exposure of future news versions, mappings or features; no intrabar historical bar assumption; no future raw trade; preserved ingestion order despite late source timestamps; conflict exclusion; and rejection of historical timing acceptance.

The user-selected Ollama Cloud `gemma4:31b` was verified by an actual public-only technical critique (`route=cloud`, returned model `gemma4:31b`, `success=true`). Its leakage warnings informed the tests. One suggestion was corrected: visible-event sets are nondecreasing across time, not necessarily *strictly* increasing when no event arrives. No local files, credentials or account data were sent to Cloud.

```text
rtk proxy env UV_CACHE_DIR=/mnt/t/alpaca-bot/var/uv-cache uv run --locked --offline pytest -q  -> 46 passed
rtk proxy env UV_CACHE_DIR=/mnt/t/alpaca-bot/var/uv-cache uv run --locked --offline ruff check src tests  -> All checks passed
rtk proxy env UV_CACHE_DIR=/mnt/t/alpaca-bot/var/uv-cache uv run --locked --offline mypy --strict src tests  -> no issues in 16 source files
```

W1-05 was later accepted as a read-only probe milestone, so W2-03 may proceed. W2-05, W2-05a, W2-06, W2-07 and W2-08 retain unmet strategy/data dependencies. No result from this synthetic replay contract is a Ross fidelity or paper-trading acceptance.

The existing W2–W4 Codex App heartbeat prompts were updated through the native automation tool to follow the latest AGENTS.md Astra Low/Ollama Cloud routing; W2 and W3 now permit only individually unblocked child tasks when earlier weekly gates remain open. Each automation stayed `ACTIVE` with its original recurrence and persistent target chat; saved settings were read back. The expired W1 schedule was not changed.

## W2-03 — Partial SDK and feed-health work (2026-10-07; not accepted)

The normal Codex sandbox command path was reverified after the user reported the earlier launcher fault fixed: `rtk read TODO.md` succeeded without escalation. No runtime repair was applied to the project. The official [`alpaca-py` v0.44.0](https://pypi.org/project/alpaca-py/0.44.0/) is now an exact locked dependency. Its tagged [stock stream](https://raw.githubusercontent.com/alpacahq/alpaca-py/v0.44.0/alpaca/data/live/stock.py) supports explicit IEX selection; its [news stream](https://raw.githubusercontent.com/alpacahq/alpaca-py/v0.44.0/alpaca/data/live/news.py) is separate. `feed_clients.py` only constructs those two SDK streams with paper credential references, raw data and `NOT_SIP_EQUIVALENT`. It performs no network I/O.

`feed_health.py` rejects a wrong feed, disconnected state, subscription from a prior connection epoch, unresolved gap, stale heartbeat/data and future local timestamps. It does not infer a news article from a quiet news stream. Its age limits must be supplied explicitly as engineering controls; no strategy threshold was selected. Synthetic tests establish these local vetoes only. An actual adapter must supply recorded subscription and recovery evidence before any eligibility claim. A successful SDK reconnect alone is not continuity proof; the tagged [shared stream source](https://raw.githubusercontent.com/alpacahq/alpaca-py/v0.44.0/alpaca/data/live/websocket.py) resubscribes but does not establish complete backfill. The tagged [REST source](https://raw.githubusercontent.com/alpacahq/alpaca-py/v0.44.0/alpaca/common/rest.py) does not set a bounded request timeout, so REST collection is not accepted until a tested bound is in place.

The public-only Ollama Cloud technical critique returned `route=cloud`, `model=gemma4:31b`, `success=true`; it highlighted that socket liveness, symbol delivery and gap closure are distinct. The coordinator checked this against the SDK sources and component tests. No local files, credentials or account data were sent to Cloud.

```text
rtk proxy env UV_CACHE_DIR=/mnt/t/alpaca-bot/var/uv-cache uv run --locked --offline pytest -q  -> 55 passed, 1 upstream websockets.legacy deprecation warning
rtk proxy env UV_CACHE_DIR=/mnt/t/alpaca-bot/var/uv-cache uv run --locked --offline ruff check src tests  -> All checks passed
rtk proxy env UV_CACHE_DIR=/mnt/t/alpaca-bot/var/uv-cache uv run --locked --offline mypy --strict src tests  -> no issues in 20 source files
rtk proxy env UV_CACHE_DIR=/mnt/t/alpaca-bot/var/uv-cache uv run --locked --offline ruff format --check src/alpaca_ross_bot/feed_clients.py tests/test_feed_clients.py src/alpaca_ross_bot/feed_health.py tests/test_feed_health.py  -> 4 files formatted
```

W2-03 remains open: live subscription acknowledgment, reconnect generation tracking, bounded REST backfill and completeness proof, raw-event journal integration, stale/gap recovery validation and read-only SDK probe parity have not passed. No orders or formal paper evaluation were attempted.

### W2-03 source review update (2026-10-08)

An independent GPT-6.1 Sol Low public-source retrieval reviewed the official v0.44.0 release, tagged SDK source and Alpaca protocol/API references. The tagged [stream implementation](https://raw.githubusercontent.com/alpacahq/alpaca-py/v0.44.0/alpaca/data/live/websocket.py) consumes connected/authenticated and subscription control frames internally; `_running` becomes true after sending the subscription request, before an acknowledgment. It automatically reconnects and resubscribes, but provides no public reconnect or acknowledgment callback. The [protocol documentation](https://docs.alpaca.markets/us/docs/streaming-market-data) confirms the server does send subscription acknowledgments, so observing them requires a version-bound lower-level adapter or an independently implemented protocol client. The [tagged REST implementation](https://raw.githubusercontent.com/alpacahq/alpaca-py/v0.44.0/alpaca/common/rest.py) does not pass a timeout to Requests; its history pagination aggregates results and does not return a completion token, so hitting a requested limit cannot be treated as complete recovery.

Coordinator inspection of the installed pinned source confirmed these claims. A separate public-only Ollama Cloud `gemma4:31b` critique independently highlighted the callback blind spot and timeout risk; its suggested sequential comparison/buffering requirement was not adopted as fact because the Alpaca sources inspected do not establish a universal tape sequence or complete-coverage proof. The implementation direction is now set: use a small project-owned client over Alpaca's documented JSON WebSocket protocol so connected/authenticated/subscription frames and disconnects are observable; use a bounded REST transport that exposes page exhaustion. Keep the official SDK pinned only for components whose behavior is bounded and observable. This is an evidence-driven exception to preferring the SDK for streams. Incomplete backfill or unobservable continuity must keep the feed blocked.

Validation rerun on 2026-10-08: full suite `55 passed` (one upstream `websockets.legacy` deprecation warning), deterministic replay suite `4 passed`, `ruff check src tests` passed, `mypy --strict src tests` passed with no issues in 20 source files, scoped Ruff format check passed, and `git diff --check` passed. These checks validate the current component state; they do not close the live transport acceptance gaps above.

### W2-03 project-owned protocol increment (2026-10-09; not accepted)

The project now has `AlpacaJsonStream` for explicitly selected IEX equities or the separate Alpaca news endpoint. Construction does no I/O. On an explicit `open()`, it authenticates with the configured paper API headers and requires Alpaca's `connected` then `authenticated` success controls within a caller-supplied bound. `subscribe()` takes explicit channels/symbols, sends one request, and accepts data only after the single-message subscription control exactly matches the request. Unsupported channels, malformed controls, mismatched state, server errors and timeouts close the socket fail-closed. `receive()` preserves the raw JSON frame and stamps local UTC first-seen time; it rejects malformed frames and protocol error controls. The adapter tracks connection and subscription epochs separately, marks a continuity gap unresolved when a subscribed socket closes, and never clears it on reconnect. Raw frames can be appended to the SQLite journal as immutable `RAW_FRAME` envelopes with a transactionally assigned sequence. Equities retain `NOT_SIP_EQUIVALENT`; news is labeled as a separate Alpaca stream.

The current primary protocol reference ([Alpaca WebSocket stream docs](https://docs.alpaca.markets/us/docs/streaming-market-data), accessed 2026-10-09) documents header or message authentication, success/authentication controls, full subscription-state acknowledgments, single-message control arrays, and data arrays that may batch records. Independent GPT-6.1 Sol Low retrieval and coordinator review on 2026-10-09 checked the [stream message contract](https://docs.alpaca.markets/us/docs/streaming-market-data#format), [stock schemas](https://docs.alpaca.markets/us/docs/real-time-stock-pricing-data), [news schema](https://docs.alpaca.markets/us/docs/streaming-real-time-news#schema), and pinned [`alpaca-py` v0.44.0 stream source](https://github.com/alpacahq/alpaca-py/blob/v0.44.0/alpaca/data/live/websocket.py). No documented Alpaca application heartbeat, contiguous server delivery sequence, or reconnect replay guarantee was found. The SDK uses transport ping/pong and an optional local receipt-age timeout. This client therefore uses explicit caller-supplied ping interval/timeout settings; its `last_heartbeat_at` is local pong receipt time, not a provider timestamp or proof of fresh data. Separate `last_data_at` freshness remains required. A missed pong drops the subscription and opens an unresolved gap; reconnect alone never clears it. Cloud suggestions about epoch arithmetic or message ordering were not accepted as continuity guarantees without primary-source evidence.


`AlpacaRestRecovery` provides explicit `/v2/stocks/trades`, `/v2/stocks/quotes`, and `/v1beta1/news` page retrieval. It requires fixed UTC start/end bounds and symbols; IEX is forced for equity endpoints, while news remains separately labeled. It applies per-socket timeouts using the remaining monotonic query deadline, explicit response byte and page caps, and preserves each raw page with both requested and next-page tokens. It aborts on bad/repeated tokens, malformed payloads, non-200 responses, timeouts or an exhausted page budget; `pagination_exhausted` is returned only when the API supplies no next token. The deadline cannot bound a stalled operating-system DNS resolver. All results keep `continuity_proven=false`: Alpaca's docs describe pagination but do not promise an immutable snapshot, no-late-arrivals guarantee, or exact reconstruction of WebSocket delivery. News `start`/`end` semantics are unresolved in the public API reference (which says sorting is by updated date), so news page exhaustion is not accepted as revision/continuity recovery.

Independent GPT-6.1 Sol Low public-source retrieval and coordinator review on 2026-10-09 checked the current [trades](https://docs.alpaca.markets/us/v1.1/reference/stocktrades-1), [quotes](https://docs.alpaca.markets/us/reference/stockquotes-1), and [news](https://docs.alpaca.markets/us/reference/news-3) API references and the pinned [`alpaca-py` v0.44.0 REST source](https://raw.githubusercontent.com/alpacahq/alpaca-py/v0.44.0/alpaca/common/rest.py). The stock references list IEX/SIP feed selection, a 1–10,000 page limit, and `next_page_token`; the news reference lists a 1–50 page limit and token, and says sort is by updated date. The pinned SDK paginates automatically but can stop at a requested total item limit and returns an aggregated response without exposing the cursor; its Requests options do not include a finite timeout. We therefore use project-owned token-visible HTTP retrieval for this gap-investigation component, not SDK pagination. A separate public-only Ollama Cloud `gemma4:31b` technical analysis was verified on 2026-10-09; its claims were checked against primary docs, and its overstatement that token exhaustion proves complete underlying records was rejected. Exhaustion here means only the API's response for this exact bounded query had no next token at retrieval time.

Synthetic verification for this increment: stream/client/health, REST pagination, raw journal capture and existing replay suites have been rerun separately (see final validation below). REST tests use injected pages and do not contact Alpaca. No Alpaca network connection or credential file was used.

W2-03 remains open. The helper can journal raw WebSocket frames, but no live consumer is wired to call it, and REST result pages/query metadata are not yet journaled. Connection epochs and an in-memory unresolved gap are tracked, but neither survives process restart; there is no accepted gap-closure evidence. REST pagination exhaustion is not backfill proof. Read-only SDK-probe parity is also open. `health_snapshot()` exposes local transport-pong and data-receipt clocks to the existing fail-closed `FeedHealth` reasons without choosing freshness limits. A successful handshake or pong alone cannot establish data eligibility, and strategy/news/scanner gates remain blocked.

Final local verification on 2026-10-09 (project-local, locked, offline environment): full pytest `72 passed` (one upstream `websockets.legacy` deprecation warning); deterministic replay tests `4 passed`; `ruff check src tests` passed; `mypy --strict src tests` passed for 26 source files; `ruff format --check src tests` passed for 26 files; `git diff --check` passed. All new network behavior was tested with synthetic socket/page fetchers. No Alpaca connection, credential lookup, order, purchase, live endpoint, or formal paper evaluation was attempted. The repository files currently report as untracked in Git, so this partial W2-03 increment is not committed.

### W2-03 acceptance completion (2026-10-13)

The 2026-10-09 gaps above are now closed for the component acceptance contract. Production factory construction for the project-owned stream and REST collector requires the canonical `Journal`. Once subscribed, every raw WebSocket frame is appended before it is returned to a caller. Before sending a subscription request, the stream durably records a session-attempt marker. A process restart that finds a session still marked active records `FEED_GAP` and latches `gap_unresolved`; disconnects, protocol errors after subscription, missed pong and normal close also latch and journal the gap. Reconnect and REST pagination never clear this state. If journaling fails, the stream fails closed. Synthetic crash/restart tests verify the gap survives reopening SQLite and blocks `FeedHealth` after a new successful handshake/subscription.

Every REST page successfully received is journaled with its exact query bounds, symbols, page size, requested/next tokens, local request/receipt timestamps, raw response bytes and `continuity_proven=false`. A received page with malformed JSON/schema is also retained with a parse error and is not marked pagination-exhausted. The bounded collector still fails on HTTP errors, request/response bounds, repeated tokens, parse errors or page-budget exhaustion. Equities remain `NOT_SIP_EQUIVALENT`; news remains a separate Alpaca feed. SDK probe construction tests confirm the pinned SDK targets IEX and the separate news endpoint, but no live SDK parity probe was performed; hidden SDK control/reconnect behavior is not relied on for feed health.

Alpaca's documented page-token completion is not evidence of a complete immutable snapshot or WebSocket continuity. Accordingly this acceptance verifies the required veto: wrong feed, stale clock/data, unacknowledged subscription, disconnect and any unresolved gap block health; no cache or reconnect can clear them. It does **not** accept backfill as continuity proof, clear a feed gap, establish G04 data eligibility, or authorize scanner/news/strategy use, orders, or paper evaluation.

Verification on 2026-10-13, locked and offline: targeted stream/REST/client/journal tests `30 passed`; full pytest `78 passed` (one upstream `websockets.legacy` deprecation warning); deterministic replay `4 passed`; Ruff check passed; strict mypy passed for 26 source files; format check passed for 26 files; `git diff --check` passed. Tests use injected socket/page transports; no Alpaca connection, account credential, order, paid data, live endpoint or formal paper evaluation was used. W2-03 is accepted at the local component-contract level only.

### W2-06 local REST article replay observations (2026-10-13)

`news_rest_observation.py` adds one idempotent `NEWS_REST_ARTICLE_OBSERVATION` reference for each record in a journaled Alpaca News REST page. Each child points to the immutable parent page sequence and article index, records a digest of that record, and retains the query interval and local page receipt time. The original page remains the raw data authority. Child events carry no provider source-time or receive-time clock; their replay availability is the local processing time. Re-running the same projection is idempotent. These events do not merge into WebSocket article fingerprint/first-seen state, and every projected record is labeled `BLOCKED_RESEARCH`.

Tests verify the child is invisible at page-receipt time and visible only after processing, preserves parent/index linkage, rejects wrong-parent, early-processing and malformed-record inputs, and creates no duplicate on repeat. This allows historical REST material to be inspected only from the time it was locally retrieved and projected. It does not reconstruct when an article was first available to a live trader, establish REST revision/order completeness, qualify catalysts, or change W2-06's blocked strategy/data disposition.

Verification after this increment, locked and offline: targeted News REST/recovery/replay tests `10 passed`; full pytest `115 passed` (one upstream `websockets.legacy` deprecation warning); deterministic replay `4 passed`; Ruff and strict mypy passed (41 source files); format check passed (41 files). No Alpaca connection, credential read, order, paid data, live endpoint, or formal paper evaluation was used.

### W2-05 current-catalog IEX observation sweep (2026-10-13)

First-party Alpaca references were rechecked on 2026-10-13: [`GET /v2/assets`](https://docs.alpaca.markets/us/reference/get-v2-assets-1) is the current asset master for Alpaca trading/data consumption; [`GET /v2/stocks/snapshots`](https://docs.alpaca.markets/us/reference/stocksnapshots-1) returns snapshot fields for explicitly supplied comma-separated symbols and accepts `feed=iex`; the [Basic plan table](https://docs.alpaca.markets/us/docs/about-market-data-api) limits real-time equities to IEX and documents 30 WebSocket symbols, while the stock snapshot page provides no numeric per-request symbol cap. Alpaca's [movers](https://docs.alpaca.markets/us/reference/movers-1) and [most-actives](https://docs.alpaca.markets/us/reference/mostactives-1) references specify real-time SIP-based rankings, not an IEX selection.

The independent GPT-6.1 Sol Low public-source retrieval and coordinator verification found no documented wildcard for REST snapshots, numeric stock-snapshot batch cap, or proof that Alpaca's current asset master is a complete all-exchange scanner universe. The public-only Ollama Cloud `gemma4:31b` analysis was also checked against these sources; its broad distinction between explicit-symbol snapshots and SIP rankings was consistent, but its endpoint-path wording was not relied on. No request limit, cadence, freshness threshold, Ross rule or SIP equivalence was inferred.

`AlpacaScannerData.collect_current_asset_iex_snapshots(max_symbols_per_batch=...)` now walks every symbol returned by the current active-equity asset catalog, issues explicit IEX snapshot requests using the caller-supplied batch size, journals each raw/parsed batch, emits per-symbol replay observations through the existing provenance bridge, and writes a manifest with missing symbols. The batch size has no default because no numeric REST batch maximum is documented. Any failed request aborts the run without a completion manifest; observed omissions remain explicit. Tests use injected responses and verify exact symbol partitioning, absent-symbol reporting, replay observation creation, and `NOT_SIP_EQUIVALENT`/`full_market_coverage=false`/`BLOCKED_RESEARCH` labels.

This is an automated current-catalog IEX observation sweep, not a Ross scanner: it does not rank by gainers/RVOL, include non-Alpaca or historical symbols, infer float, prove every symbol has fresh data, or qualify candidates. The scanner feature child stays blocked pending an accepted strategy version and G03/G04 data eligibility. No live request, order, paid data, or formal paper evaluation was made.

Verification for the scanner increment, locked and offline: targeted scanner measurement/snapshot tests `17 passed`; full pytest `117 passed` at that point (one upstream `websockets.legacy` deprecation warning); deterministic replay `4 passed`; Ruff and strict mypy passed (41 source files); format check passed (41 files); `git diff --check` passed. The compact full-suite command stalled without summary output and was interrupted; the verbose `pytest -vv -x` run then completed with all 117 tests passing. All network responses were injected; no Alpaca API request, account lookup, order, paid data, live trading, or formal paper evaluation occurred.

### W2-06 raw-frame provenance enforcement (2026-10-13)

Review found that `capture_news_frame` documented a pre-journaled raw frame but allowed `raw_frame_sequence=None`. The function now requires the sequence and checks that its journal entry is a News `RAW_FRAME` with matching first-seen time and exact payload bytes before producing article or rejection events. Stream callers already provide the durable sequence through `capture_received_news_frame`; test fixtures now create explicit raw-frame events, and a regression test confirms that omitting the reference fails without writing an article event. This closes a local bypass in the provenance contract; it does not prove the provider's delivery completeness or news strategy eligibility.

Latest locked/offline validation after both W2 follow-ups: full pytest `118 passed` (one upstream `websockets.legacy` deprecation warning), deterministic replay `4 passed`, Ruff passed, strict mypy passed for 41 source files, and all 41 files passed format checking. No external data request or broker action was made.

### W2-05 current-catalog IEX observation sweep (2026-10-13)

First-party Alpaca references were rechecked on 2026-10-13: [`GET /v2/assets`](https://docs.alpaca.markets/us/reference/get-v2-assets-1) is the current asset master for Alpaca trading/data consumption; [`GET /v2/stocks/snapshots`](https://docs.alpaca.markets/us/reference/stocksnapshots-1) returns snapshot fields for explicitly supplied comma-separated symbols and accepts `feed=iex`; the [Basic plan table](https://docs.alpaca.markets/us/docs/about-market-data-api) limits real-time equities to IEX and documents 30 WebSocket symbols, while the stock snapshot page provides no numeric per-request symbol cap. Alpaca's [movers](https://docs.alpaca.markets/us/reference/movers-1) and [most-actives](https://docs.alpaca.markets/us/reference/mostactives-1) references specify real-time SIP-based rankings, not an IEX selection.

The independent GPT-6.1 Sol Low public-source retrieval and coordinator verification found no documented wildcard for REST snapshots, numeric stock-snapshot batch cap, or proof that Alpaca's current asset master is a complete all-exchange scanner universe. The public-only Ollama Cloud `gemma4:31b` analysis was also checked against these sources; its broad distinction between explicit-symbol snapshots and SIP rankings was consistent, but its endpoint-path wording was not relied on. No request limit, cadence, freshness threshold, Ross rule or SIP equivalence was inferred.

`AlpacaScannerData.collect_current_asset_iex_snapshots(max_symbols_per_batch=...)` now walks every symbol returned by the current active-equity asset catalog, issues explicit IEX snapshot requests using the caller-supplied batch size, journals each raw/parsed batch, emits per-symbol replay observations through the existing provenance bridge, and writes a manifest with missing symbols. The batch size has no default because no numeric REST batch maximum is documented. Any failed request aborts the run without a completion manifest; observed omissions remain explicit. Tests use injected responses and verify exact symbol partitioning, absent-symbol reporting, replay observation creation, and `NOT_SIP_EQUIVALENT`/`full_market_coverage=false`/`BLOCKED_RESEARCH` labels.

This is an automated current-catalog IEX observation sweep, not a Ross scanner: it does not rank by gainers/RVOL, include non-Alpaca or historical symbols, infer float, prove every symbol has fresh data, or qualify candidates. The scanner feature child stays blocked pending an accepted strategy version and G03/G04 data eligibility. No live request, order, paid data, or formal paper evaluation was made. Validation for this increment is recorded with the current commit after the full-suite run.

### W2-02 concurrent first-open migration and W2-05 parallel source measurements (2026-10-13)

The full-suite audit exposed a race when two SQLite journal connections first opened the same version-0 database: each could read `user_version` before obtaining the migration write lock, and the concurrent test's barrier had no timeout. Journal migration now takes `BEGIN IMMEDIATE` before reading the schema version, so a second opener observes the committed version after the first migration; WAL mode is enabled after migration. The concurrency test synchronizes both initial opens and both appends, with bounded barriers, and verifies exactly one idempotent event/state winner.

W2-05 now adds distinct descriptive observation objects for `ROSS_TOOLKIT_GENERAL_2025_SCREEN` and `ROSS_TOOLKIT_WORKSHEET_2025_SCREEN`. Their source IDs and criteria remain separate. General toolkit news remains a human preference field, while the worksheet's positive-catalyst requirement is a separate unavailable criterion. Numeric RVOL and price comparisons are measurements only; float comparisons preserve the numeric result but remain source-unverified. Missing premarket rank, catalyst and market regime are explicit; both records always retain `BLOCKED_RESEARCH` eligibility. The source-scoped records do not create an executable scanner, establish the full market universe, or alter `NOT_SIP_EQUIVALENT`.

Locked/offline validation: full pytest `118 passed` (one upstream `websockets.legacy` deprecation warning); deterministic replay `4 passed`; Ruff passed; strict mypy passed for 42 source files; all 42 files passed format checking; `git diff --check` passed. No Alpaca request, order, data purchase, live trading or formal paper evaluation occurred.

### W2-06 provider-field limits retained in article records (2026-10-13)

News article records now carry explicit interpretation limits alongside Alpaca's `created_at`, `updated_at`, `symbols`, and `source` values. The official [stream schema](https://docs.alpaca.markets/us/docs/streaming-real-time-news) describes created/updated article times and symbols as related or mentioned; it does not define these as publication/first-availability time, a complete revision archive, issuer attribution, or independently verified origin. The implementation therefore labels those limits in each normalized record. This is provenance metadata only: it does not change duplicate handling, age display, news eligibility (`BLOCKED_RESEARCH`), or the unresolved catalyst path.

Validation: `pytest tests/test_news_evidence.py -q` plus full locked/offline tests, deterministic replay, Ruff, strict mypy, format check and `git diff --check` are recorded with the commit. No Alpaca request, credential lookup, order, paid data, live trading or formal paper evaluation occurred.

The same article record now also carries three independent source-scoped news-rule observations: the general toolkit's news preference (news absence is not a hard filter), the detailed stock-selection section's preferred catalyst/headline-explanation review, and the challenge worksheet's positive-catalyst requirement. Their provider-to-move interpretation remains `UNASSESSED`; each profile stays `BLOCKED_RESEARCH`. These treatments are supported by the [toolkit's opening screening section](https://cdn.warriortrading.com/warriortrading.com/assets/2025-Small-Account-Tool-Kit-1013.pdf#page=1), [detailed stock-selection section](https://cdn.warriortrading.com/warriortrading.com/assets/2025-Small-Account-Tool-Kit-1013.pdf#page=7), and [challenge worksheet](https://cdn.warriortrading.com/warriortrading.com/assets/2025-Small-Account-Tool-Kit-1013.pdf#page=3). This preserves distinct source claims; it does not classify an Alpaca article as causal, positive, fresh, or eligible.

### W2-05 detailed screening measurements preserved (2026-10-13)

The detailed [2025 v2 toolkit stock-selection section](https://cdn.warriortrading.com/warriortrading.com/assets/2025-Small-Account-Tool-Kit-1013.pdf#page=7) now has its own `ROSS_TOOLKIT_STOCK_SELECTION_2025_SCREEN` measurement record, separate from the opening toolkit profile and challenge worksheet. It reports the measured 5× 30-day RVOL and 10% daily-gain comparisons; the source-described continuation exception is `UNAVAILABLE` because prior-day move size and holding-price rules are undefined. Price preference and catalyst-review fields remain separate, with causal headline review unavailable to deterministic code. Each record stays `BLOCKED_RESEARCH` and cannot qualify a trade.

This coding increment follows independent GPT-6.1 Sol Low source retrieval and coordinator verification of the linked primary PDF. The source gives the 30-day average-volume basis and 10% minimum with a qualitative prior-day continuation exception; it does not define the continuation threshold. The PDF's later graphic has an additional 50-day label whose exact semantics remain unresolved. Cloud `gemma4:31b` returned a generic profile-isolation/fail-closed design suggestion; only the source separation and no-eligibility invariant were adopted, while all thresholds and source claims were checked against the PDF itself.

Validation: `pytest tests/test_scanner_observation.py -q` → 6 passed; full suite → 119 passed (one upstream `websockets.legacy` deprecation warning); deterministic replay → 4 passed; Ruff passed; strict mypy passed for 42 source files; all 42 files passed formatting; `git diff --check` passed. No external data request, broker action, paid data or formal paper evaluation occurred.

### W2-06 Ross news treatments retained by source (2026-10-13)

Each normalized Alpaca article now includes separate observations for the general toolkit's preferred-news treatment, the detailed stock-selection section's catalyst/headline review, and the challenge worksheet's positive-catalyst requirement. These three first-party source treatments are independently preserved from the [opening screen](https://cdn.warriortrading.com/warriortrading.com/assets/2025-Small-Account-Tool-Kit-1013.pdf#page=1), [detailed selection criteria](https://cdn.warriortrading.com/warriortrading.com/assets/2025-Small-Account-Tool-Kit-1013.pdf#page=7), and [worksheet](https://cdn.warriortrading.com/warriortrading.com/assets/2025-Small-Account-Tool-Kit-1013.pdf#page=3). Because Alpaca provider article fields do not determine whether a headline caused a particular move or satisfy the worksheet's positive-catalyst test, each source-specific result is `UNASSESSED` and each strategy path remains `BLOCKED_RESEARCH`.

Validation after this W2-06 increment: `pytest tests/test_news_evidence.py -q` → 11 passed; full suite → 119 passed (one upstream `websockets.legacy` deprecation warning); deterministic replay → 4 passed; Ruff passed; strict mypy passed for 42 source files; all 42 files passed formatting; `git diff --check` passed. No Alpaca request, credential lookup, order, paid data, live endpoint or formal paper evaluation occurred.

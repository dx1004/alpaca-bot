# Week 2 acceptance evidence

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

Independent GPT-6.1 Sol Low public-source retrieval, followed by coordinator verification of the first-party pages/PDF, confirms that the [2025 v2 Small Account Challenge toolkit](https://cdn.warriortrading.com/warriortrading.com/assets/2025-Small-Account-Tool-Kit-1013.pdf) itself contains distinct contexts: its opening small-account criteria discuss a $1 minimum, under-$20 preference, at least 5× relative volume, preferred news and float under 20M; its later worksheet specifies $5–10, top five premarket gappers, a positive catalyst, and hot/cold-market float caps of 20M/10M. The toolkit defines RVOL using typical 30-day average volume in its general criteria but also contains a 50-day graphic label. The separate stock-selection criteria add +10% daily gain and a qualitative prior-day continuation exception. These are source-scoped statements, not one reconciled $500/1× Alpaca rule profile; the thresholds and account-stage applicability must not be fused.

Warrior's [scanner support page](https://support.warriortrading.com/support/solutions/articles/19000117763), modified 2026-01-08, describes Top Gappers as the top 100 by percent gap versus prior close, updating every 30 seconds until 9:30 ET. It also says the Five Pillars list and alert do not enforce the news criterion, and the latter HOD momentum logic is not fully specified. This is evidence about Warrior's displayed product, not a published complete source universe or an Alpaca scanner specification. Alpaca's current [Trading API data tiers](https://docs.alpaca.markets/us/docs/about-market-data-api) describe Basic equities real-time as IEX, 30 streamed symbols, and Algo Trader Plus as all-exchange coverage. Therefore an IEX-only snapshot over explicitly supplied tickers cannot reproduce or claim Warrior's all-market ranking; retain `NOT_SIP_EQUIVALENT`.

For news, the [2025 toolkit](https://cdn.warriortrading.com/warriortrading.com/assets/2025-Small-Account-Tool-Kit-1013.pdf) says Ross checks the catalyst and then analyzes the daily chart. Warrior's [News Room guide](https://support.warriortrading.com/support/solutions/articles/19000131769-news-room-and-squawk-audio-alerts-wt), modified 2025-05-15, describes Day Trade Dash's News Desk posts and optional squawk; the [quote-widget guide](https://support.warriortrading.com/support/solutions/articles/19000129722-using-the-stock-quote-widget-and-cashtags-for-news-wt), modified 2025-07-24, displays headline age/color, posted timestamp and a link to the article/source, and explicitly cautions that news alone does not mean a stock should be traded. These establish a human headline-review workflow and UI metadata, not a deterministic sentiment, catalyst-quality, provider-equivalence, revision, ticker-mapping or allowable-age algorithm. The scanner support page further documents that news markers may trail alert scans by up to five minutes and list scans by their 30-second refresh; those UI delays are not provider delivery guarantees.

Accordingly the W2-05 price/change/RVOL observations remain measurements only; the W2-06 age bands and news-to-market event linkage remain descriptive only. Neither child can move to strategy eligibility from these sources. A public-only Ollama Cloud `gemma4:31b` review concurred that a complete $500/1× IEX scanner is unsupported; its suggestion that Warrior ranking weights were unspecified was rejected because the official scanner page specifies the top-100 gap ranking. No model output supplied a trading rule.

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

# Week 1 acceptance evidence

## 2026-10-06 research closeout and Week 2 handoff

The [gate review](week1-closeout-2026-10-06.md) closes W1 under the [scheduler's blocked-with-evidence rule](codex-app-task-plan.md). W1-04 is accepted **as a non-executable Ross inventory**: its 11 domains are `BLOCKED_RESEARCH` and the version is still `CANDIDATE`. W1-04c remains blocked for historical point-in-time replay and an untouched holdout. W1-06a/Webull is deferred by the user and is outside the Alpaca-only path. G03/G04, executable strategy, order and formal paper-evaluation gates remain open.

W1-04d records a distinct $500/1× [hybrid candidate version](../evidence/strategy-versions/HYBRID_500_IEX_CANDIDATE_V1.yaml) and [11-domain inventory](../evidence/hybrid-fidelity-matrix.yaml), all `BLOCKED_RESEARCH`. The five separately attributed first-party author entries are in the [source registry](../evidence/source-registry.yaml). G01H passes **provenance only**: the evidence loader rejects other-trader sources labeled `ROSS_DIRECT` or Ross broker-equivalent, mixed Ross/other-trader direct claims, and cross-version references; a valid `HYBRID_DIRECT` record must use only appropriately scoped other-trader first-party evidence. No hybrid entry, sizing, stop, exit or daily-loss parameter was accepted.

W1-06's [field-by-field matrix](w1-06-data-capability-matrix.md) passes as a **capability inventory** with independent dispositions. For the current key, live full-market SIP is `FAIL` and Alpaca Basic/IEX is `NOT_SIP_EQUIVALENT`; news semantics, point-in-time float, historical universe and IEX-only automatic discovery are `UNKNOWN` for strategy eligibility. These are explicit G04 blockers, not missing inventory entries. Independent Astra Ultra/source review found no basis to promote the Ross or hybrid manifests to an executable version, or to use the exposed historical cases as a holdout.

W2-03 may start read-only feed/health engineering because W1-05 and W2-01 are accepted. W2-01/02/04 already have their own [acceptance evidence](week2-evidence.md). Strategy-driven W2-05 scanning and W2-06 catalyst eligibility remain blocked pending selected-version rule and data acceptance. No orders, data purchase, live trading or formal paper evaluation occurred during this closeout.

Validation from `/mnt/t/alpaca-bot` after adding the hybrid schema and tests: `rtk proxy .venv/bin/python -m pytest tests/test_evidence.py -q` → 9 passed; `rtk proxy .venv/bin/python -m pytest -q` → 49 passed; `rtk proxy .venv/bin/ruff check .` → pass; `rtk proxy .venv/bin/ruff format --check src tests scripts` → 18 files formatted; `rtk proxy .venv/bin/python -m mypy --strict src scripts tests` → pass for 18 files. The full format check first found two pre-existing W2 event/replay line wraps; those two lines were mechanically formatted, then the full checks were rerun and passed. No runtime behavior was changed by that formatting repair.

## W1-01 — Python scaffold

- Verified 2026-09-28 in `/mnt/t/alpaca-bot` with uv 0.12.17 and managed Python 3.12.14.
- Exact development dependencies: Ruff 0.16.9, mypy 2.3.1, pytest 9.1.1; `uv.lock` records transitive packages and the build backend.
- `UV_CACHE_DIR=/mnt/t/alpaca-bot/var/uv-cache uv sync --locked --all-groups --offline`: pass.
- `UV_CACHE_DIR=/mnt/t/alpaca-bot/var/uv-cache uv run --locked --offline ruff check src tests`: pass.
- `UV_CACHE_DIR=/mnt/t/alpaca-bot/var/uv-cache uv run --locked --offline mypy --strict src tests`: pass, two source files.
- `UV_CACHE_DIR=/mnt/t/alpaca-bot/var/uv-cache uv run --locked --offline pytest`: pass, one install smoke test.
- Project-local cache and virtual environment stay ignored by Git.

Resolved environment faults: the global uv cache was read-only in the sandbox, so the project uses an ignored local cache. Sandbox DNS blocked the initial PyPI resolution; the same scoped dependency operation succeeded through tool-level escalation. No global permissions or package state were changed.

## W1-02 — Paper-only configuration

- Source checked 2026-09-28: [Alpaca authentication](https://docs.alpaca.markets/us/docs/authentication) documents `paper-api.alpaca.markets` for paper Trading API, `api.alpaca.markets` for live Trading API, and the shared `data.alpaca.markets` market-data host; paper and live credentials differ.
- `PaperConfig` rejects missing and unknown fields, non-paper mode, any trading URL other than the exact paper host, any market-data URL other than the exact Alpaca data host, malformed or duplicate environment-variable references, and missing credentials.
- Secret values are resolved from a supplied environment mapping only at the transport boundary. Credential object representations and explicitly redacted HTTP header logs do not expose test secrets.
- `ruff check src tests`: pass; `mypy --strict src tests`: pass, four files; `pytest`: pass, 13 tests including live-host and credential-redaction cases.
- This is a configuration gate only. No order path is enabled. The later W1-05 read-only probe still requires real account evidence before account capabilities can be accepted.

## W1-03 — Evidence and fidelity schemas

- Added a candidate strategy-version record, empty source registry and empty fidelity matrix. Their emptiness makes no strategy claim and authorizes no trading.
- The loader requires explicit source kind, publication/access dates, applicable version IDs, version status, account stage and `rule_id` classification. It rejects unknown fields and unsupported values. Rule sources must be scoped to the same strategy version; Ross direct and broker-equivalent classifications require the corresponding evidence kinds.
- `PyYAML==6.0.3` and `types-PyYAML==6.0.12.20260906` were locked and installed locally. PyPI release metadata was checked on 2026-09-28: https://pypi.org/project/PyYAML/ and https://pypi.org/project/types-PyYAML/.
- `ruff check src tests`: pass; `mypy --strict src tests`: pass, six files; `pytest tests/test_evidence.py`: pass, six schema tests including cross-version, duplicate-field and insufficient-source rejection. The preceding full suite passed 18 tests before the duplicate-field case was added.
- W1-04 must populate and review the first-party Ross record before changing the version status to `FROZEN`.

## W1-04 — Candidate inventory; executable freeze blocked

Independent Astra Ultra research and a direct check of the first-party [2025 toolkit](https://cdn.warriortrading.com/warriortrading.com/assets/2025-Small-Account-Tool-Kit-1013.pdf) on 2026-09-28 found no single complete $500/1x cash-paper profile. Its p. 3 first-week $50/$100 risk-target and -$100 daily limit conflict at $500 with p. 4's approximately 5%/10% risk-target and 10% daily limit. The [2025 challenge packet](https://cdn.warriortrading.com/warriortrading.com/assets/2025-Small-Account-Challenge-Packet.pdf) repeats both presentations. The [official challenge chronology](https://support.warriortrading.com/support/solutions/articles/19000168902-small-account-challenges-at-warrior-trading) describes a $2,000 account, initial leverage and later reset, without binding either table to $500/1x. The historical [2019 review](https://www.warriortrading.com/2019-year-in-review/) does not supply that mapping either.

Screening, RVOL, news, entry, adds, risk, stops, exits, daily limit, stop-trading and account-stage applicability are recorded individually as `BLOCKED_RESEARCH` in `evidence/fidelity-matrix.yaml`. The [watchlist explanation](https://www.warriortrading.com/day-trading-watch-list-top-stocks-to-watch/) describes RVOL windows/display semantics that differ from the toolkit's typical 30-day denominator; no Webull 10-day equivalence is inferred. The separate [micro-pullback lesson](https://www.warriortrading.com/pull-back-trading-strategy/) cannot fill worksheet exit details without version linkage.

The candidate version remains `CANDIDATE`; no risk number, pattern trigger or automated exit is frozen. The research inventory was parsed and validated with the full suite: Ruff pass, strict mypy pass, 19 pytest tests pass. Next evidence needed: first-party version/stage linkage for the risk table and precise definitions for discretionary transitions, potentially from the cited Strategies & Scaling course chapter. W1-05 capability probing is safe independent work; W1-06 and executable strategy paths remain blocked until their dependencies resolve.

On 2026-09-29, Astra Ultra found an earlier first-party [Strategy Decision — Episode 2, p. 2](https://media.warriortrading.com/2019/11/SAC-Strategy-Decision.pdf#page=2) with the same fixed $50/$100/−$100/three-loss presentation; broker and share-size choices remained undecided there. [My Small Account Strategy, pp. 2–3](https://cdn.warriortrading.com/warriortrading.com/assets/SAC2024-Strategy-PDF.pdf#page=2) repeats it, but its US cash example is $1,000 while $500 describes an international-broker minimum with possible leverage. A separate [Ross $500 article](https://www.warriortrading.com/how-to-day-trade-with-500/) discusses cash accounts and simulator practice without binding a numerical risk profile to that stage. The source registry and risk/stage rule references now record this provenance; `BLOCKED_RESEARCH` remains. Exact publication dates for those PDFs were not verified, so their date fields remain null.

On 2026-09-30, a direct check of Ross's [2017 Day 5 small-account recap](https://www.warriortrading.com/day-5-small-account-challenge-130-ep-5/) confirmed the $583.15 starting account used approximately 6× buying power at that stage and described $2,000 and $5,000 as historical account milestones. Those historical milestones are not automatic strategy-switch thresholds for an Alpaca $500 cash account. The source and account-stage rule were updated; the missing $500/1× rule mapping remains blocked. The [2025 toolkit worksheet, p. 4](https://cdn.warriortrading.com/warriortrading.com/assets/2025-Small-Account-Tool-Kit-1013.pdf#page=4) itself links its micro-pullback setup to Chapter 8 of the members-only Strategies & Scaling course and says leverage is unavailable below $5; it does not document a complete $500/1× execution profile.

Access and broker clarification on 2026-09-30: [Warrior Trading support](https://support.warriortrading.com/support/solutions/articles/19000160677-macd-resources) labels the Chapter 8 pullback lesson exclusive to signed-in Pro members and unavailable during a trial. The public link did not yield lesson content through the available browser, and this task has no authenticated Warrior Pro access; the lesson remains unverified. [Alpaca's margin policy](https://docs.alpaca.markets/us/docs/margin-and-short-selling) restricts equity accounts below $2,000 to 1× buying power. Its [account reference](https://docs.alpaca.markets/us/docs/account-plans) lists multipliers 1, 2 and 4, not 6. Ross's historical 6× buying power is margin/leverage through a different broker, not an Alpaca capability for the $500 target. A paper account configured above $500 would not test the stated $500 starting stage. No risk profile or leverage transition was changed.

User-directed research clarification on 2026-09-30: study a low-cost, $500/1× strategy using Ross's public pullback material plus clearly attributed ideas from other traders. The [source and validation track](no-leverage-pullback-research.md) records the public Ross evidence, separate supplemental candidates and offline-to-paper acceptance sequence. The [current Warrior syllabus](https://support.warriortrading.com/support/solutions/articles/19000071156) locates first/second pullbacks and micro-pullbacks in Chapter 7, while the older toolkit/support link calls the relevant pullback lesson Chapter 8; setup title and course version, not chapter number alone, must identify the source. Other-trader ideas are hybrid hypotheses and do not complete the Ross-only W1-04 acceptance or authorize an order.

Further public-source audit on 2026-09-30 found Warrior Trading's [cash-account pullback guide](https://www.warriortrading.com/can-you-trade-in-a-cash-account/): it supplies a $2–20 price band, at least 5× RVOL, a float preference under 20 million shares (under 5 million in a cold market), a news preference, and a first orderly pullback / first candle new-high description. Its surrounding challenge example is $2,000, so it does not establish the $500/1× risk, stop, exit or exact machine trigger. [Alpaca's limited-margin support answer](https://alpaca.markets/support/alpaca-cash-accounts) and [margin rules](https://docs.alpaca.markets/us/docs/margin-and-short-selling) show why Ross's cash-account settlement constraint is not a direct Alpaca account rule while borrowing leverage remains unavailable below $2,000. SMB's [Gap Give and Go sheet](https://smbpowerpoints.s3.amazonaws.com/Gap_Give_and_Go_Cheat_Sheet.pdf) was recorded only as a separately labeled comparison setup. W1-04 stays `BLOCKED_RESEARCH`; no paper account or market data was accessed. The updated source registry and matrix loaded in `tests/test_evidence.py` (6 passed); Ruff and strict mypy passed.

Capital-stage clarification on 2026-09-30: the user confirms no borrowed leverage before $2,000 and permits a sourced Ross leveraged version from $2,000 onward. [Warrior's 2025 challenge chronology](https://support.warriortrading.com/support/solutions/articles/19000168902-small-account-challenges-at-warrior-trading) describes a distinct $2,000 CMEG/DAS 6x phase followed by a $2,000 no-leverage reset. The [Webull cash-account article](https://www.warriortrading.com/can-you-trade-in-a-cash-account/) is a different $2,000 context. [Alpaca's current margin rules](https://docs.alpaca.markets/us/docs/margin-and-short-selling) and [account fields](https://docs.alpaca.markets/us/docs/account-plans) show a $2,000 eligibility threshold but only 1x/2x/4x classifications; actual paper account permissions remain untested. The stage plan, W3-03 acceptance and account-stage fidelity note now distinguish eligibility, strategy version and automatic activation. W1-04 remains blocked.

## W1-05 — Read-only probe prepared; account access blocked

- `scripts/probe_alpaca_capabilities.py` has an explicit `--connect` switch. The default invocation makes no network request. The REST plan is limited to paper account, asset, clock, calendar, IEX/SIP latest trade and quote, and news GET endpoints; the report contains status and schema presence only, without credentials, account balances or response bodies.
- Local fake-response tests verify that a forbidden SIP result stays forbidden, account balances and credentials are omitted, and missing credentials prevent all requests. The real default invocation on 2026-09-29 reported `BLOCKED_CREDENTIALS` for all nine checks. It did not test real account entitlements or paper identity.
- `ruff check .`: pass; `mypy src scripts tests` under the project's strict configuration: pass, nine files; `pytest -q`: pass, 21 tests. The probe's local deterministic/redaction behavior is verified. Real Alpaca response fields, WebSocket access, and subscription limits remain unverified, so W1-05 is not accepted and dependent data paths remain blocked.
- To complete W1-05, supply Alpaca **paper** credentials through the two configured environment-variable names in a local secret channel, run the explicit read-only REST/WebSocket probe, and verify account-level subscription limits separately. Do not place credentials in chat, Git or logs. A successful one-symbol request still cannot establish SIP-equivalent whole-market coverage.
- [Alpaca's current market-data plan documentation](https://docs.alpaca.markets/us/docs/about-market-data-api) describes Basic real-time equity coverage as IEX with a 30-symbol WebSocket subscription limit, versus all-US-exchange coverage in Algo Trader Plus. The [stream protocol](https://docs.alpaca.markets/us/docs/streaming-market-data) documents per-feed authentication, a possible one-connection limit and subscription errors. These are published plan descriptions, not verification of this paper account's actual entitlement or of scanner/news fidelity.
- On 2026-09-29, the probe gained three separately reported read-only WebSocket checks for IEX, SIP and symbol-scoped news. The requests confirm authentication and a one-symbol subscription acknowledgment, then discard frames. [Alpaca stock-stream](https://docs.alpaca.markets/us/docs/real-time-stock-pricing-data) and [news-stream](https://docs.alpaca.markets/us/docs/streaming-real-time-news) references specify those endpoints. `websockets==17.1` was pinned. The no-credential run reports `BLOCKED_CREDENTIALS` for all twelve REST/stream checks, without network traffic. The published 30-symbol limit is still not empirically verified; the probe does not deliberately exceed it.
- A repeated sandbox DNS fault blocked the first `uv add` attempt. The exact scoped dependency operation succeeded with tool-level escalation; the lockfile and local environment were updated, and subsequent offline lint, type checks and tests were run. No global DNS or permission changes were made.
- Final local verification on 2026-09-29: `uv sync --locked --all-groups --offline`, `ruff format --check .`, `ruff check .`, `mypy --strict src scripts tests` and `pytest -q` all passed; 24 tests passed. Explicit `--connect` with no paper credentials again returned only `BLOCKED_CREDENTIALS` statuses and made no network request. These checks validate the probe implementation, not any real Alpaca entitlement.
- Rechecked 2026-09-30: explicit `--connect` still returned `BLOCKED_CREDENTIALS` for all twelve REST/stream checks before transport. After the account-stage source update, `ruff format --check .`, `ruff check .`, strict mypy, and all 24 tests passed. W1-04 and W1-05 remain blocked; W1-06's stated dependencies are not complete. The user was asked whether official Chapter 8 material is accessible and whether paper credentials are available to this scheduled WSL task; neither answer is assumed.
- User clarification on 2026-09-30: Alpaca paper credentials have **not yet been configured**. Keep all account-specific capability results blocked and do not retry network probes until they are securely provisioned. The configuration references `ALPACA_PAPER_KEY_ID` and `ALPACA_PAPER_SECRET_KEY`; no secret value belongs in the repository or chat.

## W1-04a — Public pullback research R0 accepted, 2026-09-30

Deliverable: [Ross public pullback research R0](ross-public-pullback-r0.md), linked from the existing [research track](no-leverage-pullback-research.md). It contains 16 source-scoped propositions, five source-reported teaching cases, checked $500 arithmetic and six ordered closure questions. Added 13 first-party records to the registry, now 28 total; updated the entry/add/risk/stop/exit/stop-trading evidence references and narrowed blockers where the public text supplies partial answers.

Independent `gpt-6-astra` / `ultra` research and bounded document review were performed by `public_pullback_entries`; the coordinator checked the cited primary pages and PDF text. Review found no material case arithmetic error or executable overclaim. Four recommended qualifications were incorporated: first-week fixed risk; the five-minute entry context for a five-minute exit; “generally” for the older micro trigger; and fresh-breakout-relative counting. The add comparison was also renamed to avoid asserting a proven contradiction between incompletely scoped sources.

The substantive partial closures are in R0 RPB-02/03/10/11/12. They are source-scoped research findings, not executable `ROSS_DIRECT` rules. Distinct management versions and discretionary exceptions remain separate. The five cases include positive, failed and out-of-scope boundary examples; all have unresolved exact trading dates and lack contemporaneous replay inputs. No blind validation, profit evaluation, course reconstruction, broker entitlement or chart-image verification is claimed.

Validation from `/mnt/t/alpaca-bot`:

```bash
rtk proxy env UV_CACHE_DIR=/mnt/t/alpaca-bot/var/uv-cache uv run --locked --offline pytest tests/test_evidence.py -q
rtk proxy env UV_CACHE_DIR=/mnt/t/alpaca-bot/var/uv-cache uv run --locked --offline ruff check .
rtk proxy env UV_CACHE_DIR=/mnt/t/alpaca-bot/var/uv-cache uv run --locked --offline mypy --strict src scripts tests
```

Results: **6 evidence tests passed**, Ruff passed, strict mypy passed for nine source files. These are the checks run for this documentation/evidence change, not a new full-suite result. A one-off document check also confirmed 16 proposition rows, five case rows, all concrete source-map IDs registered, and all local links present. Its first broad regex incorrectly treated the `ROSS_DIRECT` classification as a source ID; diagnosis isolated that checker error, and the corrected source-map-only check passed. No repository behavior was changed to bypass a failure.

Reproducible arithmetic used in R0 (Decimal, no broker connection):

```bash
rtk proxy env UV_CACHE_DIR=/mnt/t/alpaca-bot/var/uv-cache uv run --locked --offline python - <<'PY'
from decimal import Decimal, ROUND_FLOOR
for entry, stop, expected_q, expected_loss in [
    ('4.50', '4.33', 111, '18.87'),
    ('10.75', '10.21', 46, '24.84'),
]:
    budget, p, s = map(Decimal, ['500', entry, stop])
    q = int((budget / p).to_integral_value(rounding=ROUND_FLOOR))
    assert q == expected_q and q * p <= budget < (q + 1) * p
    assert q * (p - s) == Decimal(expected_loss)
    print(q, q * p, budget - q * p, q * (p - s))
for equity in map(Decimal, ['500', '1000', '2000']):
    print(equity, equity * Decimal('0.05'), equity * Decimal('0.10'))
PY
```

All assertions passed. The results are affordability ceilings and nominal stop arithmetic before costs/holds; they neither choose risk budgets nor guarantee stop fills.

Scoped source-view limitation, isolated from text-based research:

```yaml
status: ENV_FAULT
category: PRIMARY_SOURCE_VISUALIZATION
evidence: Direct AHPI PDF screenshot initially reported an unresolved application/pdf content type. Reopening the PDF returned readable three-page PDF text; a bounded screenshot call against that resolved reference returned a citation but no model-visible chart image.
affected_scope: Visual inspection of the embedded AHPI chart only; no chart-derived labels were accepted.
safe_to_continue: Yes for attributed PDF text, primary HTML research, deterministic arithmetic and schema validation; no for visual chart verification or historical replay claims.
recommended_action: Keep the case documentary-only until a readable chart or permitted point-in-time market data is available. Do not repeat screenshot calls or alter global environment settings merely to claim visual verification.
```

Acceptance: **W1-04a complete**. The version remains `CANDIDATE` and all eleven fidelity rules remain `BLOCKED_RESEARCH`; G01/G03/G04 and parent W1-04 are not accepted. W1-04b is the next pending research child and does not depend on membership or paper credentials. W1-05 still requires securely configured paper credentials. No orders, subscriptions, services, live trading or formal paper evaluation were started.

## W1-04b — Similar-case research expansion, partial, 2026-09-30

The user asked whether additional research and similar cases are needed. The [case expansion and sampling protocol](pullback-similar-case-research.md) adds C06–C09: a reported waited-for micro-pullback loss, a no-trade session, skipped pullbacks plus a depth-dependent exit, and a daily-loss restriction despite a subsequent opportunity. Five primary sources were added to the registry, for 33 total. The original R0 and TODO link to this continuation; W1-04b stays unchecked because account-stage risk and reproducible definitions remain unresolved.

The existing independent researcher, explicitly configured as `gpt-6-astra` with `ultra` reasoning, verified the primary cases and reviewed the document. No material source error or invented strategy parameter was found. Two refinements were incorporated after coordinator source checks: PRAN's source reports approximately $110,000 at Day 60, and NAKD is explicitly out of scope for establishing a micro-pullback signal while relevant to account controls. All case dates remain unverified; none establishes a matched $500/no-leverage execution example. The official challenge chronology identifies the post-reset $2,000/no-leverage phase as a more relevant next source search, without supplying $500 sizing.

The protocol distinguishes author-reported behavior, independently verified eligibility, account/data feasibility and realized outcomes. It preserves no-trade examples, source/version differences and unknown inputs; deduplicates related content; groups shared sessions; and keeps these already viewed narratives out of any later unseen evaluation set. Coverage is assessed against specific unresolved rules, not a case count or a selected sample's win rate. This is a research procedure, not a Ross trading parameter.

Exact validation commands run for the registry/documentation update:

```bash
rtk proxy env UV_CACHE_DIR=/mnt/t/alpaca-bot/var/uv-cache uv run --locked --offline pytest tests/test_evidence.py -q
rtk proxy env UV_CACHE_DIR=/mnt/t/alpaca-bot/var/uv-cache uv run --locked --offline ruff check .
rtk proxy env UV_CACHE_DIR=/mnt/t/alpaca-bot/var/uv-cache uv run --locked --offline mypy --strict src scripts tests
```

Results: six evidence tests passed, Ruff passed, strict mypy passed for nine files. A focused document check confirmed the four case identifiers, registered source references, local links, unchanged eleven blocked fidelity records, and unchecked W1-04b. No executable code, risk configuration, credentials, account state or automation schedule changed. No paid inputs or trades were used.

## W1-04b1 — Four-category deep research accepted, 2026-09-30

User request: fill the four named research categories using deep research. The [integrated report](ross-four-case-deep-research.md) adds C10–C14 and connects them to C06–C09, with a coverage table, source locators, distinct fact/action/outcome labels, issuer-level news corroboration, hypothetical $500 arithmetic, a published Alpaca capability comparison, and an explicit inclusion/access ledger. All categories have evidence; full rule compliance, executable risk and matched replay are not claimed. The earlier case protocol links forward to this report.

Independent research and cross-checks used `deep_news_cases`, explicitly spawned as `gpt-6-astra` with `ultra` reasoning. The agent independently checked the new Ross cases, issuer evidence, broker documentation and the bounded no-leverage capital-phase search. The coordinator checked primary passages, reviewed the integrated report and retained the important qualifications: retrospective versus decision-time labels, HOD versus micro setups, prepared versus filled orders, maximum-position versus cash restrictions, source-specific no-news behavior, and nullable timestamps. No additional trader's rule or numerical strategy parameter was adopted.

Eleven sources were added, bringing the registry to **44**: five Ross recaps, three issuer records and three broker documents. Source-kind `OTHER` identifies issuer records without treating them as Ross rules. The MYND/OBLN session date is explicitly a corroborated inference, while the recap's publication date remains null. SEC evidence was readable through primary search rendering despite a direct-open failure. The fidelity matrix narrows the news, risk, stop, exit and regime blockers but retains **11/11 `BLOCKED_RESEARCH`** and `CANDIDATE` status. Published order constraints do not verify this account or close W1-05.

Validation commands, run from `/mnt/t/alpaca-bot`:

```bash
rtk proxy env UV_CACHE_DIR=/mnt/t/alpaca-bot/var/uv-cache uv run --locked --offline pytest tests/test_evidence.py -q
rtk proxy env UV_CACHE_DIR=/mnt/t/alpaca-bot/var/uv-cache uv run --locked --offline ruff check .
rtk proxy env UV_CACHE_DIR=/mnt/t/alpaca-bot/var/uv-cache uv run --locked --offline mypy --strict src scripts tests
```

Results: **6 passed**; **All checks passed**; **no issues in 9 source files**. An additional offline `uv run --locked --offline python -` check loaded the evidence bundle and verified 44 unique sources, 11 blocked rules, unchanged candidate status, C10–C14, all 11 source identifiers in the report, local Markdown links, ten Decimal arithmetic assertions from the stress table, and pending parent/holdout gates. No production code changed, so tests were limited to relevant evidence validation plus normal lint/type checks.

Tool/environment limitations were isolated without modifying global state:

```text
status: ENV_FAULT
category: orchestration-capacity
evidence: Additional agent spawn and an existing-agent follow-up returned "agent thread limit reached". The already running explicitly configured Astra Ultra researcher remained usable and completed the independent source work.
affected_scope: Additional parallel research assignments only.
safe_to_continue: Yes, through the active researcher and coordinator primary-source checks; no dependence on the failed assignments.
recommended_action: Stop further allocation attempts in this run; reuse completed research. Revisit capacity only if a future task requires another independent assignment. No global reset or repository workaround.
```

```text
status: ENV_FAULT
category: restricted-shell-network-dns
evidence: The researcher reported bounded urllib metadata requests failing with URLError and Errno -3, Temporary failure in name resolution. Primary web-tool reads and local offline checks remained functional.
affected_scope: Shell retrieval of optional source metadata; not repository code or strategy validation.
safe_to_continue: Yes for readable primary web results and deterministic offline checks. Publication/session times without evidence remain null; failed source access cannot support claims.
recommended_action: Stop affected shell network attempts; retain access gaps. No global DNS/proxy changes are justified while the authorized web path supplies the required research. Original shell metadata operation remains unverified, not repaired.
```

YouTube shell/error responses are separate source-access gaps: no transcript or video-viewing claim was made. These access/capacity limitations did not prevent completion of the bounded four-category document; they did not close missing source propositions either.

Acceptance: **W1-04b1 complete as a research-document milestone**. W1-04b/W1-04c remain pending and W1-04 plus applicable strategy gates remain blocked. The next research is account-stage risk provenance and reproducible source-scoped labels/management, followed by point-in-time input validation. No orders, credentials, subscription purchases, services, automation changes, live mode or formal paper evaluation were used.

## W1-04b2 — New-routing rerun and environment recovery, 2026-09-30

The user requested a fresh rerun under updated AGENTS.md and then asked to resolve the environment blockers. The [comparison report](ross-research-routing-comparison.md) records the successful alternate execution paths, model provenance, comparison, coordinator corrections and remaining ancillary faults. The [initial blocked record](research-routing-rerun-status.md) now points to the recovery while retaining its historical evidence.

Read-only Windows Ollama access succeeded after tool-approved escalation resolved sandboxed WSL interop for the operation. Existing model metadata maps `gemma4:31b-cloud` to `https://ollama.com` / `gemma4:31b`; both a minimal probe and actual analysis returned the exact remote model with `done=true`. No new credentials, installation, download, subscription or service were required. The original CLI export proposal was rejected by automatic review. A safer public-URLs-only request, with project documents excluded and read-only execution, was approved and completed. Two ephemeral CLI workers explicitly selected `gpt-6-astra` with `low` reasoning, so no Ultra substitution occurred. Built-in thread allocation remains limited; the CLI route was verified rather than claiming that quota was repaired.

The twelve-record public fact package went to Gemma Cloud; local instructions, the old report, credentials and account state did not. Its structured response covered all four categories and ended normally (`done_reason=stop`, 799 output tokens reported). Coordinator review corrected a guide/recap misclassification, overconfident ticker attribution, and an unsafe cross-session news/date comparison. These corrections are explicit in the report; raw model output is retained separately and is not a trading specification.

The earlier report, AGENTS.md, fidelity matrix and candidate version hashes were unchanged. Evidence registry remains at 44 sources, with 11 blocked fidelity domains and no frozen strategy. The substantive prior conclusions stand with an additional UAVS/CLWT ticker-attribution qualification. No generic model-equivalence, profitability or efficiency claim follows from this single rerun.

Validation:

```bash
rtk proxy env UV_CACHE_DIR=/mnt/t/alpaca-bot/var/uv-cache uv run --locked --offline pytest tests/test_evidence.py -q
rtk proxy env UV_CACHE_DIR=/mnt/t/alpaca-bot/var/uv-cache uv run --locked --offline ruff check .
rtk proxy env UV_CACHE_DIR=/mnt/t/alpaca-bot/var/uv-cache uv run --locked --offline mypy --strict src scripts tests
rtk git check-ignore var/research/routing-rerun-20260930/manifest.json
```

Results: 6 tests passed; Ruff passed; strict mypy passed for 9 source files; research runtime artifacts are ignored. A deterministic document/provenance check verified four baseline hashes, six artifact hashes, exact Cloud model/completion, twelve public facts, Markdown links/fences and unchanged parent gates. Both independent retrieval workers exited 0. Auxiliary CLI state-lookup and notification warnings are preserved in the report as scoped ENV_FAULT packets; they did not prevent retrieval or artifact delivery and were not repaired through global resets.

Acceptance: **W1-04b2 complete as a research comparison milestone**. Parent W1-04b/W1-04c remain pending; strategy gates remain blocked. No production code, strategy parameter, broker state or automation schedule was changed, and no formal paper evaluation was started.

## W1-04b — Historical Chapter 8 bounded inference, partial, 2026-10-01

The user's clarified goal is to infer likely historical first/second-pullback lesson content from public evidence, without requiring member-course access or exact recovery. The [Q1–Q4 continuation](ross-chapter8-inference-q1-q4.md) records a newly reviewed Ross Day 74 recap, source-scoped count/trigger/management labels, contrary interpretations and still-missing $500/1× risk and exit semantics. `ROSS_DAY74_PULLBACK_COUNT_RECAP` was added to the registry with an unknown publication date. The original article uses a premarket first and after-open second SNGX pullback, so a mandatory opening-bell count reset is contradicted; it does not define a replacement algorithm or a $500 risk limit. The coordinator checked the original Warrior recap, $500/cash articles, 2025 toolkit, bull-flag/micro articles, MACD guide and member-course catalogue link through the web tool on 2026-10-01.

The built-in Astra Ultra allocation returned `agent thread limit reached`. A separately approved, ephemeral Codex CLI worker then ran from `/tmp` with `gpt-6-astra`, `ultra` reasoning, `project_doc_max_bytes=0`, a read-only model sandbox and public URLs only. Its model/effort header and final public-source result were checked; no local project documents or credentials were sent in its prompt. The first CLI attempt could not initialize because the outer shell sandbox made `/home/dx/.codex/sqlite/state_5.sqlite` read-only. Tool-level escalation of the **same** isolated read-only model operation allowed local state initialization and the retrieval completed. No sandbox control was disabled and no global state was reset. Auxiliary state-lookup and legacy-notification warnings remained nonfatal; they do not establish a repair of built-in allocation.

The allowlisted Ollama registration listed `gemma4:31b-cloud`; a public-only technical-analysis call returned `route=cloud`, `model=gemma4:31b`, `success=true` on 2026-10-01. The Cloud draft wrongly conflated pattern-order labels with entry triggers and speculated about why the toolkit's risk presentations differ; neither was adopted. The coordinator retained only source-supported observations after checking the original passages. No private account data or local documents were provided to Cloud.

```text
status: ENV_FAULT
category: built-in-agent-allocation
evidence: Built-in Astra Ultra spawn returned agent thread limit reached on 2026-10-01; the approved isolated CLI retrieval completed, but built-in capacity was not reset.
affected_scope: Built-in subagent allocation; not the completed public-source retrieval or local validation.
safe_to_continue: Yes, through the verified CLI route for public-only research under tool-level approval; no private-file export.
recommended_action: Do not retry unchanged built-in allocation. Retain the CLI workaround until capacity is externally restored; verify model, sandbox and source output per run.
```

```text
status: ENV_FAULT (resolved for the affected operation)
category: outer-sandbox-cli-state-write
evidence: The first read-only CLI launch failed to initialize because its local Codex SQLite state path was read-only; tool-level escalation of the same CLI operation succeeded with the model's sandbox still read-only.
affected_scope: Ephemeral CLI initialization only.
safe_to_continue: Yes, after the original public-source retrieval completed and its model/effort header and output were checked.
recommended_action: Use a scoped tool-level approval if the same initialization fault recurs; do not change global permissions or disable the model sandbox.
```

Validation in `/mnt/t/alpaca-bot`: `.venv/bin/python -m pytest -q` **24 passed**; `.venv/bin/ruff check .` **passed**; `.venv/bin/ruff format --check .` **27 files formatted**; `.venv/bin/python -m mypy --strict src scripts tests` **passed, 9 source files**. All commands were invoked through `rtk proxy`. No TODO checkbox or fidelity classification was promoted. W1-04b, parent W1-04, W1-04c and G01/G03/G04 remain pending or blocked as before; no account connection, order, subscription, live mode or formal paper evaluation occurred.

## W1-04b — Counterexample verification continuation, 2026-10-02

An independent `gpt-6-astra`/`ultra` public-source review and coordinator web check added the [AYTU third-at-open exception](https://www.warriortrading.com/green-while-wearing-red-1200-ross-trade-recap/) and [PRAN timeframe/count counterexample](https://www.warriortrading.com/day-60-583-challenge-411-79/) to the [Q1–Q4 research](ross-chapter8-inference-q1-q4.md). AYTU and SNGX defeat a mandatory opening-bell count reset; AYTU defeats an absolute third-pullback prohibition. In PRAN, Ross skipped an almost-third one-minute setup and later bought a first five-minute setup. A verified `gemma4:31b` Cloud response misidentified the purchased PRAN setup as the third one-minute pullback; direct source checking rejected that claim. No $500/1× risk number, MACD decision timing, universal reset, or complete exit precedence emerged. W1-04b and dependent gates remain blocked.

status: ENV_FAULT
category: sandbox_network/upstream_public_fetch
evidence: The independent reviewer's optional shell request for publication-date metadata failed with a WSL DNS error; one read-only tool-level escalated request reached Warrior Trading but returned HTTP 403. The coordinator's web retrieval of the article content succeeded. No publication date was inferred from the site footer.
affected_scope: Optional shell metadata retrieval for public Warrior articles; source content review and repository checks were unaffected.
safe_to_continue: Yes, for web-based source verification and local documentary work; publication dates remain unknown.
recommended_action: Preserve null publication dates and use the working web route. Do not reset global networking or disguise the 403 with identity changes. Recheck exact publication metadata only if a reliable first-party route becomes available.

2026-10-02 user-requested fault diagnosis: the same read-only `socket.getaddrinfo("www.warriortrading.com", 443)` returned `Temporary failure in name resolution` inside the default sandbox and an address with tool-level escalation. A read-only `urllib.request` HEAD to the AYTU source outside the sandbox then returned HTTP 403. The [original AYTU article](https://www.warriortrading.com/green-while-wearing-red-1200-ross-trade-recap/) remained readable through the web retrieval tool; its rendered article text did not establish an exact publication date. The sandbox DNS restriction and the site's response are separate causes. No DNS, proxy, identity, or security setting was changed. The original shell metadata request is **not recovered**; publication dates remain null, and source-content research can continue through the verified web path.

Validation: `rtk proxy .venv/bin/python -m pytest -q` passed 24 tests; `rtk proxy .venv/bin/ruff check .` passed; `rtk proxy .venv/bin/ruff format --check .` passed for 27 files; `rtk proxy .venv/bin/python -m mypy --strict src scripts tests` passed for 9 source files; `rtk git diff --check` passed. No TODO checkbox or fidelity gate changed. No orders, subscriptions, live trading or formal paper evaluation occurred.

## Built-in agent allocation recovery, 2026-10-01

The user requested resolution of the remaining built-in `agent thread limit reached` fault. `list_agents` showed the root and three completed research agents in this task tree. An existing completed agent accepted a `followup_task` and returned a new response. A subsequent fresh `spawn_agent` with explicit `model=gpt-6-astra`, `reasoning_effort=low` and no inherited conversation succeeded and returned `READY`. This verifies that the original **built-in allocation operation now works**; no CLI fallback was needed for that diagnostic. The existing agent could not report its model/effort, so it must not be substituted for a task requiring a verified configuration.

[Official OpenAI Multi-agent documentation](https://developers.openai.com/api/docs/guides/responses-multi-agent) describes a limit on *active* subagent turns, not a fixed total of historical subagents. The available diagnostics do not establish why the earlier allocation failed or why capacity became available now. No agent history was deleted, no global networking or quota setting was changed, and no restart was used. A narrow recovery note was added to the global Codex `AGENTS.md`, with an exact-readback check and a backup at `/tmp/codex-global-agents-before-capacity-recovery-20261001.bak`. It directs future workers to inspect agent state, distinguish active from completed agents, verify a fresh allocation when capacity may have changed, and retain the public-only CLI fallback if the limit recurs. This reduces mistaken persistent-fault reports but does not guarantee future capacity.

```text
status: ENV_FAULT (resolved for the original built-in allocation operation)
category: built-in-agent-allocation
evidence: Earlier built-in spawn failed with agent thread limit reached; an existing-agent follow-up and then a fresh explicit Astra Low spawn both succeeded on 2026-10-01.
affected_scope: Built-in subagent allocation in this chat; cause of the earlier capacity condition remains unknown.
safe_to_continue: Yes for new built-in subagent tasks after verifying their actual requested model/effort; no trading or strategy gate is changed.
recommended_action: If the fault recurs, inspect active agents and test a fresh allocation once when state changes; do not delete history or assume a follow-up caused recovery. Use the scoped public-only CLI fallback only when needed.
```

## W1-04b — Bounded pullback reconstruction accepted, 2026-10-03

The [Q1–Q4 research](ross-chapter8-inference-q1-q4.md) now distinguishes public Ross statements, Ross-based historical-lesson hypotheses, other-author analogies and unknowns for the historical lesson, `BF23`, `MP-WEB`, `MP-PDF`, `$2,000 CASH` and `TK25`. Each candidate has a source/version scope, evidence chain and exposed example/contrary observation. An independent `gpt-6-astra`/`ultra` reviewer traced $500/1× risk provenance and audited the consolidation. The coordinator checked Ross's [2026 starting-capital guide](https://www.warriortrading.com/how-much-money-do-you-need-to-day-trade/), [Warrior small-account risk guide](https://www.warriortrading.com/how-to-manage-risk-in-a-small-account/), the first-week strategy PDFs and cited Ross recaps against the conclusions. Reviewer findings corrected an ambiguous toolkit `Risk/Share size per trade` field and an omitted older-micro profit-cushion exception; the reviewer found no remaining P1/P2 documentary issue in that scope.

Q1 did **not** yield a version-matched $500/1× per-trade/daily policy. Q2 advance/count reset and exact eligibility, Q3 MACD observation timing, and Q4 partial/stop/exit precedence remain `BLOCKED_RESEARCH` for operations. The member lesson was not accessed and is not required for this bounded research acceptance. W1-04, G01/G03/G04 and all corresponding order paths remain blocked. W1-04c owns point-in-time inputs, label disagreement and an unseen holdout; no replay or formal paper evaluation was started.

Validation: `rtk proxy .venv/bin/python -m pytest -q` passed 24 tests; `rtk proxy .venv/bin/ruff check .` passed; `rtk proxy .venv/bin/ruff format --check .` passed for 27 files; `rtk proxy .venv/bin/python -m mypy --strict src scripts tests` passed for 9 source files. The source registry retained separate publication/access dates and schema validation passed in the test suite. No code, account configuration, orders, subscription, services, live endpoint or scheduler configuration changed.

status: ENV_FAULT
category: public-source retrieval failure
evidence: The independent reviewer received `Internal Error` opening two candidate official YouTube episodes (`RMxnG64WuLc`, `0Uj8k9PlBhE`); a candidate article slug also failed. No source facts were taken from those failed responses or third-party transcripts.
affected_scope: Verification of those individual episode leads for risk provenance, not the readable Warrior articles/PDFs or local validation.
safe_to_continue: Yes, for the documented first-party article/PDF research; no, for treating the inaccessible episodes as verified evidence.
recommended_action: Keep the episode leads `SOURCE_UNAVAILABLE` and preserve the $500/1× operational risk blocker. Revisit only through a readable first-party route; do not alter global access controls or invent episode content.

## W1-04c — Offline input and holdout inventory, partial, 2026-10-03

The [W1-04c inventory](w1-04c-offline-inputs.md) lists each exposed source case, exact-session status, decision-time fields, abstentions, lawful-access questions and a separate unseen-session protocol. An Astra Ultra source review located the date **2021-07-13** on the original [AHPI lesson PDF p. 3](https://media.warriortrading.com/2021/07/13171942/SAC-Micro-Pullback-Strategy-v2.pdf#page=3) ten-second and one-minute chart cursors at about 13:09 ET. The coordinator visually checked the rendered primary page; its five-minute screenshot has a different July 8 cursor and is not used to date the micro entry. PXMD and other exposed cases retain `session=null` unless independently verified. The AHPI date is a source-image label, not independent market/fill data.

[Alpaca plan documentation](https://docs.alpaca.markets/us/docs/about-market-data-api) and its [FAQ](https://docs.alpaca.markets/us/docs/market-data-faq) identify historical data possibilities but require authentication; historical SIP older than 15 minutes is not live SIP. No point-in-time full-depth tape, source-time news revisions, complete $500 account state or separate unseen session set has been established. The read-only `rtk proxy .venv/bin/python scripts/probe_alpaca_capabilities.py --connect` returned exit 0 with all nine REST and three stream checks `BLOCKED_CREDENTIALS`, `paper_trading_only=true`, and no verified SIP, subscription-limit or WebSocket capability. It sent no order and exposed no secret. W1-04c is blocked; W1-05 remains blocked for the same credential prerequisite; W1-06/W1-07 dependencies and G04 remain unaccepted.

status: ENV_FAULT (scoped source-access fallback recovered; sample verification unresolved)
category: web PDF/sample transport, sandbox DNS and WSL interop restrictions
evidence: The Astra Ultra reviewer could read the PDF text but web screenshot failed. The same read-only public PDF download and local rendering succeeded through tool-level approved operations using existing `pypdfium2`; the coordinator visually verified the resulting page. Databento sample-link inspection failed internally, so sample contents were not inferred. No package, global setting or security control was changed.
affected_scope: Visual PDF inspection and third-party sample-content verification only; not local tests or the redacted Alpaca probe.
safe_to_continue: Yes for primary documents and the recovered AHPI page image; no for declaring an unseen holdout or complete market/depth/news dataset.
recommended_action: Preserve chart-specific source provenance and unresolved sample coverage. Do not repeat opaque web calls or change global access controls. Recheck data rights, fields and account entitlements when paper credentials are safely available.

Final local checks for this continuation: `rtk proxy .venv/bin/python -m pytest -q` passed 24 tests; `rtk proxy .venv/bin/ruff check .` passed; `rtk proxy .venv/bin/ruff format --check .` passed for 28 files; `rtk proxy .venv/bin/python -m mypy --strict src scripts tests` passed for 9 source files. No strategy parameter, order path, subscription, live setting or formal paper window was enabled.

## W1-05 — Partial credential handoff, 2026-10-03

The user supplied a paper API URL and key ID in chat, but no matching secret. The key ID was not copied into Git, logs, tool arguments or configuration. The configured paper **base** is `https://paper-api.alpaca.markets`; the probe appends `/v2/...` paths, consistent with [Alpaca's authentication documentation](https://docs.alpaca.markets/us/docs/authentication). A redacted environment-presence check in the current WSL process returned `paper_key_id_present=False` and `paper_secret_present=False`; it printed no values. The read-only capability probe is still `BLOCKED_CREDENTIALS` and no authenticated request was attempted. Continue only after a paper key pair is supplied to the scheduled WSL environment through a private local mechanism; do not paste the secret into chat.

## W1-05 — Authenticated paper capability probe, 2026-10-03

The user configured a matching paper pair locally. `stat` confirmed the WSL-native credential directory is owner-only mode `700` and the file is regular, owned by UID 1000 and mode `600`. The project `/mnt/t` location is a 9p/DriveFS mount that continued to report mode `777` after `chmod 700`; the setup script was moved to `~/.local/share/alpaca-bot/paper.json` without storing values in Git or tool commands. A dummy-only write/read acceptance check in the new directory produced mode `600` and removed its temporary file. The user kept the existing paper pair; no key value is retained in this record.

The first `--connect` attempt under the default shell sandbox returned `UNAVAILABLE` for all nine REST and three stream checks. Independent unauthenticated DNS checks for both Alpaca hosts failed, and a bounded URL check reported `gaierror`, so this result was not interpreted as invalid credentials. Tool-approved escalation of the **same read-only** probe resolved that scoped network fault. The successful redacted report found `REACHABLE` for paper account, asset sample, clock, calendar, IEX latest trade/quote, news REST, IEX market stream and news stream. SIP latest trade and quote REST each returned `FORBIDDEN`. A bounded follow-up read only those error bodies and printed the provider message, not credentials or market/account payloads: both said `subscription does not permit querying recent SIP data`. This confirms insufficient recent-SIP entitlement for this account, not invalid credentials. The [Alpaca Market Data FAQ](https://docs.alpaca.markets/us/docs/market-data-faq) distinguishes this message from other causes of HTTP 403 and says historical SIP queries can be made without that subscription when `end` is at least 15 minutes old; we have not tested historical SIP access. SIP market stream returned `UNAVAILABLE`, which still lacks a specific reason. `paper_trading_only=true`, `sip_equivalence_verified=false`, `subscription_limits_verified=false`, and `websocket_access_verified=false` (the latter aggregates all three streams). No account values, response bodies, secret values or orders were retained.

A separate minimal historical check requested one AAPL 1-minute bar for 2026-10-02 14:30–14:35 UTC with `feed=iex` and then `feed=sip`. Both returned HTTP 200 and a nonempty result; prices and volume were not printed or retained. This confirms that this account can query that delayed historical sample from both feeds, consistent with the official Basic-plan rule requiring SIP query end times to be at least 15 minutes old. It does not establish broad ticker/session coverage, full-depth history, revision completeness, point-in-time universe, retention rights or the ability to use the dataset as a Ross replay. Alpaca's current [Basic plan table](https://docs.alpaca.markets/us/docs/about-market-data-api) lists zero-cost real-time IEX, a 30-symbol WebSocket subscription limit, historical data since 2016 with the latest 15 minutes restricted, and 200 equity historical requests per minute; the account's stream and rate ceilings have not been stress-tested.

status: ENV_FAULT (resolved for the affected operation)
category: default-sandbox DNS restriction
evidence: Both Alpaca hosts failed `getent ahostsv4` with exit 2; a bounded unauthenticated URL check reported `gaierror`; the default-sandbox authenticated probe returned all `UNAVAILABLE`. The identical tool-approved read-only probe returned differentiated Alpaca responses.
affected_scope: Network reachability of the W1-05 capability probe in the default shell sandbox only.
safe_to_continue: Yes, for the verified scoped read-only path. No global DNS/proxy settings were changed.
recommended_action: Use tool-approved network access for future authorized read-only Alpaca checks; retain redacted reports and do not infer entitlements from sandbox failures.

Validation: `rtk proxy .venv/bin/python -m pytest -q tests/test_local_credentials.py tests/test_capabilities.py` passed 7 tests; Ruff check and format passed; strict mypy passed for 12 source files. W1-05 remains partial because subscription limits and SIP stream semantics are unresolved and the official SDK candidate has not passed adapter parity. W1-04c still lacks historical replay inputs and a holdout; G01–G04 remain unaccepted. No order, data purchase, live mode or formal paper evaluation occurred.

## W1-04c — Narrow historical input access check, blocked, 2026-10-04

Using the locally configured paper credentials, a read-only request for AHPI on the source-image session (2021-07-13, 17:08–17:10 UTC) returned HTTP 200 for historical SIP trades, quotes and one-minute bars. The trade and quote responses each returned the first 1,000 rows and a next-page token; pagination was not followed, so neither is complete. The bar response contained three records. A full-session-day historical news query returned 13 AHPI records with `created_at`, `updated_at` and `source` fields present. The headlines and market values were not retained or evaluated, and no data files were downloaded. These are narrow endpoint responses, not a complete replay fixture or proof of permission to retain/use the data for replay.

This observed historical SIP response conflicts with Alpaca's [Paper Trading](https://docs.alpaca.markets/us/docs/paper-trading) and [Historical Stock Data](https://docs.alpaca.markets/us/docs/historical-stock-data-1) documentation, which describes Paper Only/IEX-only or SIP as subscription-required, while its [FAQ](https://docs.alpaca.markets/us/docs/market-data-faq) describes delayed historical SIP queries. The actual access is verified only for these specific requests; account entitlement guarantees and data-use/replay rights remain unresolved. Latest SIP requests remain forbidden. No full-depth historical tape, premarket point-in-time universe, historical float, complete article first-seen/revision sequence, $500 account snapshot, or independent unseen session set has been established. The sample session and all narrative cases are exposed development examples, not holdout. Therefore W1-04c remains `BLOCKED_RESEARCH`; this did not start replay or formal paper evaluation.

An Astra Ultra public-source investigation identified the official Nasdaq ITCH directory entries for July 13, 2021 (about 6.0 GB) and August 13, 2021 (about 4.9 GB). No file was downloaded or inspected; AHPI presence, suitability as a Ross-label session and replay/retention terms remain unknown. ITCH represents Nasdaq execution-system depth, not all-venue SIP or proof of Ross's full market view. No purchase was made.

## W1-05 — Historical access distinction and SIP stream mapping, 2026-10-04

The read-only capability probe continues to identify latest SIP trades and quotes as `FORBIDDEN`, with the provider message `subscription does not permit querying recent SIP data`. The SIP stream response uses code 409; the probe now maps this to `INSUFFICIENT_SUBSCRIPTION` rather than generic `UNAVAILABLE`. This improves error classification only and does not grant access. A historical SIP request succeeding for the narrow AHPI/AAPL samples above does not settle account entitlements because Alpaca's own official pages conflict. IEX and news access remain as previously recorded. `subscription_limits_verified`, `sip_equivalence_verified` and the aggregate `websocket_access_verified` remain false; official SDK parity is still unverified.

Validation of the probe change: `rtk proxy .venv/bin/python -m pytest -q tests/test_capabilities.py tests/test_local_credentials.py` passed 7 tests; Ruff check and format and strict mypy passed. No order, data purchase, live setting or formal paper evaluation occurred. W1-05 remains partial/blocked; G01–G04 are not newly accepted.

## W1-04c — Historical SIP permission-source audit, `BLOCKED_RESEARCH`, 2026-10-05

An independent `gpt-6-astra`/`ultra` investigation used public primary Alpaca sources only; the coordinator independently opened the current Basic plan, FAQ, Paper Trading page and historical trade/quote API references. The [Basic plan table](https://docs.alpaca.markets/us/docs/about-market-data-api) describes Basic as the default for Paper and Live, free, IEX-only for real-time equities, historical coverage since 2016, and a latest-15-minute restriction. The current [market-data FAQ](https://docs.alpaca.markets/us/docs/market-data-faq) explicitly says historical SIP can be queried when `end` is at least 15 minutes old, while latest SIP endpoints require subscription. The [historical trades](https://docs.alpaca.markets/us/reference/stocktrades-1) and [historical quotes](https://docs.alpaca.markets/us/reference/stockquotes-1) references describe the corresponding delayed end-time default and SIP feed. This is consistent with the narrow AHPI historical SIP requests that returned HTTP 200 on 2026-10-04.

The current [Paper Trading page](https://docs.alpaca.markets/us/docs/paper-trading), however, still says Paper Only account holders are “only entitled to receive and make use of IEX market data”; it does not state a historical carve-out. The [Historical Stock Data overview](https://docs.alpaca.markets/us/docs/historical-stock-data-1) similarly says IEX is the only feed usable without a subscription. No public official correction or account/use-specific clarification was found. A paper API key alone does not prove the customer is in the narrower Paper Only category. Current [Terms and Conditions](https://files.alpaca.markets/disclosures/library/TermsAndConditions.pdf) describe personal/non-commercial access and restrictions on dissemination, but do not state a historical SIP retention period or expressly resolve the Paper Only wording. The linked customer agreement may apply to brokerage customers, but its applicability to a true Paper Only account was not established. No relevant exchange subscriber-agreement text was verified.

An Alpaca [historical-data tutorial](https://alpaca.markets/learn/fetch-historical-data) describes older-than-15-minute history across feeds on the free plan, but its displayed publication/update metadata is unclear and it does not expressly identify the narrower Paper Only account category. A 2021 [Market Data API launch post](https://alpaca.markets/blog/market-data-api-launch/) mentioned free and paid SIP plans for both paper-only and live customers at that time; this is historical product context, not proof of current Basic rights. Neither changes the unresolved current account/use classification.

Conclusion: historical SIP technical access for sufficiently old data is documented at the Basic-plan FAQ/API level and matches the narrow observed HTTP responses. That does **not** settle account-specific entitlement, completeness, or permission for persistent local replay archives. Keep historical SIP use/retention `BLOCKED_RESEARCH`; no paid data, new request, download, or order was made during this source audit. W1-04c remains blocked independently by incomplete pagination, missing point-in-time news/float/universe/account/depth inputs, and an empty unseen holdout.

Validation on 2026-10-05: `rtk proxy .venv/bin/python -m pytest -q` passed 46 tests; `rtk proxy .venv/bin/ruff check .` passed; `rtk proxy .venv/bin/python -m mypy --strict src scripts tests` passed for 18 source files. Repository-wide `ruff format --check .` reported formatting differences in W2-owned `src/alpaca_ross_bot/events.py` and `tests/test_replay.py`; both are outside this W1 scope and were left untouched. The W1 capability/credential files passed a targeted format check and their 7 targeted tests passed. No W1 checkbox or G01–G04 gate was promoted.

## W1-05 — Capability probe status after source audit, 2026-10-05

The prior observed realtime results remain distinct from historical access: latest SIP trades/quotes are `FORBIDDEN` for this account, and SIP streaming code 409 is mapped to `INSUFFICIENT_SUBSCRIPTION`; IEX realtime and the tested news/REST/stream endpoints remain reachable. Historical REST 200 responses outside the recent window do not change those realtime statuses. Because official sources leave Paper Only use and retention unresolved, and actual subscription ceilings plus SDK timeout/redaction/paper-only adapter parity remain unverified, W1-05 remains partial and its affected SIP history use remains `BLOCKED_RESEARCH`. No checkbox or G01–G04 gate was promoted.

## W1-04c/W1-05 — Account category confirmed and probe report corrected, 2026-10-05

The user clarified that the credentials belong to a normal Alpaca brokerage account using paper trading, not a Paper Only account. Therefore the Paper Only page's IEX-only clause does not describe this user. Alpaca's current Basic plan documentation and FAQ apply to Paper and Live plans and explicitly allow historical SIP requests with `end` at least 15 minutes old; the narrow AHPI historical requests are consistent with this documented free access. The $99 subscription remains necessary for real-time/latest SIP, which the actual account's REST and stream probes do not have. The general terms permit personal/noncommercial use and prohibit redistribution/commercial exploitation; no exact permanent-retention period was found. No purchase or order was made.

The current [redacted capability report](../evidence/alpaca-capabilities-2026-10-06.json) was captured through the paper trading endpoint. Its prior schema field `paper_trading_only` was misleading: it represented the configured endpoint, not the user's account category. Code and report schema now use `paper_trading_endpoint_validated`, and a regression test enforces the distinction. The report confirms account/asset/clock/calendar field presence, IEX latest trade/quote, news REST, IEX/news streams, `FORBIDDEN` latest SIP REST, and `INSUFFICIENT_SUBSCRIPTION` SIP stream. Basic's documented limits are 30 equity WebSocket symbols and 200 historical API requests/minute; the probe does not attempt a boundary/stress test, so `subscription_limits_verified=false` remains accurate. SDK adapter timeout/redaction/endpoint parity is not accepted; the existing probe remains in use.

W1-04c remains `BLOCKED_RESEARCH` for replay-data completeness, first-seen news, historical float/universe/account state, full-depth tape and an untouched holdout. W1-05 remains partial until the capability probe's subscription-limit status and future SDK reuse decision have sufficient acceptance evidence. G01–G04 remain unaccepted. No paper strategy order or formal paper evaluation occurred.

On 2026-10-05 local time, a bounded read-only SIP history query for AHPI 2021-07-13 17:08:00Z–17:10:00Z followed all pages: trades returned 10,357 records over two pages; quotes returned 3,068 records in one page. Every request returned HTTP 200 and pagination ended before the 25-page cap. Only counts and page-completion metadata were printed; response values were discarded and no dataset was persisted. This repairs the earlier “pagination incomplete” inventory for this two-minute window only. It does not establish data completeness outside the requested interval, provider arrival/first-seen timestamps, full-depth Level 2, point-in-time news/float/universe/account data or an unseen holdout.

## W1-05 — Read-only capability-probe milestone accepted, 2026-10-05

The user confirmed a normal Alpaca brokerage account using paper trading, so the Paper Only account restriction is not applicable. The regenerated [capability report](../evidence/alpaca-capabilities-2026-10-06.json) uses `paper_trading_endpoint_validated` to report configuration instead of suggesting the account belongs to the Paper Only customer category. It contains only endpoint status and field-presence flags. REST/stream statuses were verified again through the configured paper/data endpoints; the probe made no order and printed no account or market values. Tests prove missing credentials report `BLOCKED_CREDENTIALS` and sensitive values are redacted.

W1-05 is checked as the read-only **probe milestone**. The report correctly leaves per-account limit-boundary enforcement unverified (`subscription_limits_verified=false`); current Basic documentation lists 30 equity WebSocket symbols and 200 historical requests/minute. The official SDK candidate was reviewed but not substituted because bounded-timeout, redaction and paper-endpoint adapter parity has not passed. This milestone does not certify realtime SIP, complete historical replay, SIP equivalence or order readiness. W1-04c, parent W1-04 and G01–G04 remain pending/blocked.

Validation after the schema correction: `rtk proxy .venv/bin/python -m pytest -q` passed 46 tests; `rtk proxy .venv/bin/ruff check .` passed; `rtk proxy .venv/bin/ruff format --check src/alpaca_ross_bot/capabilities.py scripts/probe_alpaca_capabilities.py tests/test_capabilities.py tests/test_local_credentials.py` passed; `rtk proxy .venv/bin/python -m mypy --strict src scripts tests` passed for 18 source files; `rtk git diff --check` passed. Repository-wide format check remains failed only for the two W2-owned files noted above, which were not changed.

## W1-04c — Dated Ross development case added, 2026-10-05

The user's account-category confirmation is recorded in the offline inventory: this is a normal Alpaca brokerage account using Paper Trading, so the separate Paper Only customer's IEX-only wording does not apply. A first-party Warrior Trading transcript, [Behind the Trades Episode 10](https://www.warriortrading.com/reset-bad-month-behind-trades-ep-10/), explicitly dates the discussion to November 2, 2017 and narrates PYDS as a 10-second-chart micro-pullback trade, plus IZEA and SECO abstentions. We recorded these as exposed documentary development labels; price normalization from the transcript shorthand is marked as interpretation. The episode omits point-in-time market/news/float/universe/depth evidence and complete order/stop/partial/exit records, and its embedded video was not independently inspected. Thus it adds a dated source case but no complete replay fixture or holdout; W1-04c remains `BLOCKED_RESEARCH` and no paper evaluation began. See [`docs/w1-04c-offline-inputs.md`](w1-04c-offline-inputs.md).

## W1 closeout review — 2026-10-06

See the [closeout blocker register](week1-closeout-2026-10-06.md) for the complete W1 dependency/gate status. G01 and G02 are accepted for the current W1 scope: the 11 inventoried strategy domains all have version-scoped IDs/classifications, currently all blocked; live endpoint configuration is rejected and no order-submission path exists. This does not enable any trade. W1-04 remains unaccepted as an executable candidate, W1-04c remains blocked for historical replay inputs/holdout, W1-06 and W1-06a remain dependency-blocked, and W1-07 is not complete. G03 and G04 remain unaccepted; G05/G06 remain open and outside this W1 review. Astra Ultra's 2026-10-06 public-source pass found EDGAR timing is not first-availability timing, the published Ross scanner-history horizon is only six months, and no free verified source closes the missing float/news/universe/depth/account/order fields. The same review found Warrior's terms prohibit automated harvesting; no scraper/data collection was built. No order, purchase, live setting or formal paper evaluation occurred.

## W1-06 / W1-06a research inventories — 2026-10-06

The [data capability matrix](w1-06-data-capability-matrix.md) records each proposed input independently for free `LIMITED_COVERAGE_PAPER` and a later paid SIP review. It distinguishes observed IEX and news endpoint reachability, denied latest SIP, documented delayed SIP history, and unknown point-in-time float, universe, corporate-action versioning, raw-news revisions, depth, and retention/replay rights. The [Webull OpenAPI review](w1-06a-webull-openapi-review.md) records first-party non-display and scanner APIs, missing numeric API price and account entitlement, documentation conflicts over capacity/pagination, and unverified CTA/UTP, RVOL formula, float, raw news and contract grants. Both are bounded research artifacts; parent W1-04 and W1-04c remain blocked, and W1-06, W1-06a, W1-07, G03 and G04 are not checked.

An independent `gpt-6-astra` / `ultra` public-source pass gathered Webull endpoint and entitlement evidence. The coordinator directly checked the cited official overview, FAQ, changelog, rate-limit and endpoint pages. A verified Ollama Cloud `gemma4:31b` public-only technical-analysis call independently flagged the same unsupported SIP, pricing, RVOL, raw-news, float, licensing and conflicting rate-limit claims; model output was treated as critique, not acceptance evidence. No credentials, account values or nonpublic documents were sent to Cloud.

Validation after the document edits: `rtk proxy .venv/bin/python -m pytest -q` passed 46 tests; `rtk proxy .venv/bin/ruff check .` passed; `rtk proxy .venv/bin/python -m mypy --strict src scripts tests` passed for 18 source files; a targeted local Markdown-link existence check found none missing. No code, trading state, subscription or credential files changed.

## W1 user scope change and alternative-case audit — 2026-10-06

The user chose Alpaca Basic/IEX as the current live equity feed, deferred Webull/paid SIP, and accepted a separately named hybrid strategy for future Alpaca paper **only after** independent strategy, risk and data acceptance. The repo `AGENTS.md`, `TODO.md`, 8-week plan and [W1 alternative-author audit](w1-alternative-trader-and-prospective-plan-2026-10-06.md) record this. `W1-04d` and `G01H` track hybrid source/version provenance separately from the blocked Ross baseline. The current limited feed remains `NOT_SIP_EQUIVALENT`; Alpaca's published movers/most-active endpoints describe SIP-derived rankings, not a proven IEX-only full-market scanner. Historical gaps remain explicit; prospective evidence is a future predeclared observation plan, not an accepted historical replay or current paper authorization.

Two independent `gpt-6-astra` / low public-source passes found trader-authored Humbled Trader, SMB and Bear Bull Traders examples. The coordinator directly reopened those original pages, including the dated Aziz loss/re-entry example in TSLL, and excluded Investors Underground pages that returned 403 on direct open. A verified Ollama Cloud `gemma4:31b` public-only analysis was used as a bounded critique; the coordinator rejected its implication that missing historical ledgers would prevent collecting *future* first-seen evidence. No other trader's number or signal was promoted to the Ross manifest or hybrid code.

The W2, W3 and W4 native Codex heartbeat prompts were updated in place for the new scope; the app reported success and their saved prompt text was read back. The original recurrence schedules and target chats were preserved. The W1 recurrence's date window has passed, so no duplicate overlapping schedule was created. No order, data purchase, Webull account operation, live endpoint or formal paper run occurred.

Validation after the scope/document edits: `rtk proxy .venv/bin/python -m pytest -q` passed 46 tests; `rtk proxy .venv/bin/ruff check .` passed; `rtk proxy .venv/bin/python -m mypy --strict src scripts tests` passed for 18 source files; targeted local Markdown-link existence check found no missing links. All repository files remain untracked in the existing Git repository; `git diff --check` would not inspect those contents, so it was not used as evidence for this edit.

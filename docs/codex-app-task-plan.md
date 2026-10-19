# Codex App Task Scheduler plan

Status: registered and active on 2026-09-28. This plan uses Codex App native scheduled tasks. It does not use Windows Task Scheduler, cron, systemd, or a local Codex CLI runner.

At registration time, the repository was not in the Codex saved-project registry. The user subsequently created the local `Alpaca-bot` Project (`d800ee3c-fb78-4be3-9ac7-f0c14dff6e0d`) for `/mnt/t/alpaca-bot`. On 2026-09-29, Codex listed W1 and W2 under that Project. W3 and W4 remain valid same-directory scheduled chats, but the current 50-thread listing did not expose their Project association; verify that association before describing all four as Project members.

## Chat and Todo model

Each major development project is one native Codex App scheduled task and therefore one persistent chat. Every scheduled run for that project must resume the same chat and continue its remaining child Todo items. A child Todo never creates another chat.

The four development chats share the same repository but have non-overlapping primary ownership. A later chat must read the completion evidence and blockers from earlier chats before starting dependent work. Concurrent edits to the same file are forbidden.

Paper-month automation is not created now. It may be created as a fifth persistent chat only after `W4-07` and global gate `G05` pass.

## Shared task instructions

Every scheduled chat must:

- work in `/mnt/t/alpaca-bot` and read `AGENTS.md`, `TODO.md`, this file and `docs/development-plan-8-weeks.md`;
- continue only its assigned major Todo section and preserve the same chat across runs;
- select the highest-priority pending child whose dependencies are complete;
- follow Ross-first fidelity and mark unresolved strategy behavior `BLOCKED_RESEARCH` rather than inventing a substitute;
- treat Webull as the first alternate scanner/market-data candidate to validate while Alpaca remains the paper execution target; verify OpenAPI entitlements, actual price, premarket gainers, 10-day relative-volume semantics, candidate recall, float/news, streaming capacity, latency, and consolidated SIP versus Nasdaq-only coverage before claiming Ross equivalence;
- remain paper-only; never purchase data, enable live endpoints, submit live orders or start formal paper strategy orders;
- run targeted tests plus required lint/type checks and update a checkbox only after acceptance evidence passes;
- diagnose and safely repair reversible environment faults; record a full ENV_FAULT packet when unresolved;
- notify on a completed child, a material blocker, a failed gate or completion of the major project; otherwise continue without routine status noise;
- stop when its major project gate is complete or a dependency requires another chat/user/external-state change.

## Task 1 — Foundation, Ross evidence, and capabilities

- Todo ownership: `W1-01` through `W1-07`, plus relevant parts of `G01`–`G04`.
- Schedule: 2026-09-28 through 2026-10-02, Monday–Friday at 06:00 America/Los_Angeles.
- Persistent chat title: `Alpaca Bot W1 — Fidelity and Capabilities`.
- Completion: Week 1 gate review passes or remaining items are explicitly blocked with evidence and safe downstream work is identified.

## Task 2 — Event data, replay, scanner, and news

- Todo ownership: `W2-01` through `W2-08`.
- Schedule: 2026-10-05 through 2026-10-09, Monday–Friday at 06:00 America/Los_Angeles.
- Persistent chat title: `Alpaca Bot W2 — Data Replay Scanner News`.
- Dependency: consume the accepted Week 1 research/capability inventories and blocked-with-evidence gate review. W2-03 read-only Alpaca Basic/IEX and news health work may begin; W2-05 scanner eligibility and strategy-specific W2-06 news rules wait for an accepted Ross or separately labeled hybrid version and applicable data gates. Webull and paid live SIP are deferred. Do not reinterpret unresolved strategy versions or submit orders.
- Completion: Week 2 replay/fidelity gate passes or affected paths remain explicitly blocked.

## Task 3 — Strategy, risk, execution, and reconciliation

- Todo ownership: `W3-01` through `W3-08`.
- Schedule: 2026-10-12 through 2026-10-16, Monday–Friday at 06:00 America/Los_Angeles.
- Persistent chat title: `Alpaca Bot W3 — Strategy Risk Execution`.
- Dependency: Week 1 strategy version and Week 2 point-in-time data contracts.
- Completion: deterministic strategy/risk and paper execution gates pass; the four-week paper protocol is pre-registered.

## Task 4 — Integration, audit, and release freeze

- Todo ownership: `W4-01` through `W4-07`, including `G05`.
- Schedule: 2026-10-19 through 2026-10-23, Monday–Friday at 06:00 America/Los_Angeles.
- Persistent chat title: `Alpaca Bot W4 — Integration and Release Gate`.
- Dependency: verified outputs from Tasks 1–3.
- Completion: release candidate and clean $500 paper starting state are frozen, or formal paper is blocked with exact evidence.

## Deferred Task 5 — Four-week frozen paper trade

- Todo ownership: `P-01` through `P-05`.
- Schedule: create only after Task 4 proves `W4-07` and `G05`; intended duration is four complete weeks.
- Persistent chat title: `Alpaca Bot Paper — Frozen Four-Week Run`.
- Runtime rule: no strategy tuning. Any behavior-changing fix aborts the window, creates a new release and restarts a complete four-week window.

## Registration acceptance

The native scheduler setup was verified on 2026-09-28: Codex App created four active heartbeat automations, each bound to its own persistent same-directory chat, and rendered all four automation cards successfully. W1 received an immediate first-run prompt because its scheduled start had already passed. A subsequent heartbeat arrived at `2026-09-29T06:00:15Z`, which is 23:00 on September 28 in Los Angeles. The app interpreted the recurrence hour as UTC. On September 28 all four active recurrences were corrected to 13:00 UTC, equivalent to 06:00 Los Angeles daylight time throughout the September 28–October 23 development window. Persisted rules and target chat IDs were read back after the update. The W1 heartbeat arrived at `2026-09-29T13:00:14Z`, verifying the corrected W1 delivery time; W2–W4 delivery remains to be observed.

| Major project | Persistent chat ID | Automation ID | State |
| --- | --- | --- | --- |
| W1 | `01a0e9d3-c0d0-77a0-9bb1-297ce3b9c037` | `alpaca-bot-w1-fidelity-and-capabilities` | Active; first run dispatched |
| W2 | `01a0e9d3-d393-7963-8ca7-5a7184389e5b` | `alpaca-bot-w2-data-replay-scanner-news` | Active |
| W3 | `01a0e9d3-e5db-7d50-94d0-cafedca79062` | `alpaca-bot-w3-strategy-risk-execution` | Active |
| W4 | `01a0e9d3-f9fa-7f13-a52a-b27c99459657` | `alpaca-bot-w4-integration-and-release-gate` | Active |

Paper-month automation remains intentionally unregistered until `W4-07` and `G05` pass.

## W3 follow-up — October 19 market-open readiness

Prepared on October 18, 2026, at the user's request. Reuse the existing W3
heartbeat and persistent chat; do not create a fifth project chat or a formal
paper-month task. Intended one-day follow-up times are **06:20, 06:30 and 06:35
America/Los_Angeles** (13:20, 13:30 and 13:35 UTC). Scheduler delivery is not an
exchange-clock guarantee. The machine must remain powered on and the desktop
app running for local scheduled work; see [official OpenAI documentation](https://learn.chatgpt.com/docs/automations?surface=app).

The 06:20 phase collects a genuine pre-open E0 and its durability receipt using
the previously bound dedicated-account journal. The two open phases collect
bounded read-only account/lifecycle and IEX diagnostic evidence and explicitly
observe the source-bound daily-risk owner. Credentials and runtime root must be
specified, never replaced with generic defaults. The collector's code-level
date/window guards refuse a late/mismatched run before loading credentials or
connecting. Existing-account checks prevent a fresh journal from silently
substituting for the dedicated account. Account identity must still match the
journal's authenticated binding.

The pre-open collection window is 06:15 inclusive to 06:25 exclusive; open
collection is 06:30 inclusive to 06:40 exclusive, all Los Angeles time on October
19 only. Fresh official calendar evidence must confirm the declared 09:30 New
York opening. Missed pre-open collection is `NOT_CAPTURED`, never an intraday
E0. Open samples may still record diagnostic failures without an E0; missing
baseline and source/strategy/data/protection gates remain blocked. These finite
samples do not establish continuous monitoring of a small-cap universe.

This follow-up cannot submit orders, cancellations, resets, subscriptions or
formal-paper decisions. Each result must retain `NOT_SIP_EQUIVALENT`, source age,
private integrity/rebuild status, zero intents and explicit remaining gates.
After the final phase, or if the one-day schedule has expired, delete the
heartbeat and record completion or the missed window. Keep intermediate runs
quiet unless a phase completes meaningfully, a gate fails or user action is
required. Registration/readback evidence belongs in Week 3 evidence; planned
times above alone do not prove scheduler registration or future delivery.

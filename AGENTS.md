# Alpaca bot operating rules

## Scope and inheritance

- This workspace currently contains planning and research, not an authorized trading service. Do not submit orders, install services, purchase data, or enable live trading from a planning request.
- Reusable guidance was inspected in `/mnt/t/invest-project-variation/AGENTS.md` on 2026-09-27; the user confirmed this source project. No source code, credentials, runtime state, or deployment configuration was copied.
- Confirmed target: US user, USD 500 starting capital, fully automatic operation, paper first, based on Ross Cameron's small-account strategy. Respect actual Alpaca account capabilities; do not import another broker's leverage or invent missing discretionary rules. Research source ambiguities before freezing executable parameters.
- The user has authorized Ross's strategy as the default for all strategy rules: use clearly documented, applicable original rules directly without asking for parameter-by-parameter approval. Research only missing, conflicting, or insufficiently specified details, using primary sources and the global Codex research routing. Determine version applicability from evidence; do not treat this authorization as choosing one of several conflicting profiles. Ask the user only when research leaves a genuine preference or material unresolved tradeoff. Record original rules, derived calculations, broker adaptations, and newly researched additions separately.
- On 2026-10-06 the user additionally accepted a **separately labeled hybrid strategy for future Alpaca paper** if Ross's public evidence cannot specify the full $500/1× system, conditional on independent strategy, risk, data and execution acceptance. Research named traders' own public rules and cases as `OTHER_TRADER_ANALOGY` or hybrid-source evidence; never relabel them as Ross rules or claim Ross fidelity for a hybrid result. Keep the Ross candidate as a separate comparison baseline. This authorization does not freeze any hybrid parameters or permit paper orders before release gates pass.
- The user does not require access to, or proof of an exact reconstruction of, the members-only historical Chapter 8. The research objective is to infer as much of its likely pullback-strategy content as public evidence permits. Explicit, source-linked hypotheses are allowed in the research record, including carefully separated analogies from other traders. Label direct Ross statements, Ross-based inferences, other-trader analogies, and unknowns separately; record competing interpretations and confidence limits. An inferred rule is not a Ross quote or `ROSS_DIRECT`, and an unresolved execution/risk rule cannot silently become an order rule. Lack of membership alone is not a research-completion blocker.
- Fidelity is the primary objective. Do not replace a Ross rule, workflow, data dependency, or discretionary gate with a merely reasonable, cheaper, simpler, or locally convenient alternative. Apply the documented rule as written when it is unambiguous and compatible. If Alpaca or automation cannot reproduce it directly, mark that path blocked, research the missing semantics or a functionally equivalent implementation, and record any residual difference before proceeding. Engineering controls may enforce timing, auditability, reconciliation, and fail-closed safety, but may not select trades or change strategy behavior unless supported by Ross evidence.
- For the newly authorized hybrid candidate, fidelity means fidelity to its **own** source-scoped, versioned specification; it must not be advertised as Ross-equivalent. The current user-selected data scope is Alpaca Basic/IEX for live equities with Alpaca paper and separately validated Alpaca news. Webull and paid real-time SIP are deferred. IEX coverage must carry `NOT_SIP_EQUIVALENT`, and no all-market scanner claim may be made from IEX-only observations. A later data-source change requires a separate review, not an automatic switch.
- If complete historical point-in-time cases cannot be acquired, record the specific missing fields and use a predeclared prospective shadow/paper evidence plan when its gates permit. Future observations cannot retroactively validate historical Ross cases or create an unseen historical holdout. Do not lower strategy, risk, data-health or order-safety acceptance merely because a historical archive is unavailable.
- Preserve the user's global RTK, ENV_FAULT, Headroom lossless, and research-routing instructions. Project-specific evidence, trading, and deterministic validation boundaries remain in force.

## Uncertainty and evidence

- Do not present an inferred rule as a fact or silently choose foundational behavior, policy, risk tolerance, or architectural tradeoffs for the bot. Research-stage reconstruction hypotheses are permitted under the evidence labels above. Ask the user when a remaining choice requires their preference or authorization.
- Distinguish confirmed facts, proposed design, and unresolved decisions. Record source date/version and direct acceptance evidence. Never mark a requirement complete without verification.
- Strategy examples, discretionary trading videos, engineering success, and paper profit do not establish a profitable automated strategy or authorization to trade.

## Execution environment

- Work through Ubuntu/WSL. Prefix shell commands with `rtk`; use `rtk proxy` for unfiltered diagnostics. Pass the workspace directory explicitly before mutations.
- With `functions.exec`, print shell `result.output` directly and emit exit code/session ID separately. Do not serialize the entire result object.
- Stop source-dependent work on opaque CCR output; preserve Headroom's persisted lossless setting and diagnose boundedly. A placeholder does not prove command failure.
- Treat an observed environment fault as ENV_FAULT, with category, evidence, affected_scope, safe_to_continue, and recommended_action. Stop the affected operation; do not disguise it with application changes or ordinary retries.
- The user explicitly authorizes autonomous environment repair when it is safely resolvable without their input. Diagnose the root cause, use the smallest scoped reversible repair, preserve unrelated state, and verify the original operation afterward. Request tool-level escalation when necessary rather than repeatedly asking the user for already-authorized work. Stop and explain if approval review rejects the required action, credentials or payment are needed, repair is destructive, or intent remains ambiguous. Do not bypass sandbox controls or treat this as permission for live trading or global destructive resets. Record resolved faults and acceptance evidence, not just the original failure.
- Source-project asyncio/WSL issues are diagnostic history, not confirmed faults here. Do not preemptively change scheduling or global permissions.
- Use a project-local virtual environment and explicitly pinned, validated dependencies when implementation begins. Do not copy the source project's virtual environment.

## Programming and validation

- Use the shared `codex-worker-loop` skill for non-trivial work and `karpathy-guidelines` when writing/refactoring code; read each applicable SKILL.md. Shared root: `/mnt/c/Users/xuanl/.codex/skills`.
- Prefer simple, small modules with one responsibility, existing interfaces, explicit schemas, deterministic computation, and targeted edits. Add abstractions/dependencies only for a demonstrated need.
- Compute indicators, sizing, risk, and performance in code. Model output cannot override deterministic validation or risk vetoes.
- Use cached data only after freshness and eligibility validation. Keep collection health, candidate qualification, strategy evaluation, and execution separate.
- Inspect direct code and tests before edits; run relevant checks for behavior changes and broaden only with evidence. Report exact commands, results, and unresolved blockers.
- Preserve unrelated changes and historical evidence. Do not import source-project committee topology, Jira authority, Kraken endpoints, deployment paths, frozen research calendars, or runtime configuration.

## Trading and state boundaries

- Paper/live environments must have separate credentials, state, and explicit endpoint validation. Paper testing does not authorize live trading. Any executable phase requires confirmed strategy and risk configuration.
- Plan for capital growth and drawdown through explicit versioned stages. Distinguish equity-based sizing, broker capability changes, and strategy-version changes; do not infer Ross strategy switching thresholds from account size alone. Automatic transitions require previously approved conditions, evidence gates, downgrade behavior, and durable audit records; they never enable live trading automatically.
- Missing, stale, malformed, or ambiguous evidence blocks new exposure. This must not silently abandon protection of an existing position: preserve valid protective orders, reconcile, and escalate according to an approved incident policy.
- Give state a canonical owner; record durable order intent before submission and reconcile uncertain acknowledgments before retrying. A timeout does not prove rejection.
- Audit decisions and rejections through compact evidence references. Make retries idempotent and migrations recoverable; preserve reader compatibility.
- Never expose credentials, environment values, or private account data in prompts or ordinary logs. Keep required operational account state in access-controlled local storage, outside Git.
- Keep generated runtime data under an independently configured ignored path; do not reuse the Kraken state directory. Ponytail remains off by default.

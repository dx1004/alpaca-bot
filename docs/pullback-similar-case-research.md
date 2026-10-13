# Similar-case research for public pullback rules

Date: 2026-09-30. Scope: W1-04b, research only. This extends [R0](ross-public-pullback-r0.md); it does not replace its version boundaries or accept an executable strategy. The user asked whether more research and similar cases are needed. **Yes: the initial five teaching cases lack exact sessions and replay inputs, and do not cover the remaining decisions adequately.**

This batch adds four documentary decision contexts and a sampling protocol. The protocol is an engineering research design, not a Ross trading rule. Independent review uses the existing researcher explicitly configured as `gpt-6-astra` with `ultra` reasoning; the coordinator checks primary text. There is no subscription spend, account connection, order or formal paper evaluation.

## What makes a case similar

Record, rather than assume, similarity across setup version, chart timeframe, session, price/float/liquidity context, catalyst availability, momentum stage, account equity and buying power, broker execution, and data actually available at the decision. Unknown dimensions remain null. A similar-looking chart does not establish the same trade.

Prioritize Ross's original sessions matching the chosen setup and account phase. A Ross case from another capital stage can inform a source-scoped signal or rejection, but cannot supply $500 sizing. Other traders' comparable examples may test a separately named hybrid hypothesis; they cannot close a Ross attribution gap. A challenge title saying “$583” does not establish the balance on its sixtieth day.

The next capital-matched search should focus on the **post-reset, $2,000 no-leverage phase** identified in the [official 2025 challenge chronology](https://support.warriortrading.com/support/solutions/articles/19000168902-small-account-challenges-at-warrior-trading) (`ROSS_CHALLENGE_CHRONOLOGY`, modified 2025-10-09). That is a specific original-source lead, not a verified individual trade or a $500 risk mapping. Research the actual episodes and their decision-time evidence before extrapolating.

## Coverage that the next batches must seek

| Case class | Question to answer | Acceptance boundary |
| --- | --- | --- |
| Eligible setup followed by gain | Can entry, stop and management be described using only observations then available? | Verify eligibility separately from the later outcome; a gain does not prove the decision followed rules. |
| Eligible-looking setup followed by loss | Can the same pre-entry definition admit a trade that loses? | An author's claim of following one condition is not verification of every filter. Do not add a hindsight filter just to erase a losing example. |
| Rejected opportunity, including later gain | Why did Ross abstain, and would the available data support that decision? | Preserve no-trade decisions even when price later rises; distinguish rule-based rejection, discretion and missed execution. |
| Execution/risk constraint | Did spread, depth, slippage, buying power or an account stop prevent the intended action? | Keep setup eligibility, feasible order size and actual fill separate. Do not import another broker's controls. |
| News and regime boundary | Was the catalyst fresh/available, and was the setup described as early, extended, hot or cold? | Capture first-seen evidence and the author's contemporaneous description. Do not infer a news-age cutoff or regime label from later P&L. |
| Conflicting or unobservable example | Does it require missing depth, unidentified bars, unclear dates or an exception? | Keep an explicit unresolved label; do not discard the case or impute the missing condition. |

Match winning, losing and rejected opportunities within a source version where evidence permits, while retaining distinct market sessions. An outcome-enriched teaching sample is useful for finding missing definitions; its win rate is not an estimate of the strategy's natural win rate.

## Additional primary cases

Accessed 2026-09-30. Exact publication/trading dates are unverified unless stated. Calendar text, image filenames and nearby “Recent Posts” dates do not by themselves verify the case date. C06–C09 continue R0's identifiers, but nine documentary contexts are **not nine independent statistical observations**.

| Case / registry source / locator | Primary observation | Gap addressed and limits |
| --- | --- | --- |
| C06 — ASUR / `ROSS_FALSE_BREAKOUT_RECAP` / [Too Many False Breakouts](https://www.warriortrading.com/too-many-false-breakouts-2500-ross-trade-recap/), ASUR paragraph | Ross describes waiting for a one-minute micro-pullback, then experiencing a false breakout and about $700 loss. | Adds a loss not explained solely by the admitted deferred-exit mistake in R0 C04. This verifies his reported waiting behavior, not full setup compliance. Shorthand prices are not reconstructed; account context is not $500/1×. |
| C07 — SKLN and watchlist abstention / `ROSS_NO_TRADE_AUG1` / [No Trades Today](https://www.warriortrading.com/no-trades-today/), watchlist and SKLN paragraphs | Ross avoided SKLN after premarket weakness, intending to wait for a first one-minute pullback that he says did not occur; it nevertheless rose before reversing. The same recap rejected TDW for low volume and CBMX for merger-related limited movement. | Adds no-trade and catalyst-quality examples. “Aug 1” lacks a verified year. Different rejected symbols are one session cluster here; the reported 1,200-share TDW observation is not a universal minimum, and the merger example is not a blanket ban. |
| C08 — PRAN / `ROSS_DAY60_PULLBACK_DECISIONS` / [Day 60 recap](https://www.warriortrading.com/day-60-583-challenge-411-79/), PRAN discussion and buy-trigger Q&A | Ross missed an initial opportunity, rejected a later entry near $3.80 against a $3.45 low, and subsequently traded a five-minute setup. He describes an exit when buying failed to clear an apparent hidden seller near $4. | Adds structural-risk rejection, multiple timeframes and depth-based management. His example explains why a resting price trigger could enter despite an observed seller. Do not turn 35¢ into a universal cap. Later in this recap he reports approximately $110,000, so this is explicitly outside the starting-capital stage. Exact session and raw depth remain missing. |
| C09 — NAKD after account loss limit / `ROSS_MAX_LOSS_RECAP` / [Max Loss, Max Frustration](https://www.warriortrading.com/trade-recap-max-loss/), NAKD and broker-cutoff discussion | After losses, Ross stopped initiating trades despite a later NAKD rally. He distinguishes the broker's new-trade restriction from automatically closing an existing position. | Relevant to account-level rejection; `OUT_OF_SCOPE` for proving micro-pullback eligibility. The roughly $79,000 account and $5,000 cutoff are historical context, not $500 parameters or Alpaca capabilities. Stop-trading and position protection require separate semantics. |

Corroborating workflow source: [Warrior support, Routing, Hot Keys, and Buying/Selling Process](https://support.warriortrading.com/support/solutions/articles/19000045007-routing-hot-keys-and-buying-selling-process), modified 2026-07-22, item 3 (`ROSS_ENTRY_WORKFLOW_SUPPORT`). It describes checking the chart and then sellers on Level 2 before entry. For that workflow, candles alone are insufficient evidence of the full decision. This support article is not proof that its other offsets or mental-stop examples belong to the chosen micro-pullback version; none are adopted here.

These cases improve rejection and failure coverage. They do **not** resolve the $500/no-leverage risk profile, establish market-wide scanner recall, recover news first-seen times, or validate an automated Level 2 interpretation. A valid signal may still be infeasible under the actual account and data constraints.

## Sampling and review procedure

1. **Define the question before searching.** Map each candidate case to R0 Q1–Q6 and the exact proposition it could support or contradict. Start with account-stage risk provenance and source-scoped entry/rejection semantics.
2. **Keep an inclusion ledger.** Record source URL, author, version, locator, access date, verified trading date or null, symbol, account context, known inputs, selection reason and exclusion reason. Deduplicate mirrored videos/articles and mark shared sessions. Preserve unavailable/unresolved candidates instead of choosing only easy examples.
3. **Separate decision facts from outcomes.** Record setup qualification, observed trader action, stated rationale, executed/reported fill and eventual outcome as distinct fields. A retrospective description is not independently verified contemporaneous evidence.
4. **Label source fidelity first.** Use R0's `SUPPORTED / CONTRADICTED / UNRESOLVED / OUT_OF_SCOPE` per proposition. Log disagreements and assumptions. Do not count a partial condition as a fully qualified trade.
5. **Check $500 feasibility separately.** Once entry/stop/risk are established, use point-in-time buying power, eligible shares, spread, costs and partial-fill rules. No risk budget is supplied by this study design. Future leveraged cases stay in their own stage.
6. **Reserve new sessions before seeing outcomes.** All R0 and C06–C09 narratives are already exposed development evidence. A later holdout must use separate, verified sessions/data, selected by a fixed inclusion procedure before outcome inspection. Group related trades by session; no tuning after viewing the holdout. Without suitable inputs this remains pending.
7. **Evaluate more than gains.** Report definition coverage, review disagreements, abstentions, missed/extra labels and unavailable inputs before any execution or P&L claim. Strategy behavior stays frozen during a separately authorized paper window.

## When more research is enough for the next step

No arbitrary total such as 30, 50 or 100 cases proves profitability or fidelity. Close a research question when its source/version, decision-time observations, transition and applicable exceptions can be reproduced, and targeted counterexamples have been reviewed without unresolved contradictions for that selected version. Unobservable conditions must remain explicit blockers, not be replaced by convenient defaults.

Before executable acceptance, every required signal/risk/exit condition must pass its evidence gate; the relevant broker/data inputs and $500 risk profile must also be established. A case inventory can be complete as a document while trading remains blocked. Broadening to more traders is useful only for an identified gap and a separately attributed hypothesis.

To protect the development schedule, use bounded batches tied to these closure questions. Each batch should report what ambiguity it resolved, what contradiction it found and what data remains missing. Stop gathering redundant anecdotes when the next blocker is a precise rule definition, member-only evidence or missing data; investigate that blocker directly. Research may proceed alongside already authorized foundation/data work whose dependencies are satisfied, without starting strategy orders early.

## Current status

This initial expansion supplies C06–C09 and a concrete coverage protocol. The user-requested [four-category deep research](ross-four-case-deep-research.md) continues with C10–C14, issuer records, an Alpaca execution comparison and source-scoped annotation requirements. W1-04b remains incomplete because the risk mapping and reproducible entry/exit definitions are unresolved. No R0 rule is promoted to executable status. Review and registry validation are recorded in [Week 1 evidence](week1-evidence.md); the [TODO](../TODO.md) continues to prioritize these research gaps.

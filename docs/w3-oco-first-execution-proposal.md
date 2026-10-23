# OCO-first execution feasibility proposal

Status: **USER_ACCEPTED_SEPARATE_RESEARCH_CANDIDATE**, 2026-10-20
America/Los_Angeles. The user selected “Allow a separate OCO-first research
candidate”. The [pinned research manifest](../evidence/oco-first-execution-research-v1.json)
defines `OCO_FIRST_EXECUTION_RESEARCH_V1` and candidate
`HYBRID_500_IEX_OCO_FIRST_RESEARCH_V1`, accepted from
`OCO_FIRST_EXECUTION_RESEARCH_PROPOSAL_1`.
Acceptance covers the separate candidate and provisional early-target behavior;
it is not an executable release, protection/close qualification or permission
for broker requests, formal paper or live trading. The original candidate and
its P11 guard remain preserved. Existing protection requirements remain in force.

## The change in architecture

Start with native OCO protection for already owned quantity instead of creating
a standalone stop and later trying to convert it into OCO. Subsequent target
changes would amend the existing OCO take-profit leg. No deliberate cancellation
of an established valid stop is part of this proposed path.

This is an independently accepted research direction with unresolved contracts
and a material exit-timing difference. It must not be represented as an
equivalent implementation of original P11 or as a Ross rule.

## Source-supported parts and limits

The [wash-protection guide](https://docs.alpaca.markets/us/docs/user-protection)
exempts OCO/complex orders from its ordinary wash check. The
[order guide](https://docs.alpaca.markets/us/docs/orders-at-alpaca#oco-orders)
supports OCO exits for existing positions and price amendment. These are useful
building blocks, not proof of the complete proposed partial-entry lifecycle.

The [replacement reference](https://docs.alpaca.markets/us/reference/patchorderbyorderid-1)
does not promise successful venue replacement from HTTP acknowledgement. Its
unchanged-attribute wording concerns the replaced order, not a specific guarantee
for its sibling stop. Advanced stop/target separation restrictions also constrain
a marketable target amendment. No documented equity reduce-only guarantee is
assumed. Multiple disjoint OCO reservations and exact fractional-residual support
remain qualification requirements, not established facts.

## Proposed lifecycle for evaluation

1. Keep the original source/risk/entry qualification and one-entry-attempt policy.
   On an observed first fill, durably request cancellation of the unfilled entry
   remainder and own all racing fills under the canonical execution ledger.
2. For new owned quantity, propose an OCO with stop `S` and provisional target
   `U = ceil_cent(3L - 2S)`, using the accepted entry limit `L`. Record intent
   before sending, and reconcile unknown acknowledgements before any retry.
3. A later fill would use a separate, disjoint owned-quantity allocation instead
   of resizing or canceling an established stop. Never round a fractional
   residual up or down to make it fit an unverified order interface.
4. Once entry is terminal and all entry executions reconcile, derive actual
   average `F` and final target `T = ceil_cent(3F - 2S)`. Propose amending each
   remaining group's take-profit to the same `T`; retain every valid stop.
5. A timed close could amend an existing take-profit to a marketable limit only
   when supported price constraints and observed liquidity permit it. Preserve
   protection on rejection/uncertainty. This is **not** a complete deadline
   disposition: when bid is at/below `S`, a limit above `S` cannot be marketable.

These are research steps, not an enabled dispatcher or fully qualified incident
policy. An early provisional exit does not authorize a replacement entry: own
and reconcile any racing original-entry fills, retaining the one-attempt rule.
The final average includes every entry execution, not just shares still held.

## What the arithmetic proves

If every entry execution respects `price <= L`, then `F <= L`. Monotonic cent
ceiling gives `U >= T`. Thus a sell-limit execution at or above `U` is not below
the eventual gross target price bound. This proves a price inequality only;
it does not authorize that execution time, prove allocation safety or preserve
broker protection.

The [exact arithmetic evidence](../evidence/w3-oco-first-feasibility-20261020.json)
contains a deterministic rational-value check and two counterexamples. It has no
account inputs, broker calls or acceptance authority. The general proof is the
monotonicity argument, not an assertion that finite cases establish all behavior.

## Irreducible differences from the current policy

- A provisional target can execute before entry termination/reconciliation.
  This behavior is now accepted for the separate research candidate only.
  The original incident owner explicitly rejects that state with
  `TARGET_BEFORE_ENTRY_TERMINAL`. A better exit price does not make the timing
  equivalent. An early group exit can coexist with later racing entry fills.
- First-fill detection, persistence, submission and broker acceptance take time.
  New late fills likewise need protection. This proposal does not eliminate
  those intervals or classify them as verified continuous coverage. It does not
  impose an invented promise of zero network latency either.
- Separate groups require exact fill-level ownership, reservation and recovery.
  Planned integer sizing does not prove that each partial execution is integer.
- Target-only replacement still needs evidence that its companion protection
  remains valid through replacement/rejection/fill races.
- A marketable-limit amendment cannot cover every deadline/price topology, halt
  or liquidity state. Retaining a stop is not the same as proving an intraday
  liquidation path; no silent overnight-holding adaptation is accepted.

## Qualification sequence and stop conditions

| Stage | Required evidence | Current state |
| --- | --- | --- |
| Algebra and policy comparison | Exact bound, early-exit and close-topology counterexamples | Completed; not equivalent to current P11 |
| Independent research version | Separate candidate/provisional early-exit decision; other qualification retained | User accepted research scope only; source/protection/close remain unqualified |
| Deterministic owner/model | Durable group allocation, timeout recovery, no double reservation and all six incident groups | Local source-derived incident receipts, durable entry allocation and six synthetic fault groups verified; native protection/exit mapping, qualified recovery and continuous runtime acceptance remain open |
| Broker mapping | OCO during pending entry/cancel, group reservations, exact residuals, target amendments and stop preservation | Unverified |
| Bounded isolated API experiment | Qualified data/account, reviewed request manifest, safe supported cleanup and restart path | Not authorized for dispatch by this document |
| Formal paper preregistration | Accepted release and all existing W3/W4 requirements | Blocked by prior stages |

The provisional early-exit decision has been made for this separate candidate.
It does not close broker/cleanup/data gates. Implementation must bind the new
research manifest explicitly; it cannot enter the original runtime by calling
the changed behavior an engineering-only change. This exit-allocation book is
not a second cash/loss-risk allocator, and no entry-risk version binding is
inferred from sharing the parent research inputs.

The research-only [allocation model](../src/alpaca_ross_bot/oco_research.py)
and [synthetic tests](../tests/test_oco_research.py) now implement exact lot
portions, unknown-acknowledgement retention, signed over-exit incidents,
all-entry averages and terminal-census invalidation. Its observations and
acknowledgements are declared inputs, not source-qualified facts. A private
[durable adapter](../src/alpaca_ross_bot/oco_journal.py) now preserves the model,
unknown reservations and identity conflicts across restart. The shared risk
owner separately records an explicit execution-contract opt-in while retaining
the parent entry signal and original budget. A local entry receipt now binds the
original positive held reservation and immutable source-execution census under
the private journal lock. Explicit candidate incident opt-in and six synthetic
integration cases are verified. Receipt origin remains engineering-only and
source admission remains false. A separate GET-only native OCO collector and
interpreter now retain account-bound nested group and per-execution activity
observations, including transport failures, bounded pagination and temporal
conflicts. These observations are not yet bound to local exit intent/ACK
ownership; the allocation book's native exit/group facts remain declared.
continuous runtime supervision, qualified completed-slot recovery and actual
broker protection/close acceptance remain open. There is no broker dispatcher;
every operational authority remains false. Local source-derived incident receipts
now retain scoped lifecycle/position facts and quarantine completed-scope late
fills without restoring risk or accepting native protection. Manual and
source-derived observations cannot mix in one scope. Implementation verification
and its limits are recorded in
[Week 3 evidence](week3-evidence.md#w3-06-source-derived-incident-observations-and-completed-scope-quarantine--2026-10-20).

No support submission is required by this proposal. Only the new research
acceptance scope is granted. Credentials, runtime authorization, the original
production gates and broker state remain unchanged.

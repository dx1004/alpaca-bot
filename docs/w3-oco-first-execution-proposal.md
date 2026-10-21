# OCO-first execution feasibility proposal

Status: **PARTIAL_ROUTE_NOT_ACCEPTED**, 2026-10-20 America/Los_Angeles.
Proposed research identity: `OCO_FIRST_EXECUTION_RESEARCH_PROPOSAL_1`.
This preserves the original candidate for comparison. It is neither a new
accepted specification nor permission for broker requests, paper launch or live
trading. The user's existing protection decision remains in force.

## The change in architecture

Start with native OCO protection for already owned quantity instead of creating
a standalone stop and later trying to convert it into OCO. Subsequent target
changes would amend the existing OCO take-profit leg. No deliberate cancellation
of an established valid stop is part of this proposed path.

This is a proposed implementation route with unresolved contracts and a material
exit-timing difference. It must not be represented as an equivalent implementation
of accepted P11 or as a Ross rule.

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

These are research steps, not an enabled dispatcher or frozen incident policy.

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
  The current incident owner explicitly rejects that state with
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
| Independent research version | Explicit treatment of provisional early exits, protection intervals, residuals and deadline failure | Not accepted; original rules unchanged |
| Deterministic owner/model | Durable group allocation, timeout recovery, no double reservation and all six incident groups | Not implemented for this candidate |
| Broker mapping | OCO during pending entry/cancel, group reservations, exact residuals, target amendments and stop preservation | Unverified |
| Bounded isolated API experiment | Qualified data/account, reviewed request manifest, safe supported cleanup and restart path | Not authorized for dispatch by this document |
| Formal paper preregistration | Accepted release and all existing W3/W4 requirements | Blocked by prior stages |

The next meaningful decision is whether to accept a separately specified
research version with provisional early-exit behavior. That alone cannot close
the broker/cleanup/data gates. If that behavior is rejected, this proposal must
not enter the original runtime by calling it an engineering-only change.

No support submission is required by this proposal. No production policy,
acceptance flag, credential, runtime state or broker order was changed.

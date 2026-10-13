# Research rerun under Astra Low and Ollama Cloud routing

Date: 2026-09-30. **Latest status: execution paths recovered; see the completed [rerun comparison](ross-research-routing-comparison.md).** The blocked attempt below is preserved as history and no longer describes the rerun's current status. Windows Ollama Cloud was already configured, and approved public-only ephemeral Astra Low CLI workers completed retrieval. The built-in allocation limit itself remains unresolved.

Request: reread AGENTS.md, redo the four-category research under the new routing, and compare with the previous result. The on-disk instructions were read and explicitly require `gpt-6-astra` with `low` reasoning for independent retrieval, followed by Ollama Cloud `gemma4:31b` analysis. The new routing overrides earlier Ultra/local-Ollama defaults. The worker-loop and Ollama-routing skills were read; their older model defaults do not override the user's current instructions.

## Initial blocked-attempt execution evidence

- A new independent retrieval assignment was submitted with exactly `model=gpt-6-astra`, `reasoning_effort=low`, and no inherited conversation. It failed with `agent thread limit reached`; the assignment did not run.
- Agent listing showed completed older researchers. The exposed collaboration tools provide neither a close/delete operation to release their allocation nor a model/effort override for resuming them. No old Ultra agent was reused as a substitute.
- A bounded check in this WSL process found no Ollama CLI and no configured `OLLAMA_API_KEY`, `OLLAMA_HOST`, `OLLAMA_API_BASE` or `OLLAMA_BASE_URL`. Only presence booleans were printed. No callable Ollama tool was found. These checks do not prove that another host lacks a configured Cloud connection.
- No Cloud request, model availability probe, synthesis or fresh web retrieval was executed. Exact Cloud endpoint/model availability and authentication remain unverified. No credentials were inspected or requested in chat.

## Initial blocked-attempt comparison status

The previous [four-category report](ross-four-case-deep-research.md) remains historical evidence, not newly reproduced evidence. All four comparison rows are **NOT_RUN**, not agreement or disagreement:

| Category | Previous evidence inventory | New-routing result |
| --- | --- | --- |
| Pullback loss | C06 and C10; C13 setup-family boundary | NOT_RUN |
| Abstention followed by rise | C07, C09, C11 | NOT_RUN |
| Capital/spread/fills | C08, C11, C14 and broker documentation | NOT_RUN |
| News/regime contrasts | C10–C13 and issuer corroboration | NOT_RUN |

No claim about relative model quality, improved research, unchanged conclusions or newly closed gaps is justified. The prior acceptance milestone is preserved; this rerun has not passed acceptance. Trading gates and strategy parameters are unchanged.

## Environment packets

```text
status: ENV_FAULT
category: orchestration-capacity
evidence: Required Astra Low spawn returned agent thread limit reached. Available tools do not expose releasing completed agent allocations or changing a resumed agent's model/effort.
affected_scope: Required independent retrieval and downstream fresh-evidence synthesis/comparison.
safe_to_continue: Yes for routine local prerequisite checks and preserving status; no for claiming a rerun or substituting another researcher.
recommended_action: Restore capacity or resume this request in a context that can create the explicitly configured Astra Low agent. Do not reset global state or repeatedly retry unchanged allocation.
```

```text
status: ENV_FAULT
category: cloud-routing-prerequisite-unverified
evidence: Current WSL process has no Ollama CLI, no configured named Ollama connection/authentication variables, and no callable Ollama connector. No network inference attempt occurred.
affected_scope: Required Ollama Cloud gemma4:31b analysis stage.
safe_to_continue: Yes for local status work; no for claiming Cloud execution, model absence, or using local inference as a substitute.
recommended_action: Identify the existing secure Cloud connection, if configured elsewhere; otherwise configure it securely outside chat. Verify the official endpoint and exact model with the required retrieval route, then execute and retain redacted response metadata.
```

Resume procedure: collect fresh primary-source facts with Astra Low; send only the compact public evidence package to the verified Cloud model; retain source dates and inference metadata; have the coordinator verify material claims and arithmetic; compare each prior proposition as confirmed, corrected, newly supported or still unresolved. Preserve the previous report and write the comparison separately. No orders, purchases, services or formal paper evaluation are authorized by this research request.

# Research rerun: Astra Low retrieval and Gemma Cloud analysis

Date: 2026-09-30. Scope: the four categories in the [previous report](ross-four-case-deep-research.md), not a new audit of every source ever collected. Status: **research rerun and coordinator comparison completed; trading gates remain blocked**.

## Verified execution and environment recovery

The original [blocked attempt](research-routing-rerun-status.md) checked only the WSL process. Broader, bounded inspection found an existing Windows Ollama installation and Cloud registration. No new credentials, subscription, model download or service were needed.

- Windows interop initially failed inside the sandbox with `UtilBindVsockAnyPort: socket failed 1`. Tool-approved escalation made the original read-only `ollama.exe list` operation succeed. No global networking or sandbox settings were changed.
- `/api/tags` on the existing Windows service identifies `gemma4:31b-cloud` with `remote_host=https://ollama.com` and `remote_model=gemma4:31b`. A minimal inference returned `CLOUD_OK`, `model=gemma4:31b`, `done=true`. The [official Cloud documentation](https://docs.ollama.com/cloud) independently documents the remote model and endpoint.
- The built-in collaboration allocation still has a thread-limit fault. A separately approved **ephemeral, read-only Codex CLI worker** successfully performed independent retrieval with `model=gpt-6-astra`, `reasoning effort=low`. This is a working alternate execution path, not a claim that the built-in allocation limit was removed.
- Automatic approval rejected the initial CLI request because it would send local AGENTS.md/research-document contents to an external service. The approved replacement supplied only public URLs, disabled project-document injection with `project_doc_max_bytes=0`, ran in `/tmp` with a read-only sandbox, and prohibited local-file/credential access. No rejected export was performed. `--skip-git-repo-check` permits this non-repository working directory; it does not disable the sandbox.
- Retrieval sessions: `01a0f489-7366-71c1-ad41-f5db12068f32` and `01a0f48b-d86a-7283-b475-d504f2592e43`, both exited 0. The second gathered missing regime/issuer passages; neither was an Ultra substitution.
- Twelve compact public-source fact records were sent through the verified Ollama service to the Cloud model. No AGENTS.md, prior report, credentials or private account data were in that prompt. The analysis response returned `model=gemma4:31b`, `done=true`, `done_reason=stop`, `eval_count=799`.

Runtime provenance is stored under ignored `var/research/routing-rerun-20260930/`: public facts, the Cloud request/response, parsed JSON, both retrieval outputs, and a hash manifest. These are research artifacts, not runtime trading inputs. Cloud-response SHA256: `f71ae614269ca1fb3cb4581d2de70a3b5862ea020ad3be6df9df90f26670b170`.

The previous report is preserved byte-for-byte, SHA256 `5e9e603098d0ab9e4f1a15ab34ae739bc919300ac55932d32c04d65964adb881`. Historical attribution to Astra Ultra remains accurate for that earlier run; it is not rewritten to the new model name.

## Comparison with the previous research

“Confirmed” below means corroborated publisher text, not independently authenticated trades or full setup compliance. Source access date is 2026-09-30. Exact recap publication/session dates generally remain unknown.

| Question | Earlier finding | New retrieval and analysis, after coordinator review | Disposition |
| --- | --- | --- | --- |
| Can a reported pullback entry lose? | ASUR/SLS supply losing examples but not all-filter verification. | Re-read [ASUR](https://www.warriortrading.com/too-many-false-breakouts-2500-ross-trade-recap/) and [SLS](https://www.warriortrading.com/trade-the-best-leave-the-rest-ross-recap/); reported losses remain supported. No outcome-based exclusion or complete eligibility proof follows. | Confirmed, same limit. |
| Can Ross abstain before a rise? | BXG was rejected despite a subsequently successful opportunity. | The [BXG passage](https://www.warriortrading.com/another-5k-day-ross-trade-recap/) still describes unclear news, light volume and discomfort, while acknowledging others' red-to-green trade worked. CTRV's rejected add was a position cap, not established cash insufficiency. | Confirmed. |
| Do intended orders equal executions? | C14 separates prepared adds from fills and notes account ambiguity. | [UAVS/CLWT text](https://www.warriortrading.com/big-green-day-today-6378-64/) explicitly switches ticker names in the consolidation/prepared-add discussion. The prepared-versus-executed distinction holds, but its precise ticker attribution is not clean. | **Qualification added:** C14 must flag ticker ambiguity as well as account ambiguity. Do not construct a precise symbol/order ledger. |
| Does the news comparison imply a universal rule? | No-news and offering behavior varies by source context. | [OBLN/AWX](https://www.warriortrading.com/trade-the-best-leave-the-rest-ross-recap/) and [MYND/OBLN](https://www.warriortrading.com/one-trade-522-ross-trade-recap/) still support that distinction. The first OBLN headline is unidentified and belongs to a different recap; it cannot be joined to the May 2019 financing merely by ticker. | Confirmed; cross-session news join explicitly forbidden. |
| Is FRSX a verified losing micro? | No: a HOD-break case, useful for regime boundaries. | [FRSX text](https://www.warriortrading.com/stuck-in-a-cold-streak-1861-ross-trade-recap/) confirms a HOD break and that Ross would take it again. A cold description is not a numerical classifier or proof of causality. | Confirmed. |
| Can paper fills establish execution fidelity? | No; bracket/fractional capabilities also have unresolved limitations. | [Paper](https://docs.alpaca.markets/us/docs/paper-trading), [orders](https://docs.alpaca.markets/us/docs/orders-at-alpaca) and [fractional](https://docs.alpaca.markets/us/docs/fractional-trading) passages remain consistent with the earlier documented constraints/conflict. No account-level validation was performed. | Confirmed, still blocked. |
| Is the no-leverage risk profile now complete? | No source mapping established for $500 or the exact post-reset phase. | The [cash-account guide](https://www.warriortrading.com/can-you-trade-in-a-cash-account/) still does not establish numerical per-trade/daily loss caps specific to the described $2,000 challenge. Its Webull case is not automatically the CMEG post-reset phase. | Still unresolved; this is a bounded search result, not proof that no source exists. |

Issuer recheck: [MYND's release](https://www.globenewswire.com/news-release/2019/05/23/1841454/21222/en/MYnd-Analytics-Announces-2-8-Million-Registered-Direct-Offering.html) again establishes the May 23, 2019 07:30 ET announcement and expected May 28 closing, not feed first-seen. The OBLN SEC URL failed direct retrieval in this run; a search-rendered [issuer exhibit copy](https://ir.vyometx.com/sec-filings/all-sec-filings/content/0001427570-19-000043/obln-pricingprfinal52319.htm) supported its May 23 announcement and expected May 28 closing. That limited access does not newly verify the completed closing or the 8-K's availability time. The earlier session-date triangulation remains an inference.

The paper page also contains reset versus create/delete wording tension. This is an ancillary documentation issue; it does not change the four strategy conclusions or authorize account lifecycle operations.

## Coordinator corrections to the Cloud draft

The raw Gemma result was useful but **not adopted verbatim**:

1. It grouped F12 with retrospective trade recaps. F12 is a general cash-account guide; it is not an execution narrative and cannot establish a trade ledger.
2. Its losses section stated the UAVS sequence too definitely, even though another section acknowledged the ticker conflict. The accepted comparison consistently preserves that attribution uncertainty.
3. It proposed comparing the unidentified OBLN morning headline to F8's “filing date.” F8 supplies an announcement date from an exhibit; the headline belongs to a different recap with no established event identity. Require session/event identification first, and retain separate publication, filing, closing and first-seen fields. Do not assume the two OBLN events are related.

Its supported cautions about non-scalable historical sizing, retrospective evidence, unresolved fractional documentation, and absent news timing were retained after primary-source checks. Suggested next evidence is a research proposal only; missing execution logs are not fabricated and model output cannot close gates.

## Result and limits

The new route reproduced the main evidence conclusions and made one material attribution qualification more explicit. It did not establish a numeric $500 risk policy, recover news first-seen records, define a deterministic hot/cold rule, or validate premarket/partial-entry protection. No strategy parameters or fidelity statuses changed.

This was not a controlled model benchmark: the input packaging and worker scope differ, and the Cloud stage received compact retrieved facts rather than conducting its own web search. Agreement here does not establish that Astra Low plus Gemma is generally equivalent or superior to Ultra, nor prove cost or latency savings. The practical result is that the requested route now works and its output still requires source review.

Validation: both retrieval workers exited 0; remote response completed and parsed into all four requested categories; source-artifact hashes and unchanged baseline/fidelity/version hashes were checked. Repository evidence tests and final document checks are recorded in [Week 1 evidence](week1-evidence.md). No orders, purchases, broker account changes or formal paper evaluation occurred.

## Remaining environment limitations

```text
status: ENV_FAULT
category: built-in-agent-allocation
evidence: Built-in spawn returned agent thread limit reached; no allocation-release tool is exposed. Two approved ephemeral Astra Low CLI workers completed successfully.
affected_scope: Built-in collaboration allocation only.
safe_to_continue: Yes through the verified read-only CLI route, with normal tool approvals and public-only prompts.
recommended_action: Reuse the verified route when authorized; do not claim the built-in quota was reset or repeatedly retry unchanged allocation.
```

```text
status: ENV_FAULT
category: auxiliary-cli-state-and-notification
evidence: CLI emitted state-db lookup fallback warnings and an after_agent legacy_notify missing-file warning. Both source retrievals and final artifact writes completed with exit 0.
affected_scope: Auxiliary session lookup/notification; not the completed source retrieval outputs.
safe_to_continue: Yes for completed evidence review and local verification; do not use notification success as proof of completion.
recommended_action: Preserve the warnings; repair the separate global hook/state issue only after scoped diagnosis. No database edits, hook disabling or global resets were performed for this task.
```

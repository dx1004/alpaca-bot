# Codex App automation status

Status: RESOLVED on 2026-09-28.

The Codex App automation capability became available. Four native heartbeat automations and four persistent same-directory chats were created and verified through the app. Exact chat and automation identifiers are recorded in `docs/codex-app-task-plan.md`. After the formal Project appeared, the now-empty `Alpaca Bot` sidebar section was deleted on 2026-09-29.

The user created the formal local Codex Project `Alpaca-bot` for `/mnt/t/alpaca-bot` after the original registration. On 2026-09-29, `list_projects` returned project ID `d800ee3c-fb78-4be3-9ac7-f0c14dff6e0d`, and `list_threads` showed W1 and W2 attached to that ID. W3 and W4 remain active same-directory scheduled chats, but their Project association was not visible in the current 50-thread listing. Do not infer it from the shared path alone.

The earlier local OS scheduler approach remains removed. No Windows Task Scheduler, cron, systemd or local Codex CLI development task is active. The paper-month automation remains deferred until `W4-07` and `G05` pass.

On 2026-10-06, the existing W2, W3 and W4 native heartbeat prompts were updated in place and the app reported success for each. They now read the user's Alpaca Basic/IEX-only, Webull-deferred and separately labeled hybrid-paper decisions from the repository, while retaining dependency checks, paper-only boundaries and milestone-only notifications. Their recurrence dates, target chats, names and active status were preserved. The W1 recurrence's original date window has passed; this W1 research update is being completed in its existing chat rather than creating a duplicate automation or overlapping scheduled run.

## Schedule timezone fault: correction saved, delivery verification pending

- status: `ENV_FAULT` correction saved on 2026-09-28 local time; delivery at the intended hour is not yet verified.
- category: Codex App recurrence timezone interpretation.
- evidence: W1 heartbeat arrived at `2026-09-29T06:00:15Z`, which is 23:00 September 28 in America/Los_Angeles, although the intended run time was 06:00 local.
- affected_scope: all four W1–W4 recurrence hours.
- safe_to_continue: yes, after correcting the active rules; no broker activity occurred.
- recommended_action taken: changed each native recurrence from 06:00 UTC to 13:00 UTC for the September–October daylight-time development window, preserving prompt, target chat and date bounds. Readback confirms four active rules at 13:00 UTC. Observe the next scheduled run to confirm actual delivery time.

## Registration cleanup fault

An interrupted registration call had already created the four intended chats and automations before its response was visible. A later duplicate registration was detected. The duplicate automations were deleted and their four unused chats were removed from the `Alpaca Bot` section; the duplicate section was deleted. The app's archive operation for those unused chats repeatedly returned `database is locked`, so the chats remain unarchived outside the section. This does not affect the four active schedules or their target chats.

On a later verification in the same session, four additional empty duplicate chats were created during recovery, then successfully archived. Their duplicate sidebar section was deleted. The four original active automations remained intact and were rechecked through the native automation view and saved schedules.

- status: `ENV_FAULT`
- category: Codex App thread archive database lock
- evidence: `set_thread_archived` returned `database is locked` on repeated attempts; native thread listing and automation files still show the original four active registrations.
- affected_scope: cleanup of four unused duplicate chats only.
- safe_to_continue: yes, for the four verified original automations; stop the archive action.
- recommended_action: retry archival only after the app database lock clears; do not edit the app database directly.

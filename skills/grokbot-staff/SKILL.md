---
name: grokbot-staff
description: G.I.M.F. (Grok is my friend). Run a chief-of-staff + specialist team inside Hermes, and hand long jobs to the user's own Grok Bot.
---

# grokbot-staff playbook

## Local staff (always available)
1. For a fuzzy or multi-part goal, call `staff_assign` with `specialist="chief"` first. It returns a plan
   that assigns work items to specialist ids.
2. Run each work item with `staff_assign` (`specialist=<id>`, `task=<item>`, optional `context`).
   For parallel or tool-heavy work, use `delegate_task` instead; when the user has selected the
   `staff` delegation backend (`/delegation staff`), set `specialist` per task.
3. Summarise results for the user and list job ids. `staff_status` shows the roster and recent jobs;
   `staff_status job_id=<id>` shows one answer in full.

Default team: chief, research, design, writing, product, engineering. The user can replace it in
Settings -> Plugins -> grokbot-staff -> Specialists (JSON list).

## Grok Bot handoff (optional)
Use `grokbot_delegate` only when the user wants work done by their Grok Bot (long research,
scheduled routines, their connected apps) and a handoff path is configured.
- If it returns `not_configured`, show the `how_to_fix` text. Do not retry or invent another route:
  Grok Bot has no public API; the only paths are the user's webhook routine and the shared inbox folder.
- Tell the user the job id. Later, `grokbot_collect job_id=<id>` reads `results/<id>.*` from the inbox,
  or accepts `result=<pasted text>`. `grokbot_status` lists pending and done handoffs.
- Never put secrets, passwords or tokens in a handoff task.

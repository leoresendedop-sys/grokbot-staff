# Changelog

## 0.2.0 (2026-11-09)

Harness / Loop / Graph, after [@0xWhitemon](https://x.com/0xWhitemon)'s framework
"Harness, Loop, Graph: The 3 Layers That Decide If Your AI Agent Works".

### Added
- **Task contract.** `staff_assign` takes optional `inputs`, `deliverable`, `constraints` and
  `done_when` (a list of verifiable criteria), either as top-level arguments or as a `contract`
  object. When `done_when` is missing, the chief drafts it first and records where it came from
  (`done_when_source`: `caller`, `chief` or `fallback`).
- **Per-job files** under `plugin-data/grokbot-staff/jobs/<id>/`: `contract.json`,
  `progress.json` (`goal`, `tried`, `learned`, `next`, `never`), `attempt-N.json`, `review.json`
  and `deliverable.md`. They survive a session restart.
- **Verification loop.** A separate verifier call returns structured feedback (`failed`,
  `expected`, `observed`, `scope`, `dead_ends`), and that feedback drives the next attempt.
  The loop stops on a pass or on identical / near-identical output. When attempts run out the
  job is marked `needs_human` with its full history. Verifier replies that can't be parsed fail
  closed, and self-report is never accepted as evidence.
- **Independent reviewer.** It sees only the contract and the deliverable, never the author's
  notes, prompt, history or feedback. Delivery requires both verification and an `approve` verdict.
- `resume_job_id` on `staff_assign` and `/staff resume <id>`: continue a `needs_human` job from its files.
- Settings: `quality_loop` (bool, default true) and `max_attempts` (int, default 3).
- `staff_status` and the Desktop pane show attempts, the latest feedback, the review verdict and a
  *Needs you* list.
- Tests: contract parsing, legacy call, stop on pass, stop on identical output, exhaustion →
  `needs_human`, reviewer isolation, self-report, resume and file-based state.

### Compatibility
- A 0.1 call (`specialist` + `task`, optional `context`) still works. `context` becomes a
  contract input. With `quality_loop: false` you get exactly the 0.1 single-answer behaviour.
- `staff_assign` now requires only `specialist` in its schema. `task` is still required
  unless you pass `resume_job_id`.

## 0.1.2
- G.I.M.F. name, wordmark thumbnail and a generic public default team.

## 0.1.0
- Local chief + specialists via `ctx.llm`, `/staff`, `staff_assign`, `staff_status`, Desktop pane,
  team swap in Settings, and an optional fail-closed Grok Bot bridge.

# grokbot-staff

**A chief-of-staff and a specialist team inside Hermes, plus an optional bridge that hands long jobs to your own Grok Bot.**

Two halves:

1. **Local staff** works with zero configuration. A `chief` breaks work down and routes it to
   specialists (`saas`, `design`, `vision-agents`, `computer-vision`, `x`). Each specialist answers with
   its own system prompt on whatever model you already use in Hermes (`ctx.llm`). Jobs are saved, and a Desktop
   pane shows the team and its recent work. You can replace the team from Settings without touching code.
2. **Grok Bot bridge** is optional and fails closed. Hermes hands a task *to* your own Grok Bot, a cloud
   coworker that keeps working while your laptop sleeps, through a webhook routine you create or a shared
   inbox folder. When the answer is ready, Hermes collects it.

## How it differs

| Project | Direction | What it does |
|---|---|---|
| Hermes `agent-importers` (official) | Grok Bot → Hermes, one time | Copies routines, skills and memory into Hermes |
| [grokbot-hermes-bridge](https://github.com/iamsupersocks/grokbot-hermes-bridge) | Grok Bot → Hermes, live | Lets Grok Bot call Hermes over MCP |
| **grokbot-staff** | **Hermes → Grok Bot, live** + a local team | Hermes gets a staff team and can delegate work to your Grok Bot, then collect the result |

It does not import or migrate anything. It does not call any Grok Bot API either, because Grok Bot has no public REST API.

## Install

```bash
hermes plugins install leoresendedop-sys/grokbot-staff --enable
# or, from a checkout:
ln -s "$PWD" ~/.hermes/plugins/grokbot-staff && hermes plugins enable grokbot-staff
```

On Desktop, turn on the **Grok Bot Staff** pane under Capabilities → Plugins. Like every desktop half, it starts disabled.

## Tools, commands, surfaces

| Name | Kind | Purpose |
|---|---|---|
| `staff_assign` | tool | Run a task with one specialist (`chief` returns a plan) and save the job |
| `staff_status` | tool | Show the roster, recent jobs, or one job in full |
| `grokbot_delegate` | tool | Create a handoff and send it by webhook and/or inbox file |
| `grokbot_collect` | tool | Read `results/<id>.*` from the inbox, or take a pasted result, and mark the job done |
| `grokbot_status` | tool | List pending, done and failed handoffs, and which paths are configured |
| `/staff` | slash | `/staff` · `/staff design hero ideas for a fintech app` · `/staff status <id>` |
| `/grokbot` | slash | `/grokbot <task>` · `/grokbot status` · `/grokbot collect <id> [pasted result]` |
| `staff` delegation backend | `register_delegation_provider` | `/delegation staff` adds a `specialist` parameter to `delegate_task`. A specialist with a `profile` runs as that Hermes profile |
| `grokbot-staff:grokbot-staff` | skill | Team playbook for the agent |
| Desktop pane | `desktop/plugin.js` | Team cards, pending handoffs, recent jobs, and a link to Settings |

## Settings (Settings → Plugins → grokbot-staff)

| Key | Type | Default |
|---|---|---|
| `specialists` | JSON list | empty, which means the built-in team |
| `grokbot_webhook_url` | **secret** (env `GROKBOT_STAFF_WEBHOOK_URL`) | unset |
| `grokbot_inbox_dir` | str | unset |
| `webhook_timeout_seconds` | int | 15 |
| `max_output_tokens` | int | 1200 |

Custom team example:

```json
[{"id": "chief", "title": "Chief of staff", "prompt": "Plan and route."},
 {"id": "legal", "title": "Contracts reviewer", "focus": "SaaS contracts", "prompt": "Review clauses..."},
 {"id": "growth", "title": "Growth", "focus": "acquisition", "prompt": "...", "profile": "growth"}]
```

### Handoff payload

A handoff is sent as a POST to your webhook and written to `<inbox>/handoffs/<id>.json`:

```json
{"id": "gbs-1a2b3c4d5e6f", "created_at": "2026-11-07T15:00:00Z", "specialist": "saas",
 "title": "Pricing teardown", "task": "...", "reply_hint": "..."}
```

Your Grok Bot routine replies by saving `<inbox>/results/<id>.md` (or `.json` with `{"result": "..."}` or `.txt`).
If it can't write files, paste its answer back with `grokbot_collect result=...`.

## Demo script (under 90 s)

1. **0–10 s.** Hermes Desktop with the Staff pane open: "Hermes just got a staff."
2. **10–30 s.** `/staff chief plan a launch of a livestock-monitoring app`. The chief returns a plan that assigns items to saas, design, computer-vision and x.
3. **30–45 s.** `/staff design landing hero ideas with Three.js`. The answer streams in, and the job appears in the pane.
4. **45–65 s.** "Now the heavy research goes to my cloud coworker": `/grokbot deep market scan of cattle CV vendors in Brazil`. Job `gbs-…` shows up under *Pending Grok Bot handoffs*, and Grok Bot's webhook routine fires.
5. **65–80 s.** Grok Bot drops `results/gbs-….md` into the shared folder, then `/grokbot collect gbs-…`. The result is shown and the job is marked done.
6. **80–90 s.** Close on the comparison table: "Not an import. Not Grok Bot calling Hermes. Hermes delegating to Grok Bot."

## Security notes

- **No credentials in the repo.** The webhook URL is a `secret` setting read with `agent.secret_scope.get_secret`. Logs and tool output only ever show `scheme://host/...`.
- **Network.** HTTP requests go only to the webhook URL you set, and only when you call `grokbot_delegate`. The URL must be https (plain http is allowed for localhost only). Redirects are never followed, URLs with user info are rejected, and the timeout is capped at 120 s.
- **Files.** The plugin writes jobs to `<HERMES_HOME>/plugin-data/grokbot-staff/jobs.json`. If you set an inbox folder, it writes `handoffs/` and reads `results/` there. Job ids are validated, so there is no path traversal. Symlinked result files and files over 2 MB are ignored.
- **LLM calls.** These use `ctx.llm` with your current provider and host-owned credentials. They are only made when a staff tool or command runs.
- **Nothing else.** No shell commands, no background processes, no telemetry, no self-updater, no `eval`. The Desktop half imports only `@hermes/plugin-sdk` and `react` and reads its own `/api/plugins/grokbot-staff/overview` route, which is read-only.
- Do not put passwords or tokens in a handoff task. It is sent to your webhook and written to disk as plain text.

## Development

```bash
python3 -m unittest discover -s tests -t . -v      # stdlib only, no network
hermes plugins validate . --install-deps
hermes plugins doctor . --ci
```

## License

MIT

"""Tool schemas (what the model reads)."""

_STR_LIST = {"type": "array", "items": {"type": "string"}}

STAFF_ASSIGN = {
    "name": "staff_assign",
    "description": (
        "Assign a task to one specialist on the local staff team (chief, research, design, "
        "writing, product, engineering, or user-defined). Optionally pass a task contract "
        "(inputs, deliverable, constraints, done_when). If done_when is missing the chief drafts "
        "verifiable criteria first. The specialist's answer is checked by a separate verifier "
        "against done_when (up to max_attempts tries with structured feedback), then by an "
        "independent reviewer, before the job is marked done. If it cannot pass, the job is "
        "marked needs_human with its history instead of claiming success. Use specialist='chief' "
        "to get a plan that splits work across the team. Resume a job with resume_job_id."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "specialist": {"type": "string", "description": "Specialist id, e.g. 'design'."},
            "task": {"type": "string", "description": "What the specialist should do."},
            "title": {"type": "string", "description": "Optional short title for the job."},
            "context": {"type": "string", "description": "Optional extra context or material (added to inputs)."},
            "inputs": {**_STR_LIST, "description": "Optional contract inputs: facts, files, links the work must use."},
            "deliverable": {"type": "string", "description": "Optional: what exactly must be handed back."},
            "constraints": {**_STR_LIST, "description": "Optional constraints (scope, format, what not to touch)."},
            "done_when": {**_STR_LIST, "description": (
                "Optional list of verifiable acceptance criteria. Omit to let the chief draft them.")},
            "contract": {"type": "object", "description": (
                "Optional contract object with the same keys (task, inputs, deliverable, constraints, "
                "done_when). Top-level arguments win.")},
            "resume_job_id": {"type": "string", "description": (
                "Optional: continue a needs_human or interrupted job from its progress file. "
                "New done_when/constraints/context are merged into its contract.")},
        },
        "required": ["specialist"],
    },
}

STAFF_STATUS = {
    "name": "staff_status",
    "description": (
        "Show the staff roster and recent local jobs (status, attempts, review verdict, needs_human), "
        "or one job in full when job_id is given: contract, progress file, every attempt's verifier "
        "feedback, the review verdict and the deliverable."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "job_id": {"type": "string", "description": "Optional job id to show in full."},
            "limit": {"type": "integer", "description": "Max jobs to list (default 10)."},
        },
    },
}

GROKBOT_DELEGATE = {
    "name": "grokbot_delegate",
    "description": (
        "Hand a long-running task to the user's own Grok Bot (cloud coworker) through their "
        "configured webhook routine and/or shared inbox folder. Returns a job id; collect the "
        "answer later with grokbot_collect. Fails with setup instructions if neither path is set."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "task": {"type": "string", "description": "Full task description for Grok Bot."},
            "specialist": {"type": "string", "description": "Optional specialist id to frame the task."},
            "title": {"type": "string", "description": "Optional short title."},
            "reply_hint": {"type": "string",
                           "description": "Optional: how Grok Bot should return the result."},
        },
        "required": ["task"],
    },
}

GROKBOT_COLLECT = {
    "name": "grokbot_collect",
    "description": (
        "Collect the result of a Grok Bot handoff: reads results/<job_id>.json|.md|.txt from the "
        "inbox folder, or accepts a pasted result, and marks the job done."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "job_id": {"type": "string", "description": "Job id returned by grokbot_delegate."},
            "result": {"type": "string", "description": "Optional pasted result text."},
        },
        "required": ["job_id"],
    },
}

GROKBOT_STATUS = {
    "name": "grokbot_status",
    "description": "List Grok Bot handoffs (pending and done) and which handoff paths are configured.",
    "parameters": {
        "type": "object",
        "properties": {
            "status": {"type": "string", "enum": ["pending", "done", "failed", "all"],
                       "description": "Filter (default all)."},
            "limit": {"type": "integer", "description": "Max jobs (default 20)."},
        },
    },
}

"""grokbot-staff: a chief-of-staff + specialists inside Hermes, and an optional
fail-closed bridge that hands work to the user's own Grok Bot."""
from __future__ import annotations

import logging
from pathlib import Path

from . import schemas
from .delegation import make_provider
from .store import JobStore
from .tools import Staff

logger = logging.getLogger(__name__)
PLUGIN_NAME = "grokbot-staff"
TOOLSET = "grokbot_staff"


def _data_dir() -> Path:
    from plugins.plugin_storage import plugin_data_dir
    return plugin_data_dir(PLUGIN_NAME)


class _LazyStore(JobStore):
    """Resolves the data dir on every access so it follows the active profile."""

    def __init__(self):  # noqa: D401
        pass

    @property
    def dir(self):  # type: ignore[override]
        return _data_dir()

    @property
    def path(self):  # type: ignore[override]
        return _data_dir() / "jobs.json"


def _get_secret(name: str):
    from agent.secret_scope import get_secret
    return get_secret(name)


def build_staff(ctx) -> Staff:
    return Staff(
        get_config=lambda key, default=None: ctx.get_config(key, default=default),
        get_secret=_get_secret,
        store=_LazyStore(),
        llm=getattr(ctx, "llm", None),
    )


def register(ctx):
    staff = build_staff(ctx)
    for name, schema, handler in (
        ("staff_assign", schemas.STAFF_ASSIGN, staff.staff_assign),
        ("staff_status", schemas.STAFF_STATUS, staff.staff_status),
        ("grokbot_delegate", schemas.GROKBOT_DELEGATE, staff.grokbot_delegate),
        ("grokbot_collect", schemas.GROKBOT_COLLECT, staff.grokbot_collect),
        ("grokbot_status", schemas.GROKBOT_STATUS, staff.grokbot_status),
    ):
        ctx.register_tool(name=name, toolset=TOOLSET, schema=schema, handler=handler)

    ctx.register_command("staff", staff.slash_staff,
                         description="Staff team: list, assign (/staff design <task>), status, resume",
                         args_hint="[list|status [job_id]|resume <job_id>|<specialist> <task>]")
    ctx.register_command("grokbot", staff.slash_grokbot,
                         description="Hand work to your Grok Bot, or check/collect handoffs",
                         args_hint="[status|collect <job_id> [result]|<task>]")

    skill = Path(__file__).parent / "skills" / "grokbot-staff" / "SKILL.md"
    if skill.exists():
        ctx.register_skill("grokbot-staff", skill)

    if hasattr(ctx, "register_delegation_provider"):
        try:
            ctx.register_delegation_provider(make_provider(staff.roster))
        except Exception:  # optional surface; never block the rest of the plugin
            logger.warning("grokbot-staff: delegation provider not registered", exc_info=True)

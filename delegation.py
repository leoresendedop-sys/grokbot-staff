"""Optional delegation backend 'staff': adds a `specialist` parameter to delegate_task.
Select it with `/delegation staff` (or `hermes config set delegation.backend staff`).
A specialist with a `profile` set runs as that Hermes profile; otherwise the child keeps
the default route and is labelled with the specialist id."""
from __future__ import annotations

from typing import Callable, List, Dict

try:  # public ABC; absent only when running the unit tests without Hermes
    from agent.delegation_provider import DelegationProvider, DelegationRouteError
except ImportError:  # pragma: no cover - exercised in tests
    class DelegationProvider:  # type: ignore[no-redef]
        def settings(self, config):
            return {}

    class DelegationRouteError(Exception):  # type: ignore[no-redef]
        pass


def make_provider(roster_fn: Callable[[], List[Dict[str, str]]]):
    class StaffDelegation(DelegationProvider):
        @property
        def name(self) -> str:
            return "staff"

        def task_parameters(self, config):
            roster = roster_fn()
            return {"specialist": {
                "type": "string",
                "enum": [s["id"] for s in roster],
                "description": "Staff specialist for this task: " +
                               "; ".join(f"{s['id']}: {s['focus'] or s['title']}" for s in roster),
            }}

        def description(self, config):
            return ("Set `specialist` per task (grokbot-staff). Put the specialist's brief in "
                    "`context`; load skill grokbot-staff:grokbot-staff for the team playbook.")

        def is_available(self) -> bool:
            return True

        def route_task(self, task, config):
            sid = task.get("specialist")
            if not sid:
                return dict(config)
            for s in roster_fn():
                if s["id"] == sid:
                    if s.get("profile"):
                        return {"profile": s["profile"], "label": sid}
                    route = dict(config)
                    route["label"] = sid
                    return route
            raise DelegationRouteError(f"Unknown specialist {sid!r}.")

    return StaffDelegation()

"""Grok Bot handoff bridge. Grok Bot has no public REST API, so the only supported
paths are (a) the user's own Grok Bot webhook routine and (b) a shared inbox folder.
Everything fails closed: no configured path means a clear error, never a fake call."""
from __future__ import annotations

import ipaddress
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

_SAFE_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_MAX_RESULT_BYTES = 2_000_000

SETUP_HINT = (
    "No Grok Bot handoff path is configured. In Hermes open Settings -> Plugins -> "
    "grokbot-staff and set either 'Grok Bot webhook URL' (a webhook routine you created "
    "in your own Grok Bot; stored as the GROKBOT_STAFF_WEBHOOK_URL secret) or "
    "'Grok Bot inbox folder' (a shared/synced folder your Grok Bot reads). "
    "CLI: hermes config set plugins.entries.grokbot-staff.settings.grokbot_inbox_dir ~/GrokBotInbox"
)


def redact_url(url: str) -> str:
    """Show scheme://host only. Never log paths or query strings (they carry tokens)."""
    try:
        p = urllib.parse.urlsplit(url)
        return f"{p.scheme}://{p.hostname or '?'}/..." if p.scheme else "(invalid)"
    except ValueError:
        return "(invalid)"


def _is_loopback(host: str) -> bool:
    if host in ("localhost",):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def validate_webhook_url(url: str) -> Optional[str]:
    """Return an error string, or None when the URL is acceptable."""
    try:
        p = urllib.parse.urlsplit(url)
    except ValueError:
        return "webhook URL does not parse"
    if p.username or p.password:
        return "webhook URL must not contain user info"
    if not p.hostname:
        return "webhook URL has no host"
    if p.scheme == "https":
        return None
    if p.scheme == "http" and _is_loopback(p.hostname):
        return None
    return "webhook URL must be https (plain http is allowed only for localhost)"


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Refuse every redirect so the payload can never be re-sent to another host."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D401
        return None


def _opener() -> urllib.request.OpenerDirector:
    return urllib.request.build_opener(_NoRedirect())


def post_webhook(url: str, payload: Dict[str, Any], timeout: float = 15.0,
                 opener: Optional[urllib.request.OpenerDirector] = None) -> Tuple[bool, str]:
    err = validate_webhook_url(url)
    if err:
        return False, err
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=body, method="POST",
        headers={"Content-Type": "application/json", "User-Agent": "grokbot-staff/0.2"},
    )
    try:
        with (opener or _opener()).open(req, timeout=max(1.0, min(float(timeout), 120.0))) as resp:
            code = getattr(resp, "status", None) or resp.getcode()
            if 200 <= code < 300:
                return True, f"HTTP {code}"
            return False, f"webhook answered HTTP {code}"
    except urllib.error.HTTPError as e:
        if 300 <= e.code < 400:
            return False, f"webhook redirected (HTTP {e.code}); redirects are refused"
        return False, f"webhook answered HTTP {e.code}"
    except urllib.error.URLError as e:
        return False, f"webhook unreachable: {getattr(e, 'reason', e)}"
    except (TimeoutError, OSError) as e:
        return False, f"webhook error: {type(e).__name__}"


def write_inbox_handoff(inbox_dir: str, payload: Dict[str, Any]) -> Tuple[bool, str]:
    if not _SAFE_ID.match(str(payload.get("id", ""))):
        return False, "invalid job id"
    try:
        root = Path(os.path.expanduser(inbox_dir)).resolve()
        target = root / "handoffs"
        target.mkdir(parents=True, exist_ok=True)
        (root / "results").mkdir(exist_ok=True)
        f = target / f"{payload['id']}.json"
        tmp = f.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        os.replace(tmp, f)
        return True, str(f)
    except OSError as e:
        return False, f"could not write inbox file: {type(e).__name__}"


def read_inbox_result(inbox_dir: str, job_id: str) -> Optional[str]:
    if not inbox_dir or not _SAFE_ID.match(job_id or ""):
        return None
    root = Path(os.path.expanduser(inbox_dir)).resolve() / "results"
    for ext in (".json", ".md", ".txt"):
        f = root / f"{job_id}{ext}"
        try:
            if not f.is_file() or f.is_symlink() or f.stat().st_size > _MAX_RESULT_BYTES:
                continue
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if ext == ".json":
            try:
                data = json.loads(text)
                if isinstance(data, dict):
                    return str(data.get("result") or data.get("text") or json.dumps(data))
            except ValueError:
                pass
        return text
    return None

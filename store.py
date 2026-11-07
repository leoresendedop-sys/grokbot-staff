"""Durable job store: one JSON file under the plugin's data dir, guarded by a lock."""
from __future__ import annotations

import json
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

_LOCK = threading.Lock()
_MAX_JOBS = 500


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def new_id(prefix: str = "job") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


class JobStore:
    def __init__(self, data_dir: Path):
        self.dir = Path(data_dir)
        self.path = self.dir / "jobs.json"

    def _read(self) -> List[Dict[str, Any]]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return data if isinstance(data, list) else []
        except (OSError, ValueError):
            return []

    def _write(self, jobs: List[Dict[str, Any]]) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(jobs[-_MAX_JOBS:], indent=1), encoding="utf-8")
        os.replace(tmp, self.path)

    def add(self, job: Dict[str, Any]) -> Dict[str, Any]:
        with _LOCK:
            jobs = self._read()
            jobs.append(job)
            self._write(jobs)
        return job

    def update(self, job_id: str, **fields: Any) -> Optional[Dict[str, Any]]:
        with _LOCK:
            jobs = self._read()
            for j in jobs:
                if j.get("id") == job_id:
                    j.update(fields)
                    j["updated_at"] = now_iso()
                    self._write(jobs)
                    return j
        return None

    def get(self, job_id: str) -> Optional[Dict[str, Any]]:
        for j in self._read():
            if j.get("id") == job_id:
                return j
        return None

    def list(self, kind: Optional[str] = None, status: Optional[str] = None,
             limit: int = 20) -> List[Dict[str, Any]]:
        jobs = [j for j in self._read()
                if (kind is None or j.get("kind") == kind)
                and (status is None or j.get("status") == status)]
        return list(reversed(jobs))[: max(1, min(int(limit or 20), 200))]

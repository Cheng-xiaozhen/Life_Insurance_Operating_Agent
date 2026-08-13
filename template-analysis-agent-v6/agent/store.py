from __future__ import annotations

import json
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any
from uuid import uuid4

from .models import AgentEvent, SessionState


class InMemorySessionStore:
    def __init__(self):
        self._sessions: dict[str, SessionState] = {}
        self._lock = Lock()

    def get(self, session_id: str | None) -> SessionState:
        if not session_id:
            return SessionState()
        with self._lock:
            return deepcopy(self._sessions.get(session_id, SessionState()))

    def save(self, session_id: str | None, state: SessionState) -> None:
        if not session_id:
            return
        with self._lock:
            self._sessions[session_id] = deepcopy(state)


class RunStore:
    def __init__(self, runs_dir: str | Path):
        self.runs_dir = Path(runs_dir).resolve()

    def create_run(self) -> tuple[str, Path]:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")[:-3]
        run_id = f"{stamp}-{uuid4().hex[:8]}"
        run_dir = self.runs_dir / run_id
        run_dir.mkdir(parents=True, exist_ok=False)
        return run_id, run_dir

    @staticmethod
    def _json_default(value: Any) -> Any:
        if isinstance(value, Path):
            return str(value)
        raise TypeError(f"cannot serialize {type(value).__name__}")

    def write_json(self, run_dir: Path, name: str, value: Any) -> Path:
        path = run_dir / name
        path.write_text(
            json.dumps(value, ensure_ascii=False, indent=2, default=self._json_default) + "\n",
            encoding="utf-8",
        )
        return path.resolve()

    def write_text(self, run_dir: Path, name: str, value: str) -> Path:
        path = run_dir / name
        path.write_text(value, encoding="utf-8")
        return path.resolve()

    def append_event(self, run_dir: Path, event: AgentEvent) -> None:
        with (run_dir / "events.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(event.to_dict(), ensure_ascii=False) + "\n")

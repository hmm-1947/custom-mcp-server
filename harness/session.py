"""The task session: the state machine every tool call flows through.

This is what turns a set of file-editing tools into an engineering harness. The
session remembers, per task:

  * which files were searched, read (and over which line ranges), and edited
  * the content digest of each file at the moment it was read, so a stale read
    can be detected and a blind edit refused
  * every terminal command and validation run, with exit codes and timings
  * where the task is in the workflow, and what is still outstanding

Tools consult the session to decide whether an action is allowed, and every
response carries the resulting guidance back to the model.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .phases import Phase, advance, goal_of

SESSION_DIR = Path(__file__).resolve().parent.parent / ".sessions"
MAX_HISTORY = 60


def digest(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


@dataclass
class FileRecord:
    path: str
    searched: bool = False
    read_full: bool = False
    read_ranges: list[list[int]] = field(default_factory=list)
    read_digest: str | None = None
    read_at: float | None = None
    edit_count: int = 0
    last_edit_at: float | None = None
    last_edit_kind: str | None = None
    validated_at: float | None = None
    flagged_by_impact: bool = False

    def covers(self, start: int | None, end: int | None) -> bool:
        """True when a prior read already showed the requested line range."""
        if self.read_full:
            return True
        if start is None or end is None:
            return self.read_full
        return any(low <= start and end <= high for low, high in self.read_ranges)

    def summary(self) -> dict:
        data = asdict(self)
        data["read"] = self.read_full or bool(self.read_ranges)
        return data


class TaskSession:
    """One unit of engineering work against one workspace."""

    def __init__(self, workspace: str, root: Path, goal: str):
        self.task_id = uuid.uuid4().hex[:12]
        self.workspace = workspace
        self.root = Path(root)
        self.goal = goal
        self.created_at = time.time()
        self.phase = Phase.ORIENT
        self.files: dict[str, FileRecord] = {}
        self.searches: list[dict] = []
        self.commands: list[dict] = []
        self.validations: list[dict] = []
        self.notes: list[str] = []
        self.blockers: list[dict] = []
        self.repair_attempts = 0
        self.profile: dict | None = None
        self.git_checked_at: float | None = None
        self.last_diagnosis: dict | None = None
        self.impact_candidates: list[str] = []
        self.completed_at: float | None = None
        self.lock = threading.RLock()

    # ---------------------------------------------------------------- ledger

    def record(self, path: str) -> FileRecord:
        record = self.files.get(path)
        if record is None:
            record = FileRecord(path=path)
            self.files[path] = record
        return record

    def record_search(self, kind: str, query: str, hits: int, files: list[str]) -> None:
        with self.lock:
            self.searches.append({
                "kind": kind, "query": query, "hits": hits,
                "files": files[:40], "at": time.time(),
            })
            del self.searches[:-MAX_HISTORY]
            for path in files:
                self.record(path).searched = True
            if self.phase in (Phase.ORIENT, Phase.IDLE):
                self.phase = Phase.EXPLORE

    def record_read(self, path: str, start: int | None, end: int | None, full: bool, absolute: Path) -> None:
        with self.lock:
            record = self.record(path)
            if full:
                record.read_full = True
            elif start is not None and end is not None:
                record.read_ranges.append([start, end])
                record.read_ranges = _merge_ranges(record.read_ranges)
            record.read_digest = digest(absolute)
            record.read_at = time.time()
            if self.phase in (Phase.ORIENT, Phase.EXPLORE, Phase.IDLE):
                self.phase = Phase.READ

    def record_edit(self, path: str, kind: str, absolute: Path, lines: tuple[int, int] | None) -> None:
        with self.lock:
            record = self.record(path)
            record.edit_count += 1
            record.last_edit_at = time.time()
            record.last_edit_kind = kind
            record.read_digest = digest(absolute)
            if lines is not None and not record.read_full:
                record.read_ranges.append([lines[0], lines[1]])
                record.read_ranges = _merge_ranges(record.read_ranges)
            self.phase = Phase.VALIDATE if self.phase != Phase.REPAIR else Phase.VALIDATE

    def record_command(self, result: dict, purpose: str = "") -> None:
        with self.lock:
            entry = {
                "command": result.get("command"), "exit_code": result.get("exit_code"),
                "duration_ms": result.get("duration_ms"), "ok": result.get("ok"),
                "purpose": purpose, "at": time.time(),
            }
            self.commands.append(entry)
            del self.commands[:-MAX_HISTORY]

    def record_validation(self, run: dict) -> None:
        """run: {"passed": bool, "steps": [...], "edited_files": [...], ...}"""
        with self.lock:
            run = dict(run)
            run["at"] = time.time()
            run["attempt"] = len(self.validations) + 1
            self.validations.append(run)
            del self.validations[:-MAX_HISTORY]
            if run.get("passed"):
                stamp = time.time()
                # Only the files this run actually covered count as validated.
                # Stamping merely-read files would let an unproven edit slip
                # through the completion check later.
                for path in run.get("edited_files") or self.edited_files():
                    self.record(path).validated_at = stamp
                # A pass does NOT close the task: only task(action='complete')
                # does, after the completion checks. Staying in VALIDATE keeps
                # the session alive for a deeper run or a follow-up edit.
                self.phase = Phase.VALIDATE
                self.repair_attempts = 0
            else:
                self.phase = Phase.DIAGNOSE
                self.repair_attempts += 1

    def record_diagnosis(self, diagnosis: dict) -> None:
        with self.lock:
            self.last_diagnosis = diagnosis
            if diagnosis.get("classification") == "environment":
                self.phase = Phase.DIAGNOSE
            elif diagnosis.get("classification") == "code":
                self.phase = Phase.REPAIR

    def block(self, reason: str, evidence: list[str] | None = None) -> None:
        with self.lock:
            self.blockers.append({"reason": reason, "evidence": evidence or [], "at": time.time()})
            self.phase = Phase.BLOCKED

    def note(self, text: str) -> None:
        with self.lock:
            self.notes.append(text)
            del self.notes[:-MAX_HISTORY]

    # ------------------------------------------------------------- questions

    def edited_files(self) -> list[str]:
        return sorted(path for path, record in self.files.items() if record.edit_count)

    def read_files(self) -> list[str]:
        return sorted(path for path, record in self.files.items() if record.read_full or record.read_ranges)

    def searched_files(self) -> list[str]:
        return sorted(path for path, record in self.files.items() if record.searched)

    def dirty_files(self) -> list[str]:
        """Files edited since the last time validation passed for them."""
        return sorted(
            path for path, record in self.files.items()
            if record.edit_count and (record.validated_at is None or (record.last_edit_at or 0) > record.validated_at)
        )

    def stale_reads(self) -> list[dict]:
        """Files whose on-disk content changed after we read them."""
        stale: list[dict] = []
        for path, record in self.files.items():
            if record.read_digest is None:
                continue
            current = digest(self.root / path)
            if current is not None and current != record.read_digest:
                stale.append({"path": path, "read_at": record.read_at})
        return stale

    def last_validation(self) -> dict | None:
        return self.validations[-1] if self.validations else None

    def explored(self) -> bool:
        return bool(self.searches) or any(entry.get("purpose") == "explore" for entry in self.commands)

    def ledger(self) -> dict:
        return {
            "task_id": self.task_id,
            "workspace": self.workspace,
            "goal": self.goal,
            "phase": self.phase.value,
            "phase_goal": goal_of(self.phase),
            "searches": len(self.searches),
            "commands_run": len(self.commands),
            "validation_attempts": len(self.validations),
            "repair_attempts": self.repair_attempts,
            "files_searched": self.searched_files(),
            "files_read": self.read_files(),
            "files_edited": self.edited_files(),
            "files_awaiting_validation": self.dirty_files(),
            "impact_candidates_unvisited": [
                path for path in self.impact_candidates
                if not (self.files.get(path) and (self.files[path].read_full or self.files[path].read_ranges))
            ],
            "blockers": self.blockers,
            "last_validation_passed": bool(self.last_validation() and self.last_validation()["passed"]),
        }

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "workspace": self.workspace,
            "root": str(self.root),
            "goal": self.goal,
            "created_at": self.created_at,
            "phase": self.phase.value,
            "files": {path: record.summary() for path, record in self.files.items()},
            "searches": self.searches[-20:],
            "commands": self.commands[-20:],
            "validations": self.validations[-10:],
            "notes": self.notes,
            "blockers": self.blockers,
            "repair_attempts": self.repair_attempts,
            "profile": self.profile,
            "completed_at": self.completed_at,
        }

    def advance_phase(self) -> Phase:
        with self.lock:
            self.phase = advance(self.phase)
            return self.phase

    def persist(self) -> None:
        try:
            SESSION_DIR.mkdir(parents=True, exist_ok=True)
            slug = re.sub(r"[^A-Za-z0-9_.-]", "_", self.workspace) or "workspace"
            (SESSION_DIR / f"{slug}.json").write_text(
                json.dumps(self.to_dict(), indent=2, default=str), encoding="utf-8",
            )
        except OSError:
            pass  # persistence is a convenience, never a failure mode


class SessionStore:
    """One active task per workspace."""

    def __init__(self) -> None:
        self._sessions: dict[str, TaskSession] = {}
        self._lock = threading.RLock()

    def start(self, workspace: str, root: Path, goal: str) -> TaskSession:
        with self._lock:
            session = TaskSession(workspace, root, goal)
            self._sessions[workspace] = session
            return session

    def get(self, workspace: str) -> TaskSession | None:
        return self._sessions.get(workspace)

    def require(self, workspace: str, root: Path) -> TaskSession:
        """Return the active session, auto-opening one so no tool hard-fails.

        An implicit session is marked in its goal so the guidance can tell the
        model to state a real goal. A completed session is kept, not replaced -
        discarding it would erase the read/edit ledger that later tool calls and
        the completion checks depend on. Only task(action='start') or
        task(action='abandon') replaces a session.
        """
        with self._lock:
            session = self._sessions.get(workspace)
            if session is None:
                session = self.start(workspace, root, "(implicit - no goal declared)")
            return session

    def drop(self, workspace: str) -> None:
        with self._lock:
            self._sessions.pop(workspace, None)

    def all(self) -> dict[str, TaskSession]:
        return dict(self._sessions)


def _merge_ranges(ranges: list[list[int]]) -> list[list[int]]:
    if not ranges:
        return []
    ordered = sorted((list(pair) for pair in ranges), key=lambda pair: (pair[0], pair[1]))
    merged = [ordered[0]]
    for start, end in ordered[1:]:
        if start <= merged[-1][1] + 1:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return merged


#: Process-wide store. The MCP server is long-lived, so this is the task memory
#: that lets the harness reason across tool calls instead of statelessly.
SESSIONS = SessionStore()

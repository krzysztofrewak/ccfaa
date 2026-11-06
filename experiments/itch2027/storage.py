"""Where the itch2027 experiments keep their outputs: CSV tables, resumable run logs and run metadata."""

from __future__ import annotations

import csv
import importlib.metadata
import json
import os
import platform
import subprocess
from collections.abc import Iterable, Sequence
from datetime import datetime, timezone
from pathlib import Path

import brewery_schedule_digital_twin

EXPERIMENT_DIR = Path(__file__).resolve().parent
RESULTS_DIR = EXPERIMENT_DIR / "results"
REFERENCE_DIR = EXPERIMENT_DIR / "reference_results"


def write_csv(path: Path, rows: Sequence[dict]) -> None:
    """Writes ``rows`` with the columns of all of them, in the order they first appear."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(dict.fromkeys(key for row in rows for key in row))
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    print(f"{len(rows)} rows -> {path}")


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="") as f:
        return list(csv.DictReader(f))


class RunLog:
    """A directory of runs, one JSON line per run in one file per scenario, written as each run ends.

    A run is on disk once ``append`` returns, so an interrupted experiment
    resumes without repeating finished runs. A line cut short by the
    interruption is dropped when the log is read, so its run is made again.
    """

    def __init__(self, directory: Path) -> None:
        self.directory = directory

    def append(self, scenario_id: str, run: dict) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        with (self.directory / f"{scenario_id}.jsonl").open("a") as f:
            f.write(json.dumps(run) + "\n")
            f.flush()
            os.fsync(f.fileno())

    def read(self) -> list[dict]:
        runs: list[dict] = []
        for path in sorted(self.directory.glob("*.jsonl")):
            lines = path.read_text().splitlines()
            parsed = []
            for line in lines:
                try:
                    parsed.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
            if len(parsed) != len(lines):
                path.write_text("".join(json.dumps(run) + "\n" for run in parsed))
            runs += parsed
        return runs


class Metadata:
    """``metadata.json`` of a results directory: the protocol of the experiment and every session that ran it."""

    def __init__(self, results_dir: Path) -> None:
        self.path = results_dir / "metadata.json"

    def start_session(self, protocol: dict, workers: int, done: int, pending: int) -> str:
        """Records the protocol and a new session; the session's start, which ``finish_session`` takes."""
        metadata = self._read()
        metadata.update(protocol)
        started = datetime.now(timezone.utc).isoformat(timespec="seconds")
        session = {
            "started_at": started,
            "workers": workers,
            "runs_done_before": done,
            "runs_pending": pending,
            "hardware": hardware(),
            "versions": versions(),
        }
        metadata.setdefault("sessions", []).append(session)
        self._write(metadata)
        return started

    def finish_session(self, started: str, made: int) -> None:
        metadata = self._read()
        for session in metadata["sessions"]:
            if session["started_at"] == started:
                session.update(finished_at=datetime.now(timezone.utc).isoformat(timespec="seconds"), runs_made=made)
        self._write(metadata)

    def _read(self) -> dict:
        return json.loads(self.path.read_text()) if self.path.exists() else {}

    def _write(self, metadata: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(metadata, indent=2, default=str) + "\n")


def hardware() -> dict:
    cpu = next(
        (line.split(":", 1)[1].strip() for line in _lines("/proc/cpuinfo") if line.startswith("model name")),
        platform.processor(),
    )
    memory = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") if hasattr(os, "sysconf") else None
    return {
        "cpu": cpu,
        "logical_cpus": os.cpu_count(),
        "memory_gib": None if memory is None else round(memory / 2**30, 1),
        "platform": platform.platform(),
        "node": platform.node(),
    }


def versions() -> dict:
    return {
        "python": platform.python_version(),
        "ortools": importlib.metadata.version("ortools"),
        "brewery_schedule_digital_twin": brewery_schedule_digital_twin.__version__,
        "git_commit": _git("rev-parse", "HEAD"),
        "git_uncommitted_changes": bool(_git("status", "--porcelain")),
    }


def _git(*args: str) -> str:
    try:
        return subprocess.run(["git", *args], capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return ""


def _lines(path: str) -> Iterable[str]:
    try:
        return Path(path).read_text().splitlines()
    except OSError:
        return []

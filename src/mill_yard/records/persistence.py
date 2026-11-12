"""Persist versioned run metadata, summary, and JSON Lines event records."""

import json
import platform
import shutil
import subprocess
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import simpy

from mill_yard import __version__
from mill_yard.evaluations.metrics import summary as summary
from mill_yard.records.events import SCHEMA_VERSION


def create_run_directory(directory="runs") -> Path:
    """Reserve a unique timestamped directory before execution starts."""
    now = datetime.now(UTC)
    run_id = f"{now:%Y%m%dT%H%M%SZ}_{uuid4().hex[:12]}"
    path = Path(directory) / run_id
    path.mkdir(parents=True, exist_ok=False)
    return path


def save_run(result, directory="runs", *, run_path=None) -> Path:
    """Write metadata and metrics, preserving an already streamed event file."""
    now = datetime.now(UTC)
    path = Path(run_path) if run_path is not None else create_run_directory(directory)
    path.mkdir(parents=True, exist_ok=True)
    source_root = Path(__file__).resolve().parents[3]
    try:
        revision = subprocess.run(
            ["git", "-C", str(source_root), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
            timeout=3,
        ).stdout.strip()
        dirty = bool(
            subprocess.run(
                ["git", "-C", str(source_root), "status", "--porcelain"],
                capture_output=True,
                text=True,
                check=True,
                timeout=3,
            ).stdout.strip()
        )
    except (OSError, subprocess.SubprocessError):
        revision = None
        dirty = None
    scenario_data = asdict(result.scenario)
    sources = scenario_data.pop("configuration_sources", [])
    metadata = {
        "schema_version": SCHEMA_VERSION,
        "run_id": path.name,
        "created_utc": now.isoformat(),
        "package_version": __version__,
        "python": platform.python_version(),
        "simpy": simpy.__version__,
        "git_revision": revision,
        "git_dirty": dirty,
        "units": {"time": "minutes", "distance": "metres", "material": "tonnes"},
        "policy": result.policy,
        "policy_parameters": result.policy_parameters,
        "seed": result.seed,
        "recording_level": result.recording_level,
        "scenario": scenario_data,
        "configuration_sources": sources,
        "run_status": "complete" if result.state.time >= result.scenario.horizon else "partial",
    }
    (path / "metadata.json").write_text(
        json.dumps(metadata, indent=2, allow_nan=False), encoding="utf-8"
    )
    (path / "summary.json").write_text(
        json.dumps(summary(result), indent=2, allow_nan=False), encoding="utf-8"
    )
    target = path / "events.jsonl"
    if result.events_path is not None:
        source = Path(result.events_path)
        if source.resolve() != target.resolve():
            shutil.copyfile(source, target)
    else:
        with target.open("w", encoding="utf-8") as stream:
            for event in result.events:
                stream.write(json.dumps(event, allow_nan=False) + "\n")
    return path

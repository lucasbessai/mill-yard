"""Persist versioned run metadata, summary, and JSON Lines event records."""

import json
import platform
import subprocess
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from mill_yard import __version__


def summary(result):
    """Build machine-readable metrics, including unfinished work."""
    state = result.state
    loaders = {}
    for key, loader in state.loaders.items():
        data = asdict(loader)
        if loader.travel_started is not None and loader.phase_end is not None:
            duration = loader.phase_end - loader.travel_started
            fraction = (
                min(1.0, max(0.0, (state.time - loader.travel_started) / duration))
                if duration
                else 1.0
            )
            data["distance"] += loader.travel_distance * fraction
            data["travel_time"] += duration * fraction
        loaders[key] = data
    unfinished = []
    for truck in state.trucks.values():
        data = asdict(truck)
        handling = truck.handling_time + (
            state.time - truck.busy_since if truck.busy_since is not None else 0
        )
        data["time_in_yard"] = state.time - truck.arrival
        data["waiting_time_so_far"] = state.time - truck.arrival - handling
        unfinished.append(data)
    return {
        "time": state.time,
        "completed_trucks": result.completed_trucks,
        "unfinished_trucks": unfinished,
        "completed_truck_wait_total": sum(t["waiting_time"] for t in result.completed_trucks),
        "unfinished_truck_wait_so_far": sum(t["waiting_time_so_far"] for t in unfinished),
        "loader_travel_distance_total": sum(x["distance"] for x in loaders.values()),
        "loader_travel_time_total": sum(x["travel_time"] for x in loaders.values()),
        "queue": state.queue,
        "consumed": state.consumed,
        "starvation": state.starvation,
        "loaders": loaders,
        "piles": {key: asdict(value) for key, value in state.piles.items()},
        "infeeds": {key: asdict(value) for key, value in state.infeeds.items()},
    }


def save_run(result, directory="runs") -> Path:
    """Save a run in a uniquely named timestamped directory."""
    now = datetime.now(UTC)
    run_id = f"{now:%Y%m%dT%H%M%SZ}_{uuid4().hex[:12]}"
    path = Path(directory) / run_id
    path.mkdir(parents=True, exist_ok=False)
    try:
        revision = subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True, timeout=3
        ).stdout.strip()
        dirty = bool(
            subprocess.run(
                ["git", "status", "--porcelain"],
                capture_output=True,
                text=True,
                check=True,
                timeout=3,
            ).stdout.strip()
        )
    except (OSError, subprocess.SubprocessError):
        revision = None
        dirty = None
    metadata = {
        "schema_version": 1,
        "run_id": run_id,
        "created_utc": now.isoformat(),
        "package_version": __version__,
        "python": platform.python_version(),
        "git_revision": revision,
        "git_dirty": dirty,
        "units": {"time": "minutes", "distance": "metres", "material": "tonnes"},
        "policy": result.policy,
        "policy_parameters": result.policy_parameters,
        "seed": result.seed,
        "recording_level": result.recording_level,
        "scenario": asdict(result.scenario),
    }
    (path / "metadata.json").write_text(
        json.dumps(metadata, indent=2, allow_nan=False), encoding="utf-8"
    )
    (path / "summary.json").write_text(
        json.dumps(summary(result), indent=2, allow_nan=False), encoding="utf-8"
    )
    with (path / "events.jsonl").open("w", encoding="utf-8") as stream:
        for event in result.events:
            stream.write(json.dumps(event, allow_nan=False) + "\n")
    return path

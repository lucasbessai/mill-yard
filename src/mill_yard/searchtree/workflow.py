"""End-to-end random-path calibration and OMADS search workflow."""

import json
from dataclasses import dataclass
from pathlib import Path

from mill_yard.model.yard import YardScenario
from mill_yard.searchtree.calibration import RandomPathCalibration, calibrate_random_paths
from mill_yard.searchtree.objective import ObjectiveWeights
from mill_yard.searchtree.optimizer import run_omads


@dataclass(frozen=True)
class SearchWorkflowResult:
    """Calibration data and optimizer output from one search workflow."""

    calibration: RandomPathCalibration
    optimizer_result: dict
    calibration_path: Path


def run_search_workflow(
    scenario: YardScenario,
    *,
    decision_length: int = 10,
    samples: int = 100,
    budget: int = 20,
    seed: int = 0,
    weights: ObjectiveWeights | None = None,
    post_dir: str | Path = "runs/omads",
) -> SearchWorkflowResult:
    """Calibrate random paths, then optimize using their component statistics.

    ``samples`` controls the number of full-horizon random simulations.
    ``decision_length`` controls the number of action-code coordinates in each
    random vector and the OMADS design vector. ``budget`` is OMADS's black-box
    evaluation budget. The seed makes both calibration and optimizer runs
    reproducible.
    """
    output_dir = Path(post_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    calibration = calibrate_random_paths(
        scenario,
        samples=samples,
        decision_length=decision_length,
        seed=seed,
    )
    calibration_path = output_dir / "random_path_calibration.json"
    calibration_path.write_text(
        json.dumps(calibration.to_dict(), indent=2) + "\n",
        encoding="utf-8",
    )

    optimizer_result = run_omads(
        scenario,
        decision_length=decision_length,
        budget=budget,
        seed=seed,
        weights=weights,
        normalization=calibration.statistics,
        post_dir=output_dir,
    )
    return SearchWorkflowResult(
        calibration=calibration,
        optimizer_result=optimizer_result,
        calibration_path=calibration_path,
    )


__all__ = ["SearchWorkflowResult", "run_search_workflow"]

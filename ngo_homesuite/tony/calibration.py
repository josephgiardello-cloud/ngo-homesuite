import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.isotonic import IsotonicRegression
from sklearn.metrics import brier_score_loss, roc_auc_score


REQUIRED_COLUMNS = {"risk_probability", "outcome"}


def _validate_frame(frame: pd.DataFrame) -> None:
    missing = REQUIRED_COLUMNS - set(frame.columns)
    if missing:
        raise ValueError(f"Calibration CSV missing required columns: {', '.join(sorted(missing))}")

    if frame.empty:
        raise ValueError("Calibration CSV has no rows.")


def _safe_auc(outcomes: np.ndarray, probs: np.ndarray) -> float | None:
    unique = np.unique(outcomes)
    if len(unique) < 2:
        return None
    return float(roc_auc_score(outcomes, probs))


def calibrate_probabilities(frame: pd.DataFrame, bins: int = 10) -> dict[str, Any]:
    _validate_frame(frame)
    work = frame.copy()
    work["risk_probability"] = pd.to_numeric(work["risk_probability"], errors="coerce")
    work["outcome"] = pd.to_numeric(work["outcome"], errors="coerce")
    work = work.dropna(subset=["risk_probability", "outcome"])

    if work.empty:
        raise ValueError("Calibration CSV has no valid numeric rows after cleaning.")

    probs = work["risk_probability"].astype(float).clip(0.0, 1.0).to_numpy()
    outcomes = work["outcome"].astype(int).clip(0, 1).to_numpy()

    model = IsotonicRegression(out_of_bounds="clip")
    calibrated = model.fit_transform(probs, outcomes)

    brier_before = float(brier_score_loss(outcomes, probs))
    brier_after = float(brier_score_loss(outcomes, calibrated))

    auc_before = _safe_auc(outcomes, probs)
    auc_after = _safe_auc(outcomes, calibrated)

    calibration_frame = pd.DataFrame(
        {
            "raw_probability": probs,
            "calibrated_probability": calibrated,
            "outcome": outcomes,
        }
    )

    calibration_frame["bin"] = pd.cut(calibration_frame["raw_probability"], bins=bins, include_lowest=True)
    curve = (
        calibration_frame.groupby("bin", observed=False)
        .agg(
            mean_pred=("raw_probability", "mean"),
            observed_rate=("outcome", "mean"),
            calibrated_mean=("calibrated_probability", "mean"),
            n=("outcome", "size"),
        )
        .dropna(subset=["mean_pred"])
        .reset_index(drop=True)
    )

    return {
        "metrics": {
            "rows": int(len(calibration_frame)),
            "brier_before": round(brier_before, 5),
            "brier_after": round(brier_after, 5),
            "auc_before": round(auc_before, 5) if auc_before is not None else None,
            "auc_after": round(auc_after, 5) if auc_after is not None else None,
        },
        "curve": curve.round(6).to_dict(orient="records"),
        "samples": calibration_frame.drop(columns=["bin"]).round(6).to_dict(orient="records"),
    }


def run(input_file: str, out_file: str, bins: int = 10) -> dict[str, Any]:
    frame = pd.read_csv(input_file)
    result = calibrate_probabilities(frame, bins=bins)
    Path(out_file).parent.mkdir(parents=True, exist_ok=True)
    Path(out_file).write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result

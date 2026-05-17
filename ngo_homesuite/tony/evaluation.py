import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.isotonic import IsotonicRegression
from sklearn.metrics import brier_score_loss, log_loss, roc_auc_score
from sklearn.model_selection import KFold, StratifiedKFold, train_test_split

from .calibration import calibrate_probabilities

REQUIRED_COLUMNS = {"risk_probability", "outcome"}


def _validate_frame(frame: pd.DataFrame) -> None:
    missing = REQUIRED_COLUMNS - set(frame.columns)
    if missing:
        raise ValueError(f"Evaluation CSV missing required columns: {', '.join(sorted(missing))}")
    if frame.empty:
        raise ValueError("Evaluation CSV has no rows.")


def _safe_auc(outcomes: np.ndarray, probs: np.ndarray) -> float | None:
    unique = np.unique(outcomes)
    if len(unique) < 2:
        return None
    return float(roc_auc_score(outcomes, probs))


def _binary_accuracy(outcomes: np.ndarray, probs: np.ndarray, threshold: float) -> float:
    predicted = (probs >= threshold).astype(int)
    return float((predicted == outcomes).mean())


def _ece(outcomes: np.ndarray, probs: np.ndarray, bins: int) -> float:
    clipped = np.clip(probs, 0.0, 1.0)
    edges = np.linspace(0.0, 1.0, bins + 1)
    total = len(clipped)
    if total == 0:
        return 0.0

    err = 0.0
    for idx in range(bins):
        left = edges[idx]
        right = edges[idx + 1]
        if idx == bins - 1:
            mask = (clipped >= left) & (clipped <= right)
        else:
            mask = (clipped >= left) & (clipped < right)
        count = int(mask.sum())
        if count == 0:
            continue
        avg_prob = float(clipped[mask].mean())
        avg_outcome = float(outcomes[mask].mean())
        err += (count / total) * abs(avg_prob - avg_outcome)
    return float(err)


def _metrics_bundle(outcomes: np.ndarray, probs: np.ndarray, bins: int) -> dict[str, float | None]:
    brier = float(brier_score_loss(outcomes, probs))
    auc = _safe_auc(outcomes, probs)
    ll = float(log_loss(outcomes, np.clip(probs, 1e-6, 1 - 1e-6)))
    return {
        "brier": round(brier, 5),
        "auc": round(auc, 5) if auc is not None else None,
        "log_loss": round(ll, 5),
        "ece": round(_ece(outcomes, probs, bins=bins), 5),
        "accuracy_at_0_5": round(_binary_accuracy(outcomes, probs, threshold=0.5), 5),
    }


def _safe_mean(values: list[float | None]) -> float | None:
    valid = [float(v) for v in values if v is not None]
    if not valid:
        return None
    return float(np.mean(valid))


def _aggregate_metrics(metric_rows: list[dict[str, float | None]]) -> dict[str, float | None]:
    if not metric_rows:
        return {
            "brier": None,
            "auc": None,
            "log_loss": None,
            "ece": None,
            "accuracy_at_0_5": None,
        }

    return {
        "brier": round(float(_safe_mean([row.get("brier") for row in metric_rows]) or 0.0), 5),
        "auc": (
            round(float(_safe_mean([row.get("auc") for row in metric_rows]) or 0.0), 5)
            if _safe_mean([row.get("auc") for row in metric_rows]) is not None
            else None
        ),
        "log_loss": round(float(_safe_mean([row.get("log_loss") for row in metric_rows]) or 0.0), 5),
        "ece": round(float(_safe_mean([row.get("ece") for row in metric_rows]) or 0.0), 5),
        "accuracy_at_0_5": round(float(_safe_mean([row.get("accuracy_at_0_5") for row in metric_rows]) or 0.0), 5),
    }


def _fit_calibrator(train_probs: np.ndarray, train_outcomes: np.ndarray) -> IsotonicRegression | None:
    if len(np.unique(train_outcomes)) < 2:
        return None
    calibrator = IsotonicRegression(out_of_bounds="clip")
    calibrator.fit(train_probs, train_outcomes)
    return calibrator


def _apply_out_of_sample_calibration(
    train_probs: np.ndarray,
    train_outcomes: np.ndarray,
    test_probs: np.ndarray,
) -> np.ndarray:
    calibrator = _fit_calibrator(train_probs, train_outcomes)
    if calibrator is None:
        return test_probs
    return calibrator.transform(test_probs)


def _train_test_indices(
    outcomes: np.ndarray,
    test_size: float,
    random_state: int,
    stratified: bool,
) -> tuple[np.ndarray, np.ndarray]:
    indices = np.arange(len(outcomes))
    stratify = outcomes if stratified and len(np.unique(outcomes)) >= 2 else None
    train_idx, test_idx = train_test_split(
        indices,
        test_size=test_size,
        random_state=random_state,
        stratify=stratify,
    )
    return np.array(train_idx), np.array(test_idx)


def _evaluate_holdout(
    probs: np.ndarray,
    outcomes: np.ndarray,
    bins: int,
    test_size: float,
    random_state: int,
    stratified: bool,
) -> dict[str, Any]:
    train_idx, test_idx = _train_test_indices(outcomes, test_size=test_size, random_state=random_state, stratified=stratified)

    train_probs = probs[train_idx]
    train_outcomes = outcomes[train_idx]
    test_probs = probs[test_idx]
    test_outcomes = outcomes[test_idx]

    in_sample_raw = _metrics_bundle(train_outcomes, train_probs, bins=bins)
    in_sample_cal = _metrics_bundle(
        train_outcomes,
        _apply_out_of_sample_calibration(train_probs, train_outcomes, train_probs),
        bins=bins,
    )

    out_sample_raw = _metrics_bundle(test_outcomes, test_probs, bins=bins)
    out_sample_cal = _metrics_bundle(
        test_outcomes,
        _apply_out_of_sample_calibration(train_probs, train_outcomes, test_probs),
        bins=bins,
    )

    return {
        "method": "holdout",
        "test_size": round(float(test_size), 4),
        "random_state": int(random_state),
        "stratified": bool(stratified),
        "train_rows": int(len(train_idx)),
        "test_rows": int(len(test_idx)),
        "in_sample": {
            "raw": in_sample_raw,
            "calibrated": in_sample_cal,
        },
        "out_of_sample": {
            "raw": out_sample_raw,
            "calibrated": out_sample_cal,
        },
    }


def _evaluate_kfold(
    probs: np.ndarray,
    outcomes: np.ndarray,
    bins: int,
    folds: int,
    random_state: int,
    stratified: bool,
) -> dict[str, Any]:
    splits = min(max(int(folds), 2), len(outcomes))
    if stratified and len(np.unique(outcomes)) >= 2:
        splitter = StratifiedKFold(n_splits=splits, shuffle=True, random_state=random_state)
        iterator = splitter.split(probs, outcomes)
    else:
        splitter = KFold(n_splits=splits, shuffle=True, random_state=random_state)
        iterator = splitter.split(probs)

    fold_rows: list[dict[str, Any]] = []
    out_raw_rows: list[dict[str, float | None]] = []
    out_cal_rows: list[dict[str, float | None]] = []
    in_raw_rows: list[dict[str, float | None]] = []
    in_cal_rows: list[dict[str, float | None]] = []

    for fold_id, (train_idx, test_idx) in enumerate(iterator, start=1):
        train_probs = probs[train_idx]
        train_outcomes = outcomes[train_idx]
        test_probs = probs[test_idx]
        test_outcomes = outcomes[test_idx]

        in_raw = _metrics_bundle(train_outcomes, train_probs, bins=bins)
        in_cal = _metrics_bundle(
            train_outcomes,
            _apply_out_of_sample_calibration(train_probs, train_outcomes, train_probs),
            bins=bins,
        )
        out_raw = _metrics_bundle(test_outcomes, test_probs, bins=bins)
        out_cal = _metrics_bundle(
            test_outcomes,
            _apply_out_of_sample_calibration(train_probs, train_outcomes, test_probs),
            bins=bins,
        )

        fold_rows.append(
            {
                "fold": fold_id,
                "train_rows": int(len(train_idx)),
                "test_rows": int(len(test_idx)),
                "in_sample": {"raw": in_raw, "calibrated": in_cal},
                "out_of_sample": {"raw": out_raw, "calibrated": out_cal},
            }
        )

        in_raw_rows.append(in_raw)
        in_cal_rows.append(in_cal)
        out_raw_rows.append(out_raw)
        out_cal_rows.append(out_cal)

    return {
        "method": "kfold",
        "folds": int(splits),
        "random_state": int(random_state),
        "stratified": bool(stratified),
        "in_sample": {
            "raw": _aggregate_metrics(in_raw_rows),
            "calibrated": _aggregate_metrics(in_cal_rows),
        },
        "out_of_sample": {
            "raw": _aggregate_metrics(out_raw_rows),
            "calibrated": _aggregate_metrics(out_cal_rows),
        },
        "fold_details": fold_rows,
    }


def evaluate_probabilities(
    frame: pd.DataFrame,
    bins: int = 10,
    method: str = "holdout",
    test_size: float = 0.3,
    folds: int = 5,
    random_state: int = 42,
    stratified: bool = True,
) -> dict[str, Any]:
    _validate_frame(frame)

    work = frame.copy()
    work["risk_probability"] = pd.to_numeric(work["risk_probability"], errors="coerce")
    work["outcome"] = pd.to_numeric(work["outcome"], errors="coerce")
    work = work.dropna(subset=["risk_probability", "outcome"]).copy()
    if work.empty:
        raise ValueError("Evaluation CSV has no valid numeric rows after cleaning.")

    probs = work["risk_probability"].astype(float).clip(0.0, 1.0).to_numpy()
    outcomes = work["outcome"].astype(int).clip(0, 1).to_numpy()
    prevalence = float(outcomes.mean()) if len(outcomes) else 0.0

    no_skill = np.full(shape=len(outcomes), fill_value=prevalence, dtype=float)

    raw_metrics = _metrics_bundle(outcomes, probs, bins=bins)
    no_skill_metrics = _metrics_bundle(outcomes, no_skill, bins=bins)

    calibration_result = calibrate_probabilities(
        pd.DataFrame({"risk_probability": probs, "outcome": outcomes}), bins=bins
    )
    calibrated_probs = np.array(
        [float(sample["calibrated_probability"]) for sample in calibration_result["samples"]], dtype=float
    )
    calibrated_metrics = _metrics_bundle(outcomes, calibrated_probs, bins=bins)

    brier_raw = float(raw_metrics["brier"])
    brier_calibrated = float(calibrated_metrics["brier"])
    brier_noskill = float(no_skill_metrics["brier"])

    skill_raw = None
    skill_calibrated = None
    if brier_noskill > 1e-9:
        skill_raw = 1.0 - (brier_raw / brier_noskill)
        skill_calibrated = 1.0 - (brier_calibrated / brier_noskill)

    summary = {
        "rows": int(len(outcomes)),
        "positive_rate": round(prevalence, 5),
        "raw": raw_metrics,
        "calibrated": calibrated_metrics,
        "no_skill": no_skill_metrics,
        "brier_skill_vs_no_skill_raw": round(skill_raw, 5) if skill_raw is not None else None,
        "brier_skill_vs_no_skill_calibrated": round(skill_calibrated, 5) if skill_calibrated is not None else None,
    }

    selected_method = str(method).strip().lower()
    if selected_method not in {"holdout", "kfold"}:
        selected_method = "holdout"

    out_of_sample = (
        _evaluate_kfold(
            probs=probs,
            outcomes=outcomes,
            bins=bins,
            folds=folds,
            random_state=random_state,
            stratified=stratified,
        )
        if selected_method == "kfold"
        else _evaluate_holdout(
            probs=probs,
            outcomes=outcomes,
            bins=bins,
            test_size=float(min(max(test_size, 0.05), 0.8)),
            random_state=random_state,
            stratified=stratified,
        )
    )

    field_comparison = {
        "dataset_scope": "external field benchmark",
        "calibration_delta_brier": round(brier_raw - brier_calibrated, 5),
        "accuracy_delta_vs_no_skill": round(
            float(raw_metrics["accuracy_at_0_5"]) - float(no_skill_metrics["accuracy_at_0_5"]), 5
        ),
        "auc_delta_vs_no_skill": (
            round(float(raw_metrics["auc"]) - float(no_skill_metrics["auc"]), 5)
            if raw_metrics["auc"] is not None and no_skill_metrics["auc"] is not None
            else None
        ),
    }

    return {
        "summary": summary,
        "validation": out_of_sample,
        "field_comparison": field_comparison,
        "calibration_curve": calibration_result["curve"],
    }


def run(
    input_file: str,
    out_file: str,
    bins: int = 10,
    method: str = "holdout",
    test_size: float = 0.3,
    folds: int = 5,
    random_state: int = 42,
    stratified: bool = True,
) -> dict[str, Any]:
    frame = pd.read_csv(input_file)
    result = evaluate_probabilities(
        frame,
        bins=bins,
        method=method,
        test_size=test_size,
        folds=folds,
        random_state=random_state,
        stratified=stratified,
    )
    Path(out_file).parent.mkdir(parents=True, exist_ok=True)
    Path(out_file).write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result

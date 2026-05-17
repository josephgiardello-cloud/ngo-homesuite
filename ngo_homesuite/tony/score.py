import logging
import math
import os
import pickle
import json
from hashlib import sha256
from datetime import datetime
from typing import Any
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from .config import load_config
from .utils import read_json, write_json

FEATURE_COLUMNS = [
    "continuity_months",
    "operating_margin",
    "program_expense_ratio",
    "liabilities_to_assets",
    "revenue_volatility",
]

_MODEL_CACHE: dict[str, Pipeline] = {}


def _descriptor(continuity_months: float | None, risk_probability: float, thresholds: dict[str, float]) -> str:
    if continuity_months is None:
        return "Unknown"
    if continuity_months >= thresholds["continuity_low"] and risk_probability < thresholds["risk_probability_moderate"]:
        return "Low Risk (Excellent)"
    if continuity_months >= thresholds["continuity_moderate"] and risk_probability < thresholds["risk_probability_high"]:
        return "Moderate Risk (Acceptable)"
    return "High Risk (Insufficient)"


def _resolve_thresholds(config: dict[str, Any], entity_type: str | None) -> dict[str, float]:
    thresholds = dict(config.get("thresholds", {}))
    if entity_type:
        entity_thresholds = config.get("entity_thresholds", {}).get(entity_type, {})
        thresholds.update(entity_thresholds)
    return thresholds


def _resolve_scoring_preset(config: dict[str, Any], preset_name: str | None = None) -> tuple[str, dict[str, float]]:
    presets = config.get("scoring_presets", {})
    selected = preset_name or config.get("scoring_preset", "balanced")
    if not isinstance(selected, str):
        selected = "balanced"
    selected = selected.strip().lower() or "balanced"

    defaults = {
        "benchmark_gap": 0.25,
        "confidence_penalty": 0.20,
        "donor_penalty": 0.15,
        "cashflow_penalty": 0.15,
        "compliance_penalty": 0.10,
        "irs_penalty": 0.15,
        "altman_penalty": 0.20,
        "trend_relief": 0.20,
        "charity_relief": 0.15,
    }

    preset = presets.get(selected, presets.get("balanced", {}))
    if not isinstance(preset, dict):
        preset = {}

    merged: dict[str, float] = {}
    for key, value in defaults.items():
        merged[key] = float(preset.get(key, value))
    return selected, merged


def _entity_profile(config: dict[str, Any], entity_type: str | None) -> dict[str, Any]:
    if not entity_type:
        return {}
    return config.get("entity_profiles", {}).get(entity_type, {})


def _weights_for_entity(config: dict[str, Any], entity_type: str | None) -> dict[str, float]:
    profile = _entity_profile(config, entity_type)
    base = dict(config.get("weights", {}))
    override = profile.get("weights", {})
    base.update(override)
    return base


def _feature_frame(records: list[dict[str, Any]]) -> pd.DataFrame:
    if not records:
        raise ValueError("No records available for scoring.")

    frame = pd.DataFrame(records).sort_values("year").reset_index(drop=True)
    if frame.empty:
        raise ValueError("No records available for scoring.")

    for column in [
        "revenue",
        "expenses",
        "program_expenses",
        "assets",
        "liabilities",
        "unrestricted_net_assets",
        "executive_compensation",
        "staff_salaries",
        "admin_salaries",
        "total_salaries",
        "executive_salary_ratio",
        "staff_salary_ratio",
        "admin_salary_ratio",
        "salaries_to_expense_ratio",
    ]:
        if column not in frame.columns:
            frame[column] = np.nan

    frame["continuity_months_raw"] = np.where(
        frame["expenses"].fillna(0) > 0,
        frame["unrestricted_net_assets"].fillna(0) / (frame["expenses"].replace(0, np.nan) / 12),
        np.nan,
    )
    # Preserve a tangible continuity value for reporting, but cap model input for stability.
    frame["continuity_months"] = frame["continuity_months_raw"].clip(lower=-24, upper=120)
    frame["operating_margin"] = np.where(
        frame["revenue"].fillna(0) > 0,
        (frame["revenue"].fillna(0) - frame["expenses"].fillna(0)) / frame["revenue"].replace(0, np.nan),
        0.0,
    )
    frame["program_expense_ratio"] = np.where(
        frame["expenses"].fillna(0) > 0,
        frame["program_expenses"].fillna(frame["expenses"] * 0.75) / frame["expenses"].replace(0, np.nan),
        np.nan,
    )
    frame["liabilities_to_assets"] = np.where(
        frame["assets"].fillna(0) > 0,
        frame["liabilities"].fillna(0) / frame["assets"].replace(0, np.nan),
        0.0,
    )
    revenue = frame["revenue"].astype(float)
    pct_vol = revenue.pct_change().replace([np.inf, -np.inf], np.nan).abs()
    log_rev = np.log(revenue.where(revenue > 0))
    log_vol = log_rev.diff().rolling(window=3, min_periods=2).std()
    frame["revenue_volatility"] = log_vol.fillna(pct_vol).fillna(0).clip(lower=0, upper=2)
    return frame


def _label_records(frame: pd.DataFrame, thresholds: dict[str, float], config: dict[str, Any]) -> pd.Series:
    rules = config.get("labeling", {})
    points_cutoff = int(rules.get("risk_points_cutoff", 3))
    severe_margin = float(rules.get("severe_margin", -0.1))
    weak_program_ratio = float(rules.get("weak_program_ratio", 0.55))
    high_volatility = float(rules.get("high_revenue_volatility", 0.35))

    continuity = frame["continuity_months"].fillna(0)
    margin = frame["operating_margin"].fillna(0)
    leverage = frame["liabilities_to_assets"].fillna(0)
    program_ratio = frame["program_expense_ratio"].fillna(0)
    volatility = frame["revenue_volatility"].fillna(0)

    points = (
        (continuity < float(thresholds["continuity_moderate"])).astype(int) * 2
        + (continuity < float(thresholds["continuity_low"])).astype(int)
        + (margin < 0).astype(int) * 2
        + (margin < severe_margin).astype(int)
        + (leverage > 0.9).astype(int) * 2
        + (leverage > 1.0).astype(int)
        + (program_ratio < weak_program_ratio).astype(int)
        + (volatility > high_volatility).astype(int)
    )
    return (points >= points_cutoff).astype(int)


def _load_reference_profiles(config: dict[str, Any]) -> pd.DataFrame:
    profiles = list(config.get("model", {}).get("reference_profiles", []))
    profiles_file = config.get("model", {}).get("reference_profiles_file")
    if profiles_file:
        resolved = os.path.abspath(os.path.expanduser(str(profiles_file)))
        if not os.path.exists(resolved):
            raise FileNotFoundError(f"Reference profiles file not found: {resolved}")
        suffix = os.path.splitext(resolved)[1].lower()
        if suffix == ".csv":
            external = pd.read_csv(resolved).to_dict(orient="records")
        elif suffix == ".json":
            with open(resolved, encoding="utf-8") as handle:
                raw = json.load(handle)
            if isinstance(raw, list):
                external = raw
            elif isinstance(raw, dict) and isinstance(raw.get("profiles"), list):
                external = raw["profiles"]
            else:
                raise ValueError("Reference profiles JSON must be a list or an object with a 'profiles' list.")
        else:
            raise ValueError("Reference profiles file must be CSV or JSON.")
        profiles.extend(external)

    if not profiles:
        return pd.DataFrame(columns=[*FEATURE_COLUMNS, "risk_label"])

    reference = pd.DataFrame(profiles)
    if "risk_label" not in reference.columns:
        raise ValueError("Reference profiles require a risk_label column.")
    return reference


def _model_cache_key(training: pd.DataFrame, config: dict[str, Any]) -> str:
    stable = training[[*FEATURE_COLUMNS, "risk_label"]].round(6).sort_values(FEATURE_COLUMNS).to_json(orient="records")
    model_opts = {
        "random_state": config["model"].get("random_state", 42),
        "max_iter": config["model"].get("max_iter", 1000),
        "class_weight": config["model"].get("class_weight", "balanced"),
    }
    return sha256((stable + str(model_opts)).encode("utf-8")).hexdigest()


def _load_pretrained_model(config: dict[str, Any]) -> Pipeline | None:
    path = config.get("model", {}).get("pretrained_model_path")
    if not path:
        return None
    resolved = os.path.abspath(os.path.expanduser(str(path)))
    if not os.path.exists(resolved):
        return None
    with open(resolved, "rb") as handle:
        model = pickle.load(handle)
    if not isinstance(model, Pipeline):
        raise ValueError("Pretrained model must be a sklearn Pipeline.")
    return model


def _persist_model_if_requested(model: Pipeline, config: dict[str, Any]) -> None:
    path = config.get("model", {}).get("pretrained_model_path")
    save_if_missing = bool(config.get("model", {}).get("save_trained_if_missing", False))
    if not path or not save_if_missing:
        return
    resolved = os.path.abspath(os.path.expanduser(str(path)))
    if os.path.exists(resolved):
        return
    parent = os.path.dirname(resolved)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(resolved, "wb") as handle:
        pickle.dump(model, handle)


def _train_model(feature_frame: pd.DataFrame, config: dict[str, Any], thresholds: dict[str, float]) -> Pipeline:
    pretrained = _load_pretrained_model(config)
    if pretrained is not None:
        return pretrained

    training = feature_frame[FEATURE_COLUMNS].copy()
    training["risk_label"] = _label_records(feature_frame, thresholds, config)
    reference = _load_reference_profiles(config)
    if not reference.empty:
        training = pd.concat([training, reference], ignore_index=True, sort=False)

    training = training.dropna(subset=["risk_label"]).copy()
    training["risk_label"] = training["risk_label"].astype(int)

    # Ensure at least two classes are available for logistic regression.
    if training["risk_label"].nunique() < 2:
        fallback = pd.DataFrame(
            [
                {"continuity_months": 2.0, "operating_margin": -0.1, "program_expense_ratio": 0.5, "liabilities_to_assets": 1.0, "revenue_volatility": 0.5, "risk_label": 1},
                {"continuity_months": 9.0, "operating_margin": 0.08, "program_expense_ratio": 0.8, "liabilities_to_assets": 0.35, "revenue_volatility": 0.1, "risk_label": 0},
            ]
        )
        training = pd.concat([training, fallback], ignore_index=True, sort=False)

    cache_enabled = bool(config.get("model", {}).get("cache_models", True))
    cache_key = _model_cache_key(training, config)
    if cache_enabled and cache_key in _MODEL_CACHE:
        return _MODEL_CACHE[cache_key]

    model = Pipeline(
        steps=[
            ("imputer", SimpleImputer(strategy="median")),
            ("scaler", StandardScaler()),
            (
                "classifier",
                LogisticRegression(
                    max_iter=int(config["model"].get("max_iter", 1000)),
                    class_weight=config["model"].get("class_weight", "balanced"),
                    random_state=int(config["model"].get("random_state", 42)),
                ),
            ),
        ]
    )
    model.fit(training[FEATURE_COLUMNS], training["risk_label"])
    if cache_enabled:
        _MODEL_CACHE[cache_key] = model
    _persist_model_if_requested(model, config)
    return model


def _sigmoid(value: float) -> float:
    bounded = max(min(value, 50.0), -50.0)
    return 1.0 / (1.0 + math.exp(-bounded))


def _normalization_settings(config: dict[str, Any], entity_type: str | None = None) -> dict[str, Any]:
    defaults = {
        "continuity_months": {"center": 6.0, "scale": 3.0, "low": -24.0, "high": 120.0, "positive": True},
        "operating_margin": {"center": 0.03, "scale": 0.08, "low": -0.5, "high": 0.5, "positive": True},
        "program_expense_ratio": {"center": 0.7, "scale": 0.12, "low": 0.0, "high": 1.0, "positive": True},
        "liabilities_to_assets": {"center": 0.6, "scale": 0.2, "low": 0.0, "high": 2.0, "positive": False},
        "revenue_volatility": {"center": 0.2, "scale": 0.12, "low": 0.0, "high": 2.0, "positive": False},
    }
    override = config.get("normalization", {})
    entity_override = _entity_profile(config, entity_type).get("normalization", {})
    merged: dict[str, Any] = {}
    for feature, settings in defaults.items():
        merged[feature] = {**settings, **override.get(feature, {}), **entity_override.get(feature, {})}
    return merged


def _weighted_health_score(latest_row: pd.Series, weights: dict[str, float], config: dict[str, Any], entity_type: str | None = None) -> tuple[float, dict[str, float]]:
    settings = _normalization_settings(config, entity_type)
    normalized: dict[str, float] = {}
    for feature, weight in weights.items():
        value = float(latest_row.get(feature) or 0.0)
        spec = settings.get(feature, {})
        low = float(spec.get("low", value))
        high = float(spec.get("high", value))
        center = float(spec.get("center", 0.0))
        scale = max(float(spec.get("scale", 1.0)), 1e-6)
        positive = bool(spec.get("positive", True))

        clipped = min(max(value, low), high)
        transformed = _sigmoid((clipped - center) / scale)
        normalized[feature] = transformed if positive else 1.0 - transformed

    score = sum(normalized[column] * weight for column, weight in weights.items())
    return round(score, 4), {k: round(v, 4) for k, v in normalized.items()}


def _time_weights(years: pd.Series, config: dict[str, Any]) -> pd.Series:
    if not bool(config.get("time_weighting", {}).get("enabled", True)):
        return pd.Series([1.0] * len(years), index=years.index)
    half_life = max(float(config.get("time_weighting", {}).get("half_life_years", 2.5)), 0.1)
    max_year = years.max()
    age = max_year - years
    weights = np.power(0.5, age / half_life)
    total = float(weights.sum())
    if total <= 0:
        return pd.Series([1.0 / len(years)] * len(years), index=years.index)
    return pd.Series((weights / total), index=years.index)


def _time_weighted_health_score(feature_frame: pd.DataFrame, weights: dict[str, float], config: dict[str, Any], entity_type: str | None) -> tuple[float, dict[str, float]]:
    tw = _time_weights(feature_frame["year"], config)
    settings = _normalization_settings(config, entity_type)
    normalized_means: dict[str, float] = {}
    for feature, weight in weights.items():
        spec = settings.get(feature, {})
        low = float(spec.get("low", -1e9))
        high = float(spec.get("high", 1e9))
        center = float(spec.get("center", 0.0))
        scale = max(float(spec.get("scale", 1.0)), 1e-6)
        positive = bool(spec.get("positive", True))
        values = pd.to_numeric(feature_frame[feature], errors="coerce").fillna(0.0)
        clipped = values.clip(lower=low, upper=high)
        transformed = clipped.apply(lambda v: _sigmoid((float(v) - center) / scale))
        transformed = transformed if positive else (1.0 - transformed)
        normalized_means[feature] = float((transformed * tw).sum())

    score = sum(normalized_means[column] * weights[column] for column in weights.keys())
    return round(float(score), 4), {k: round(v, 4) for k, v in normalized_means.items()}


def _apply_calibration_curve(raw_probability: float, config: dict[str, Any]) -> float | None:
    curve_file = config.get("model", {}).get("calibration_curve_file")
    if not curve_file:
        return None
    path = Path(os.path.abspath(os.path.expanduser(str(curve_file))))
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        curve = payload.get("curve", [])
        if not curve:
            return None
        x = np.array([float(row["mean_pred"]) for row in curve])
        y = np.array([float(row["observed_rate"]) for row in curve])
        if len(x) < 2:
            return None
        calibrated = float(np.interp(min(max(raw_probability, 0.0), 1.0), x, y))
        return round(calibrated, 4)
    except Exception:
        return None


def _probability_uncertainty(
    model: Pipeline,
    latest_row: pd.Series,
    feature_frame: pd.DataFrame,
    config: dict[str, Any],
) -> dict[str, float | None]:
    if not bool(config.get("uncertainty", {}).get("enabled", True)):
        return {"risk_probability_ci_low": None, "risk_probability_ci_high": None, "risk_probability_std": None}

    simulations = int(config.get("uncertainty", {}).get("simulations", 250))
    noise_scale = float(config.get("uncertainty", {}).get("feature_noise_scale", 0.12))
    sims = max(min(simulations, 2000), 50)

    baseline = np.array([float(latest_row.get(feature) or 0.0) for feature in FEATURE_COLUMNS], dtype=float)
    spread = feature_frame[FEATURE_COLUMNS].std(ddof=0).fillna(0.0).to_numpy(dtype=float)
    spread = np.where(spread <= 1e-9, np.maximum(np.abs(baseline) * 0.05, 0.01), spread)
    noise = np.random.default_rng(42).normal(loc=0.0, scale=spread * noise_scale, size=(sims, len(FEATURE_COLUMNS)))
    sim_features = baseline + noise
    sim_df = pd.DataFrame(sim_features, columns=FEATURE_COLUMNS)
    sim_probs = model.predict_proba(sim_df)[:, 1]
    low = float(np.quantile(sim_probs, 0.1))
    high = float(np.quantile(sim_probs, 0.9))
    std = float(np.std(sim_probs))
    return {
        "risk_probability_ci_low": round(max(min(low, 1.0), 0.0), 4),
        "risk_probability_ci_high": round(max(min(high, 1.0), 0.0), 4),
        "risk_probability_std": round(std, 5),
    }


def _final_index_blend(
    probability: float,
    weighted_health_score: float,
    config: dict[str, Any],
    entity_type: str | None,
) -> dict[str, float]:
    profile = _entity_profile(config, entity_type)
    blend = dict(config.get("final_index", {}))
    blend.update(profile.get("final_index", {}))
    ml_weight = float(blend.get("ml_weight", 0.7))
    health_weight = float(blend.get("health_weight", 0.3))
    total = ml_weight + health_weight
    if total <= 0:
        ml_weight, health_weight = 0.7, 0.3
        total = 1.0
    ml_weight /= total
    health_weight /= total

    ml_risk_index = probability * 100.0
    health_risk_index = (1.0 - weighted_health_score) * 100.0
    final = ml_weight * ml_risk_index + health_weight * health_risk_index
    return {
        "final_risk_index": round(float(min(max(final, 0.0), 100.0)), 2),
        "final_index_ml_weight": round(ml_weight, 4),
        "final_index_health_weight": round(health_weight, 4),
    }


def _feature_contributions(model: Pipeline, latest_row: pd.Series) -> dict[str, Any]:
    imputer = model.named_steps["imputer"]
    scaler = model.named_steps["scaler"]
    classifier = model.named_steps["classifier"]

    latest_df = pd.DataFrame([{feature: latest_row.get(feature) for feature in FEATURE_COLUMNS}])
    transformed = scaler.transform(imputer.transform(latest_df))[0]
    coefficients = classifier.coef_[0]
    base_logit = float(classifier.intercept_[0]) if hasattr(classifier, "intercept_") else 0.0
    contributions = {feature: float(transformed[idx] * coefficients[idx]) for idx, feature in enumerate(FEATURE_COLUMNS)}
    ranked = sorted(contributions.items(), key=lambda item: abs(item[1]), reverse=True)
    shap_logit_values = {key: round(value, 5) for key, value in contributions.items()}
    model_logit = base_logit + float(sum(contributions.values()))
    model_probability = _sigmoid(model_logit)
    return {
        "feature_contributions": {key: round(value, 5) for key, value in contributions.items()},
        "shap_linear_logit_values": shap_logit_values,
        "shap_base_logit": round(base_logit, 5),
        "shap_total_logit": round(model_logit, 5),
        "shap_probability_from_logit": round(float(model_probability), 5),
        "top_drivers": [
            {
                "feature": name,
                "contribution": round(value, 5),
                "direction": "increases risk" if value > 0 else "reduces risk",
            }
            for name, value in ranked[:3]
        ],
    }


def _safe_series_delta(series: pd.Series) -> float:
    clean = pd.to_numeric(series, errors="coerce").dropna()
    if len(clean) < 2:
        return 0.0
    return float(clean.iloc[-1] - clean.iloc[0])


def _safe_yoy_growth(series: pd.Series) -> float | None:
    clean = pd.to_numeric(series, errors="coerce").dropna()
    if len(clean) < 2:
        return None
    previous = float(clean.iloc[-2])
    latest = float(clean.iloc[-1])
    if abs(previous) < 1e-9:
        return None
    return (latest - previous) / abs(previous)


def _safe_ratio(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or denominator is None:
        return None
    if abs(float(denominator)) < 1e-9:
        return None
    return float(numerator) / float(denominator)


def _altman_zscore_nonprofit(latest_row: pd.Series, config: dict[str, Any]) -> dict[str, float | str | None]:
    assets = _first_numeric(latest_row, ["assets", "total_assets"])
    liabilities = _first_numeric(latest_row, ["liabilities", "total_liabilities"])

    current_assets = _first_numeric(latest_row, ["current_assets", "cash_and_equivalents", "cash"])
    current_liabilities = _first_numeric(latest_row, ["current_liabilities", "short_term_liabilities", "accounts_payable"])

    liquidity_proxy_used = False
    if current_assets is not None and current_liabilities is not None:
        working_capital = current_assets - current_liabilities
    elif assets is not None and liabilities is not None:
        # Fallback proxy when current balance sheet splits are unavailable.
        working_capital = assets - liabilities
        liquidity_proxy_used = True
    else:
        working_capital = None

    retained_earnings = _first_numeric(latest_row, ["retained_earnings", "unrestricted_net_assets", "net_assets"])
    ebit = _first_numeric(latest_row, ["ebit", "operating_income"])
    if ebit is None:
        revenue = _first_numeric(latest_row, ["revenue", "total_revenue"])
        expenses = _first_numeric(latest_row, ["expenses", "total_expenses"])
        if revenue is not None and expenses is not None:
            ebit = revenue - expenses

    book_equity = _first_numeric(latest_row, ["book_value_equity", "unrestricted_net_assets", "net_assets"])
    if book_equity is None and assets is not None and liabilities is not None:
        book_equity = assets - liabilities
    if retained_earnings is None:
        retained_earnings = book_equity

    x1 = _safe_ratio(working_capital, assets)
    x2 = _safe_ratio(retained_earnings, assets)
    x3 = _safe_ratio(ebit, assets)
    x4 = _safe_ratio(book_equity, liabilities)

    z_score = None
    if x1 is not None and x2 is not None and x3 is not None and x4 is not None:
        z_score = 6.56 * x1 + 3.26 * x2 + 6.72 * x3 + 1.05 * x4

    altman_cfg = config.get("altman_z", {}) if isinstance(config.get("altman_z"), dict) else {}
    safe_threshold = float(altman_cfg.get("safe_threshold", 2.6))
    distress_threshold = float(altman_cfg.get("distress_threshold", 1.1))

    zone = "unknown"
    if z_score is not None:
        if z_score > safe_threshold:
            zone = "safe"
        elif z_score < distress_threshold:
            zone = "distress"
        else:
            zone = "grey"

    return {
        "altman_z_score": round(float(z_score), 4) if z_score is not None else None,
        "altman_zone": zone,
        "altman_x1_working_capital_to_assets": round(float(x1), 4) if x1 is not None else None,
        "altman_x2_retained_earnings_to_assets": round(float(x2), 4) if x2 is not None else None,
        "altman_x3_ebit_to_assets": round(float(x3), 4) if x3 is not None else None,
        "altman_x4_equity_to_liabilities": round(float(x4), 4) if x4 is not None else None,
        "altman_liquidity_proxy_used": liquidity_proxy_used,
    }


def _altman_penalty(zone: str) -> float:
    if zone == "distress":
        return 1.0
    if zone == "grey":
        return 0.4
    if zone == "safe":
        return 0.0
    return 0.2


def _grant_recommendation(
    risk_probability: float,
    continuity_months: float | None,
    operating_margin: float,
    liabilities_to_assets: float,
    data_confidence_score: float,
    altman_zone: str | None = None,
    organizational_health_score: float | None = None,
    legal_reputation_risk_score: float | None = None,
) -> dict[str, Any]:
    reasons: list[str] = []
    if continuity_months is not None and continuity_months < 3:
        reasons.append("low_reserve_months")
    if operating_margin < 0:
        reasons.append("negative_operating_margin")
    if liabilities_to_assets > 1.0:
        reasons.append("high_leverage")
    if data_confidence_score < 0.5:
        reasons.append("low_data_confidence")
    if altman_zone == "distress":
        reasons.append("altman_distress_zone")
    elif altman_zone == "grey":
        reasons.append("altman_grey_zone")
    if organizational_health_score is not None and organizational_health_score < 0.45:
        reasons.append("organizational_health_weak")
    if legal_reputation_risk_score is not None and legal_reputation_risk_score > 0.6:
        reasons.append("legal_reputation_risk_high")

    if risk_probability >= 0.65 or altman_zone == "distress" or len(reasons) >= 2:
        label = "Elevated Risk"
    elif risk_probability >= 0.4 or len(reasons) == 1:
        label = "Conditional"
    else:
        label = "Standard"

    return {
        "label": label,
        "reasons": reasons,
    }


def _standard_grant_metrics(
    feature_frame: pd.DataFrame,
    latest_row: pd.Series,
    continuity_raw: float | None,
    data_confidence_score: float,
    compliance_score: float | None,
    risk_probability: float,
    donor_top_share: float | None,
    months_cash_on_hand: float | None,
    altman: dict[str, float | str | None],
) -> dict[str, float | None]:
    revenue_growth_yoy = _safe_yoy_growth(feature_frame["revenue"])
    expense_growth_yoy = _safe_yoy_growth(feature_frame["expenses"])
    net_assets_growth_yoy = _safe_yoy_growth(feature_frame["unrestricted_net_assets"])

    operating_margin = float(latest_row.get("operating_margin") or 0.0)
    program_expense_ratio = float(latest_row.get("program_expense_ratio") or 0.0)
    liabilities_to_assets = float(latest_row.get("liabilities_to_assets") or 0.0)

    return {
        "reserve_months": round(float(continuity_raw), 2) if continuity_raw is not None else None,
        "operating_margin": round(operating_margin, 4),
        "program_expense_ratio": round(program_expense_ratio, 4),
        "liabilities_to_assets": round(liabilities_to_assets, 4),
        "revenue_growth_yoy": round(float(revenue_growth_yoy), 4) if revenue_growth_yoy is not None else None,
        "expense_growth_yoy": round(float(expense_growth_yoy), 4) if expense_growth_yoy is not None else None,
        "net_assets_growth_yoy": round(float(net_assets_growth_yoy), 4) if net_assets_growth_yoy is not None else None,
        "months_cash_on_hand": round(float(months_cash_on_hand), 2) if months_cash_on_hand is not None else None,
        "donor_top_share": round(float(donor_top_share), 4) if donor_top_share is not None else None,
        "data_confidence_score": round(float(data_confidence_score), 4),
        "compliance_score": round(float(compliance_score), 4) if compliance_score is not None else None,
        "risk_probability": round(float(risk_probability), 4),
        "altman_z_score": altman.get("altman_z_score"),
        "altman_zone": altman.get("altman_zone"),
    }


def _derive_size_band_from_assets(assets: float | None) -> str | None:
    if assets is None:
        return None
    if assets < 1_000_000:
        return "micro"
    if assets < 10_000_000:
        return "small"
    if assets < 50_000_000:
        return "mid"
    return "large"


def _resolve_peer_group_context(
    latest_row: pd.Series,
    metadata: dict[str, Any] | None,
    peer_cfg: dict[str, Any],
) -> dict[str, str]:
    metadata = metadata or {}
    keys = peer_cfg.get("keys", ["size_band", "ntee_code", "state"])
    if not isinstance(keys, list):
        keys = ["size_band", "ntee_code", "state"]

    context: dict[str, str] = {}
    for key in keys:
        if key == "size_band":
            assets = _first_numeric(latest_row, ["assets", "total_assets"])
            value = _derive_size_band_from_assets(assets)
        else:
            row_value = latest_row.get(key)
            value = None
            if isinstance(row_value, str) and row_value.strip():
                value = row_value.strip()
            elif isinstance(metadata.get(key), str) and str(metadata.get(key)).strip():
                value = str(metadata.get(key)).strip()
        if value:
            context[str(key)] = str(value)
    return context


def _peer_benchmark(
    latest_row: pd.Series,
    config: dict[str, Any],
    feature_frame: pd.DataFrame,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    reference = _load_reference_profiles(config)
    peer_cfg = config.get("peer_benchmark", {}) if isinstance(config.get("peer_benchmark"), dict) else {}

    working_reference = reference.copy()
    selected_group: dict[str, str] = {}
    group_rows = None

    if not working_reference.empty:
        min_group_rows = max(int(peer_cfg.get("min_group_rows", 20)), 1)
        context = _resolve_peer_group_context(latest_row, metadata, peer_cfg)
        filtered = working_reference
        for key, value in context.items():
            if key in filtered.columns:
                narrowed = filtered[filtered[key].astype(str).str.strip().str.lower() == value.lower()]
                if not narrowed.empty:
                    filtered = narrowed
        if len(filtered) >= min_group_rows:
            working_reference = filtered
            selected_group = context
            group_rows = int(len(filtered))
        else:
            group_rows = int(len(working_reference))

    peer_pool = working_reference[FEATURE_COLUMNS].copy() if not working_reference.empty else feature_frame[FEATURE_COLUMNS].copy()

    mode = str(peer_cfg.get("mode", "percentile_only")).strip().lower()
    if mode not in {"percentile_only", "zscore_only", "blended"}:
        mode = "percentile_only"
    zscore_clip = max(float(peer_cfg.get("zscore_clip", 3.0)), 0.5)
    percentile_weight = max(float(peer_cfg.get("percentile_weight", 0.7)), 0.0)
    zscore_weight = max(float(peer_cfg.get("zscore_weight", 0.3)), 0.0)
    weight_total = percentile_weight + zscore_weight
    if weight_total <= 0:
        percentile_weight, zscore_weight = 0.7, 0.3
        weight_total = 1.0
    percentile_weight /= weight_total
    zscore_weight /= weight_total

    if peer_pool.empty:
        return {
            "peer_percentiles": {},
            "peer_z_scores": {},
            "peer_benchmark_score": None,
            "peer_percentile_score": None,
            "peer_zscore_score": None,
            "peer_benchmark_mode": mode,
            "peer_group_filters": selected_group,
            "peer_group_rows": group_rows,
        }

    settings = _normalization_settings(config)
    percentiles: dict[str, float] = {}
    z_scores: dict[str, float] = {}
    zscore_scores: list[float] = []
    for feature in FEATURE_COLUMNS:
        population = pd.to_numeric(peer_pool[feature], errors="coerce").dropna()
        if population.empty:
            continue
        value = float(latest_row.get(feature) or 0.0)
        positive = bool(settings.get(feature, {}).get("positive", True))

        mean = float(population.mean())
        std = float(population.std(ddof=0))
        std = std if std > 1e-9 else 1.0

        if positive:
            percentile = float((population <= value).mean() * 100)
            z_value = (value - mean) / std
        else:
            percentile = float((population >= value).mean() * 100)
            z_value = (mean - value) / std

        z_clipped = float(min(max(z_value, -zscore_clip), zscore_clip))
        zscore_component = (z_clipped + zscore_clip) / (2 * zscore_clip)

        percentiles[feature] = round(percentile, 1)
        z_scores[feature] = round(z_value, 4)
        zscore_scores.append(zscore_component)

    percentile_score = round(float(np.mean(list(percentiles.values()))) / 100, 4) if percentiles else None
    zscore_score = round(float(np.mean(zscore_scores)), 4) if zscore_scores else None

    if mode == "zscore_only":
        benchmark = zscore_score
    elif mode == "blended":
        if percentile_score is None and zscore_score is None:
            benchmark = None
        elif percentile_score is None:
            benchmark = zscore_score
        elif zscore_score is None:
            benchmark = percentile_score
        else:
            benchmark = round(percentile_weight * percentile_score + zscore_weight * zscore_score, 4)
    else:
        benchmark = percentile_score

    return {
        "peer_percentiles": percentiles,
        "peer_z_scores": z_scores,
        "peer_benchmark_score": benchmark,
        "peer_percentile_score": percentile_score,
        "peer_zscore_score": zscore_score,
        "peer_benchmark_mode": mode,
        "peer_group_filters": selected_group,
        "peer_group_rows": group_rows,
    }


def _data_confidence(payload: dict[str, Any]) -> float:
    records = payload.get("records", [])
    if not records:
        return 0.0

    raw = pd.DataFrame(records)
    required = ["year", "revenue", "expenses", "assets", "liabilities", "unrestricted_net_assets", "program_expenses"]
    completeness_scores: list[float] = []
    for column in required:
        if column not in raw.columns:
            completeness_scores.append(0.0)
        else:
            completeness_scores.append(float(pd.to_numeric(raw[column], errors="coerce").notna().mean()))

    completeness = float(np.mean(completeness_scores)) if completeness_scores else 0.0
    history_depth = min(len(records) / 5, 1.0)
    return round(completeness * 0.75 + history_depth * 0.25, 4)


def _trend_stability(feature_frame: pd.DataFrame) -> dict[str, float]:
    continuity_delta = _safe_series_delta(feature_frame["continuity_months_raw"])
    margin_delta = _safe_series_delta(feature_frame["operating_margin"])
    leverage_improvement = -_safe_series_delta(feature_frame["liabilities_to_assets"])

    # Bound trend contributions to avoid outliers dominating the final score.
    continuity_component = np.tanh(continuity_delta / 12)
    margin_component = np.tanh(margin_delta / 0.2)
    leverage_component = np.tanh(leverage_improvement / 0.4)
    trend_score = float(np.mean([continuity_component, margin_component, leverage_component]))

    return {
        "continuity_delta": round(float(continuity_delta), 4),
        "operating_margin_delta": round(float(margin_delta), 4),
        "leverage_improvement": round(float(leverage_improvement), 4),
        "trend_stability_score": round(trend_score, 4),
    }


def _resolve_compliance_score(payload: dict[str, Any]) -> float | None:
    metadata = payload.get("metadata", {}) if isinstance(payload, dict) else {}
    direct = metadata.get("compliance_score")
    if isinstance(direct, (int, float)):
        return max(min(float(direct) / 100, 1.0), 0.0)

    compliance_payload = payload.get("compliance") if isinstance(payload.get("compliance"), dict) else None
    if compliance_payload and isinstance(compliance_payload.get("overall_score"), (int, float)):
        return max(min(float(compliance_payload["overall_score"]) / 100, 1.0), 0.0)
    return None


def _resolve_external_signal_scores(payload: dict[str, Any]) -> dict[str, float | None]:
    metadata = payload.get("metadata", {}) if isinstance(payload, dict) else {}
    irs_risk = metadata.get("irs_teos_status_risk")
    charity_score = metadata.get("charity_navigator_score")

    irs_risk_value = None
    charity_score_value = None
    if isinstance(irs_risk, (int, float)):
        irs_risk_value = max(min(float(irs_risk), 1.0), 0.0)
    if isinstance(charity_score, (int, float)):
        charity_score_value = max(min(float(charity_score), 1.0), 0.0)

    return {
        "irs_teos_status_risk": irs_risk_value,
        "charity_navigator_score": charity_score_value,
    }


def _first_numeric(latest_row: pd.Series, candidates: list[str]) -> float | None:
    for key in candidates:
        value = latest_row.get(key)
        if value is None:
            continue
        numeric = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
        if pd.notna(numeric):
            return float(numeric)
    return None


def _donor_and_revenue_quality(latest_row: pd.Series) -> dict[str, float | None]:
    revenue = float(latest_row.get("revenue") or 0.0)
    top_donor_share = _first_numeric(latest_row, ["top_donor_share", "largest_donor_share", "donor_concentration"])
    contributions = _first_numeric(latest_row, ["contributions_revenue", "donations_revenue", "contributed_revenue"]) or 0.0
    grants = _first_numeric(latest_row, ["grant_revenue", "grants_revenue"]) or 0.0
    program_revenue = _first_numeric(latest_row, ["program_revenue", "earned_revenue", "service_revenue"]) or 0.0
    volatility = float(latest_row.get("revenue_volatility") or 0.0)

    if top_donor_share is None:
        # Fallback proxy when donor concentration data is unavailable.
        top_donor_share = min(max(0.22 + 0.45 * volatility, 0.05), 0.95)

    if revenue > 0:
        recurring_mix = min(max((program_revenue + 0.5 * contributions + 0.3 * grants) / revenue, 0.0), 1.0)
    else:
        recurring_mix = 0.0

    donor_concentration_score = min(max(1.0 - top_donor_share, 0.0), 1.0)
    stability_bonus = min(max(1.0 - volatility, 0.0), 1.0)
    revenue_quality_score = round(float(0.6 * recurring_mix + 0.4 * stability_bonus), 4)

    return {
        "donor_top_share": round(float(top_donor_share), 4),
        "donor_concentration_score": round(float(donor_concentration_score), 4),
        "revenue_quality_score": revenue_quality_score,
    }


def _cashflow_durability(latest_row: pd.Series) -> dict[str, float | None]:
    revenue = float(latest_row.get("revenue") or 0.0)
    expenses = float(latest_row.get("expenses") or 0.0)
    operating_cash_flow = revenue - expenses

    reserves = _first_numeric(latest_row, ["cash_and_equivalents", "cash", "reserves", "unrestricted_net_assets"]) or 0.0
    debt_service = _first_numeric(latest_row, ["debt_service", "annual_debt_service", "interest_and_principal"])

    monthly_burn = expenses / 12 if expenses > 0 else None
    months_cash_on_hand = reserves / monthly_burn if monthly_burn else None

    stressed_revenue = revenue * 0.85
    stressed_monthly_burn = (expenses * 1.08) / 12 if expenses > 0 else None
    stressed_cash = reserves + min(stressed_revenue - expenses, 0)
    months_cash_stressed = (stressed_cash / stressed_monthly_burn) if stressed_monthly_burn and stressed_cash > 0 else 0.0

    dscr = (operating_cash_flow / debt_service) if debt_service and debt_service > 0 else None

    months_component = np.tanh((months_cash_on_hand or 0.0) / 10)
    stress_component = np.tanh((months_cash_stressed or 0.0) / 8)
    dscr_component = np.tanh(((dscr or 1.0) - 1.0) / 0.8)
    durability_score = float(np.mean([months_component, stress_component, dscr_component]))

    return {
        "operating_cash_flow": round(float(operating_cash_flow), 2),
        "months_cash_on_hand": round(float(months_cash_on_hand), 2) if months_cash_on_hand is not None else None,
        "months_cash_on_hand_stress": round(float(months_cash_stressed), 2),
        "debt_service_coverage": round(float(dscr), 3) if dscr is not None else None,
        "cashflow_durability_score": round(float(min(max(durability_score, 0.0), 1.0)), 4),
    }


def _compensation_burden(latest_row: pd.Series) -> dict[str, float | None]:
    expenses = float(latest_row.get("expenses") or 0.0)
    executive = pd.to_numeric(pd.Series([latest_row.get("executive_compensation")]), errors="coerce").iloc[0]
    staff = pd.to_numeric(pd.Series([latest_row.get("staff_salaries")]), errors="coerce").iloc[0]
    admin = pd.to_numeric(pd.Series([latest_row.get("admin_salaries")]), errors="coerce").iloc[0]
    total = pd.to_numeric(pd.Series([latest_row.get("total_salaries")]), errors="coerce").iloc[0]

    executive = float(executive) if pd.notna(executive) else None
    staff = float(staff) if pd.notna(staff) else None
    admin = float(admin) if pd.notna(admin) else None
    total = float(total) if pd.notna(total) else None

    if total is None:
        components = [value for value in [executive, staff, admin] if value is not None]
        total = float(sum(components)) if len(components) >= 2 else None

    return {
        "executive_compensation": round(executive, 2) if executive is not None else None,
        "staff_salaries": round(staff, 2) if staff is not None else None,
        "admin_salaries": round(admin, 2) if admin is not None else None,
        "total_salaries": round(total, 2) if total is not None else None,
        "executive_salary_ratio": round((executive / expenses), 6) if executive is not None and expenses > 0 else None,
        "staff_salary_ratio": round((staff / expenses), 6) if staff is not None and expenses > 0 else None,
        "admin_salary_ratio": round((admin / expenses), 6) if admin is not None and expenses > 0 else None,
        "salaries_to_expense_ratio": round((total / expenses), 6) if total is not None and expenses > 0 else None,
    }


def _coerce_unit_interval(value: Any) -> float | None:
    numeric = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
    if pd.isna(numeric):
        return None
    v = float(numeric)
    if v > 1.0:
        v = v / 100.0
    return min(max(v, 0.0), 1.0)


def _coerce_non_negative(value: Any) -> float | None:
    numeric = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
    if pd.isna(numeric):
        return None
    return max(float(numeric), 0.0)


def _financial_depth_metrics(latest_row: pd.Series) -> dict[str, float | None]:
    current_assets = _first_numeric(latest_row, ["current_assets", "cash_and_equivalents", "cash"])
    current_liabilities = _first_numeric(latest_row, ["current_liabilities", "short_term_liabilities", "accounts_payable"])
    inventories = _first_numeric(latest_row, ["inventory", "inventories"]) or 0.0

    working_capital = None
    working_capital_ratio = None
    current_ratio = None
    quick_ratio = None

    assets = _first_numeric(latest_row, ["assets", "total_assets"])
    if current_assets is not None and current_liabilities is not None:
        working_capital = current_assets - current_liabilities
        working_capital_ratio = _safe_ratio(working_capital, assets)
        current_ratio = _safe_ratio(current_assets, current_liabilities)
        quick_ratio = _safe_ratio(current_assets - inventories, current_liabilities)

    fundraising_expense = _first_numeric(latest_row, ["fundraising_expense", "fundraising_cost", "fundraising_expenses"])
    contributions = _first_numeric(latest_row, ["contributions_revenue", "donations_revenue", "contributed_revenue"])
    fundraising_cost_to_raise_dollar = _safe_ratio(fundraising_expense, contributions)

    investment_income = _first_numeric(latest_row, ["investment_income", "investment_return", "investment_revenue"])
    endowment_assets = _first_numeric(latest_row, ["endowment_assets", "endowment_balance", "quasi_endowment"])
    endowment_draw = _first_numeric(latest_row, ["endowment_draw", "investment_spending", "draw_from_endowment"])
    investment_income_ratio = _safe_ratio(investment_income, _first_numeric(latest_row, ["revenue", "total_revenue"]))
    endowment_draw_rate = _safe_ratio(endowment_draw, endowment_assets)

    return {
        "working_capital": round(float(working_capital), 2) if working_capital is not None else None,
        "working_capital_ratio": round(float(working_capital_ratio), 4) if working_capital_ratio is not None else None,
        "current_ratio": round(float(current_ratio), 4) if current_ratio is not None else None,
        "quick_ratio": round(float(quick_ratio), 4) if quick_ratio is not None else None,
        "fundraising_cost_to_raise_dollar": round(float(fundraising_cost_to_raise_dollar), 4) if fundraising_cost_to_raise_dollar is not None else None,
        "investment_income_ratio": round(float(investment_income_ratio), 4) if investment_income_ratio is not None else None,
        "endowment_draw_rate": round(float(endowment_draw_rate), 4) if endowment_draw_rate is not None else None,
    }


def _organizational_health(metadata: dict[str, Any] | None) -> dict[str, Any]:
    metadata = metadata or {}

    board_independence = _coerce_unit_interval(metadata.get("board_independence"))
    board_turnover = _coerce_unit_interval(metadata.get("board_turnover_rate"))
    succession_plan = _coerce_unit_interval(metadata.get("succession_plan_score"))
    conflict_policy = _coerce_unit_interval(metadata.get("conflict_of_interest_policy_score"))
    board_diversity = _coerce_unit_interval(metadata.get("board_diversity_index"))

    governance_components = [
        board_independence,
        (1.0 - board_turnover) if board_turnover is not None else None,
        succession_plan,
        conflict_policy,
        board_diversity,
    ]
    governance_values = [v for v in governance_components if v is not None]
    governance_score = float(np.mean(governance_values)) if governance_values else None

    impact_outcome_rate = _coerce_unit_interval(metadata.get("outcome_achievement_rate"))
    impact_eval_score = _coerce_unit_interval(metadata.get("impact_evaluation_score"))
    beneficiary_reach_growth = _coerce_unit_interval(metadata.get("beneficiary_reach_growth"))
    impact_values = [v for v in [impact_outcome_rate, impact_eval_score, beneficiary_reach_growth] if v is not None]
    impact_score = float(np.mean(impact_values)) if impact_values else None

    cybersecurity_maturity = _coerce_unit_interval(metadata.get("cybersecurity_maturity"))
    continuity_plan = _coerce_unit_interval(metadata.get("business_continuity_plan_score"))
    key_person_risk = _coerce_unit_interval(metadata.get("key_person_risk"))
    ops_values = [
        v
        for v in [
            cybersecurity_maturity,
            continuity_plan,
            (1.0 - key_person_risk) if key_person_risk is not None else None,
        ]
        if v is not None
    ]
    operational_resilience_score = float(np.mean(ops_values)) if ops_values else None

    demand_trend = _coerce_unit_interval(metadata.get("demand_trend_score"))
    market_share_change = _coerce_unit_interval(metadata.get("market_share_change_score"))
    competitor_pressure = _coerce_unit_interval(metadata.get("competitor_pressure"))
    market_values = [
        v
        for v in [
            demand_trend,
            market_share_change,
            (1.0 - competitor_pressure) if competitor_pressure is not None else None,
        ]
        if v is not None
    ]
    market_position_score = float(np.mean(market_values)) if market_values else None

    staff_turnover = _coerce_unit_interval(metadata.get("staff_turnover_rate"))
    volunteer_engagement = _coerce_unit_interval(metadata.get("volunteer_engagement_score"))
    training_investment = _coerce_unit_interval(metadata.get("training_investment_score"))
    dei_index = _coerce_unit_interval(metadata.get("dei_index"))
    human_values = [
        v
        for v in [
            (1.0 - staff_turnover) if staff_turnover is not None else None,
            volunteer_engagement,
            training_investment,
            dei_index,
        ]
        if v is not None
    ]
    human_capital_score = float(np.mean(human_values)) if human_values else None

    litigation_count = _coerce_non_negative(metadata.get("open_litigation_count"))
    watchdog_flags = _coerce_non_negative(metadata.get("watchdog_flags"))
    adverse_media = _coerce_unit_interval(metadata.get("adverse_media_score"))
    whistleblower_cases = _coerce_non_negative(metadata.get("whistleblower_cases"))

    litigation_risk = min((litigation_count or 0.0) / 5.0, 1.0)
    watchdog_risk = min((watchdog_flags or 0.0) / 4.0, 1.0)
    whistleblower_risk = min((whistleblower_cases or 0.0) / 3.0, 1.0)
    legal_risk_components = [litigation_risk, watchdog_risk, whistleblower_risk]
    if adverse_media is not None:
        legal_risk_components.append(adverse_media)
    legal_reputation_risk_score = float(np.mean(legal_risk_components)) if legal_risk_components else None

    fraud_signal_score = max(watchdog_risk, whistleblower_risk, adverse_media or 0.0)

    health_components = [
        governance_score,
        impact_score,
        operational_resilience_score,
        market_position_score,
        human_capital_score,
        (1.0 - legal_reputation_risk_score) if legal_reputation_risk_score is not None else None,
    ]
    health_values = [v for v in health_components if v is not None]
    organizational_health_score = float(np.mean(health_values)) if health_values else None

    return {
        "governance_score": round(float(governance_score), 4) if governance_score is not None else None,
        "program_impact_score": round(float(impact_score), 4) if impact_score is not None else None,
        "operational_resilience_score": round(float(operational_resilience_score), 4) if operational_resilience_score is not None else None,
        "market_position_score": round(float(market_position_score), 4) if market_position_score is not None else None,
        "human_capital_score": round(float(human_capital_score), 4) if human_capital_score is not None else None,
        "legal_reputation_risk_score": round(float(legal_reputation_risk_score), 4) if legal_reputation_risk_score is not None else None,
        "fraud_signal_score": round(float(fraud_signal_score), 4),
        "organizational_health_score": round(float(organizational_health_score), 4) if organizational_health_score is not None else None,
    }


def _scenario_stress_tests(
    latest_row: pd.Series,
    baseline_probability: float,
    donor_top_share: float | None,
    config: dict[str, Any],
) -> dict[str, Any]:
    scenario_cfg = config.get("scenario_testing", {}) if isinstance(config.get("scenario_testing"), dict) else {}
    scenarios = scenario_cfg.get("scenarios", [])
    if not isinstance(scenarios, list) or not scenarios:
        scenarios = [
            {"name": "moderate_stress", "revenue_shock_pct": 0.10, "expense_shock_pct": 0.06, "reserve_haircut_pct": 0.10, "donor_loss_pct": 0.08},
            {"name": "severe_stress", "revenue_shock_pct": 0.20, "expense_shock_pct": 0.12, "reserve_haircut_pct": 0.20, "donor_loss_pct": 0.15},
        ]

    revenue = float(latest_row.get("revenue") or 0.0)
    expenses = float(latest_row.get("expenses") or 0.0)
    reserves = float(_first_numeric(latest_row, ["cash_and_equivalents", "cash", "reserves", "unrestricted_net_assets"]) or 0.0)
    liabilities_to_assets = float(latest_row.get("liabilities_to_assets") or 0.0)

    results: list[dict[str, Any]] = []
    probabilities = [float(min(max(baseline_probability, 0.0), 1.0))]

    for raw_scenario in scenarios:
        if not isinstance(raw_scenario, dict):
            continue
        name = str(raw_scenario.get("name", "stress")).strip() or "stress"
        rev_shock = min(max(float(raw_scenario.get("revenue_shock_pct", 0.0)), 0.0), 0.9)
        exp_shock = min(max(float(raw_scenario.get("expense_shock_pct", 0.0)), 0.0), 0.9)
        reserve_haircut = min(max(float(raw_scenario.get("reserve_haircut_pct", 0.0)), 0.0), 0.95)
        donor_loss = min(max(float(raw_scenario.get("donor_loss_pct", 0.0)), 0.0), 0.95)

        stressed_revenue = revenue * (1.0 - rev_shock)
        stressed_expenses = expenses * (1.0 + exp_shock)
        stressed_margin = (stressed_revenue - stressed_expenses) / stressed_revenue if stressed_revenue > 0 else -1.0

        stressed_monthly_burn = (stressed_expenses / 12.0) if stressed_expenses > 0 else None
        stressed_reserves = reserves * (1.0 - reserve_haircut)
        stressed_continuity = (stressed_reserves / stressed_monthly_burn) if stressed_monthly_burn and stressed_monthly_burn > 0 else 0.0

        stressed_donor_share = None
        if donor_top_share is not None:
            stressed_donor_share = min(max(float(donor_top_share) + donor_loss, 0.0), 1.0)

        # Deterministic stress uplift with transparent components.
        delta_margin = max(0.0, -stressed_margin) * 0.50
        delta_liquidity = max(0.0, 3.0 - float(stressed_continuity)) / 6.0 * 0.25
        delta_donor = max(0.0, (stressed_donor_share or 0.0) - 0.35) * 0.25
        delta_leverage = 0.10 if liabilities_to_assets > 1.0 else 0.0

        scenario_probability = min(max(baseline_probability + delta_margin + delta_liquidity + delta_donor + delta_leverage, 0.0), 1.0)
        probabilities.append(float(scenario_probability))

        results.append(
            {
                "scenario": name,
                "stressed_operating_margin": round(float(stressed_margin), 4),
                "stressed_reserve_months": round(float(stressed_continuity), 2),
                "stressed_donor_top_share": round(float(stressed_donor_share), 4) if stressed_donor_share is not None else None,
                "scenario_risk_probability": round(float(scenario_probability), 4),
            }
        )

    return {
        "scenario_stress_tests": results,
        "scenario_worst_case_probability": round(float(max(probabilities)), 4),
        "scenario_median_probability": round(float(np.median(probabilities)), 4),
    }


def _plain_language_explanation(summary: dict[str, Any]) -> list[str]:
    notes: list[str] = []

    final_prob = summary.get("final_risk_probability")
    if isinstance(final_prob, (float, int)):
        if final_prob >= 0.65:
            notes.append("Overall risk is elevated because the final risk probability is above 0.65.")
        elif final_prob >= 0.4:
            notes.append("Overall risk is conditional because the final risk probability is between 0.40 and 0.65.")
        else:
            notes.append("Overall risk is lower based on current financial and organizational signals.")

    altman_zone = None
    altman_payload = summary.get("altman")
    if isinstance(altman_payload, dict):
        altman_zone = altman_payload.get("altman_zone")
    if altman_zone == "distress":
        notes.append("Altman Z'' places the organization in the distress zone.")
    elif altman_zone == "grey":
        notes.append("Altman Z'' places the organization in a cautionary grey zone.")

    current_ratio = summary.get("current_ratio")
    if isinstance(current_ratio, (float, int)) and float(current_ratio) < 1.0:
        notes.append("Short-term liquidity is tight because the current ratio is below 1.0.")

    fundraising_cost = summary.get("fundraising_cost_to_raise_dollar")
    if isinstance(fundraising_cost, (float, int)) and float(fundraising_cost) > 0.35:
        notes.append("Fundraising efficiency is weak because cost to raise one dollar exceeds 0.35.")

    org_health = summary.get("organizational_health_score")
    if isinstance(org_health, (float, int)) and float(org_health) < 0.45:
        notes.append("Organizational-health signals are weak across governance, operations, or people metrics.")

    worst_case = summary.get("scenario_worst_case_probability")
    if isinstance(worst_case, (float, int)) and isinstance(final_prob, (float, int)) and float(worst_case) - float(final_prob) > 0.15:
        notes.append("Stress scenarios materially worsen risk, indicating limited downside resilience.")

    if not notes:
        notes.append("No dominant red flags were detected in the current scoring profile.")
    return notes


def _adjusted_risk_probability(
    base_prob: float,
    adjustments: dict[str, float | None],
    weights: dict[str, float],
) -> tuple[float, dict[str, float | None]]:
    penalty_total = (
        weights.get("benchmark_gap", 0.0) * float(adjustments.get("benchmark_gap") or 0.0)
        + weights.get("confidence_penalty", 0.0) * float(adjustments.get("confidence_penalty") or 0.0)
        + weights.get("donor_penalty", 0.0) * float(adjustments.get("donor_penalty") or 0.0)
        + weights.get("cashflow_penalty", 0.0) * float(adjustments.get("cashflow_penalty") or 0.0)
        + weights.get("compliance_penalty", 0.0) * float(adjustments.get("compliance_penalty") or 0.0)
        + weights.get("irs_penalty", 0.0) * float(adjustments.get("irs_penalty") or 0.0)
        + weights.get("altman_penalty", 0.0) * float(adjustments.get("altman_penalty") or 0.0)
    )

    relief_total = (
        weights.get("trend_relief", 0.0) * float(adjustments.get("trend_relief") or 0.0)
        + weights.get("charity_relief", 0.0) * float(adjustments.get("charity_relief") or 0.0)
    )

    adjusted = base_prob + penalty_total - relief_total
    adjusted = float(min(max(adjusted, 0.0), 1.0))

    components: dict[str, float | None] = {
        "base_probability": round(float(base_prob), 4),
        "penalty_total": round(float(penalty_total), 4),
        "relief_total": round(float(relief_total), 4),
        "benchmark_gap": round(float(adjustments.get("benchmark_gap") or 0.0), 4),
        "confidence_penalty": round(float(adjustments.get("confidence_penalty") or 0.0), 4),
        "donor_penalty": round(float(adjustments.get("donor_penalty") or 0.0), 4),
        "cashflow_penalty": round(float(adjustments.get("cashflow_penalty") or 0.0), 4),
        "compliance_penalty": round(float(adjustments.get("compliance_penalty") or 0.0), 4) if adjustments.get("compliance_penalty") is not None else None,
        "irs_penalty": round(float(adjustments.get("irs_penalty") or 0.0), 4) if adjustments.get("irs_penalty") is not None else None,
        "altman_penalty": round(float(adjustments.get("altman_penalty") or 0.0), 4) if adjustments.get("altman_penalty") is not None else None,
        "trend_relief": round(float(adjustments.get("trend_relief") or 0.0), 4),
        "charity_relief": round(float(adjustments.get("charity_relief") or 0.0), 4) if adjustments.get("charity_relief") is not None else None,
    }
    return round(adjusted, 4), components


def score_risk_adjustable(
    data: dict[str, Any],
    entity_type: str,
    horizon: int,
    continuity_low: float,
    continuity_moderate: float,
) -> dict[str, Any]:
    config = load_config()
    thresholds = _resolve_thresholds(config, entity_type)
    thresholds["continuity_low"] = continuity_low
    thresholds["continuity_moderate"] = continuity_moderate
    record = {
        "year": datetime.now().year,
        "expenses": data.get("expenses", data.get("total_expenses")),
        **data,
    }
    feature_frame = _feature_frame([record])
    model = _train_model(feature_frame, config, thresholds)
    latest = feature_frame.iloc[-1]
    risk_probability = float(model.predict_proba(feature_frame[FEATURE_COLUMNS])[-1][1])
    continuity_raw = round(float(latest["continuity_months_raw"]), 2) if pd.notna(latest["continuity_months_raw"]) else None
    continuity_months = round(float(latest["continuity_months"]), 2) if pd.notna(latest["continuity_months"]) else None
    weighted_score, normalized_features = _weighted_health_score(latest, config["weights"], config)
    explanation = _feature_contributions(model, latest)

    annual_expenses = float(latest.get("expenses") or 0.0)
    unrestricted_net_assets = float(latest.get("unrestricted_net_assets") or 0.0)
    monthly_burn = annual_expenses / 12 if annual_expenses > 0 else None

    return {
        "entity_type": entity_type,
        "horizon": horizon,
        "ContinuityRawMonths": continuity_raw,
        "ContinuityRiskScore": continuity_months,
        "OperatingMargin": round(float(latest["operating_margin"]), 4),
        "ProgramExpenseRatio": round(float(latest["program_expense_ratio"]), 4),
        "LiabilitiesToAssets": round(float(latest["liabilities_to_assets"]), 4),
        "RevenueVolatility": round(float(latest["revenue_volatility"]), 4),
        "ModelRiskProbability": round(risk_probability, 4),
        "WeightedHealthScore": weighted_score,
        "NormalizedFeatures": normalized_features,
        "ContinuityDescriptor": _descriptor(continuity_raw, risk_probability, thresholds),
        "ContinuityInputs": {
            "unrestricted_net_assets": round(unrestricted_net_assets, 2),
            "annual_expenses": round(annual_expenses, 2),
            "monthly_burn": round(monthly_burn, 2) if monthly_burn is not None else None,
        },
        **explanation,
        "scored_at": datetime.now().isoformat(),
    }


def run(
    input_file: str,
    entity_type: str,
    horizon: int,
    out_file: str,
    config_path: str | None = None,
) -> dict[str, Any]:
    # Layer 1: Raw Features
    payload = read_json(input_file)
    feature_frame = _feature_frame(payload.get("records", []))
    config = load_config(config_path)

    # Layer 2: Base Risk Model
    weights = _weights_for_entity(config, entity_type)
    thresholds = _resolve_thresholds(config, entity_type)
    model = _train_model(feature_frame, config, thresholds)
    feature_frame["base_risk_probability"] = model.predict_proba(feature_frame[FEATURE_COLUMNS])[:, 1]

    latest = feature_frame.iloc[-1]
    continuity_raw = round(float(latest["continuity_months_raw"]), 2) if pd.notna(latest["continuity_months_raw"]) else None
    continuity_months = round(float(latest["continuity_months"]), 2) if pd.notna(latest["continuity_months"]) else None
    weighted_score, normalized_features = _weighted_health_score(latest, weights, config, entity_type)
    time_weighted_score, time_weighted_features = _time_weighted_health_score(feature_frame, weights, config, entity_type)
    explanation = _feature_contributions(model, latest)
    peer_benchmark = _peer_benchmark(latest, config, feature_frame, payload.get("metadata", {}))
    trend_metrics = _trend_stability(feature_frame)
    donor_quality = _donor_and_revenue_quality(latest)
    cashflow = _cashflow_durability(latest)
    financial_depth = _financial_depth_metrics(latest)
    compensation = _compensation_burden(latest)
    organizational_health = _organizational_health(payload.get("metadata", {}))
    altman = _altman_zscore_nonprofit(latest, config)
    data_confidence = _data_confidence(payload)
    compliance_score = _resolve_compliance_score(payload)
    external_signals = _resolve_external_signal_scores(payload)
    base_probability_raw = float(latest["base_risk_probability"])
    calibrated_probability = _apply_calibration_curve(base_probability_raw, config)
    base_probability = calibrated_probability if calibrated_probability is not None else base_probability_raw

    # Layer 3: Adjusted Risk Score (clean penalties/rewards)
    adjustments: dict[str, float | None] = {
        "benchmark_gap": 1.0 - (peer_benchmark["peer_benchmark_score"] if peer_benchmark["peer_benchmark_score"] is not None else 0.5),
        "confidence_penalty": 1.0 - data_confidence,
        "compliance_penalty": (1.0 - compliance_score) if compliance_score is not None else None,
        "donor_penalty": 1.0 - float(donor_quality["donor_concentration_score"] or 0.0),
        "cashflow_penalty": 1.0 - float(cashflow["cashflow_durability_score"] or 0.0),
        "irs_penalty": external_signals["irs_teos_status_risk"],
        "altman_penalty": _altman_penalty(str(altman.get("altman_zone") or "unknown")),
        "charity_relief": external_signals["charity_navigator_score"],
        "trend_relief": max(min(trend_metrics["trend_stability_score"], 1.0), -1.0),
    }
    preset_name, preset_weights = _resolve_scoring_preset(config)
    final_risk_probability, composite_components = _adjusted_risk_probability(
        base_prob=base_probability,
        adjustments=adjustments,
        weights=preset_weights,
    )

    uncertainty = _probability_uncertainty(model, latest, feature_frame, config)

    # Layer 4: Final Risk Index
    final_index = _final_index_blend(
        probability=final_risk_probability,
        weighted_health_score=weighted_score,
        config=config,
        entity_type=entity_type,
    )
    annual_expenses = float(latest.get("expenses") or 0.0)
    unrestricted_net_assets = float(latest.get("unrestricted_net_assets") or 0.0)
    monthly_burn = annual_expenses / 12 if annual_expenses > 0 else None
    standard_metrics = _standard_grant_metrics(
        feature_frame=feature_frame,
        latest_row=latest,
        continuity_raw=continuity_raw,
        data_confidence_score=data_confidence,
        compliance_score=compliance_score,
        risk_probability=final_risk_probability,
        donor_top_share=donor_quality["donor_top_share"],
        months_cash_on_hand=cashflow["months_cash_on_hand"],
        altman=altman,
    )
    grant_recommendation = _grant_recommendation(
        risk_probability=final_risk_probability,
        continuity_months=continuity_raw,
        operating_margin=float(latest["operating_margin"]),
        liabilities_to_assets=float(latest["liabilities_to_assets"]),
        data_confidence_score=data_confidence,
        altman_zone=str(altman.get("altman_zone") or "unknown"),
        organizational_health_score=organizational_health.get("organizational_health_score"),
        legal_reputation_risk_score=organizational_health.get("legal_reputation_risk_score"),
    )
    scenario_testing = _scenario_stress_tests(
        latest_row=latest,
        baseline_probability=final_risk_probability,
        donor_top_share=donor_quality.get("donor_top_share"),
        config=config,
    )

    history_frame = feature_frame[
        [
            "year",
            "continuity_months_raw",
            "continuity_months",
            "operating_margin",
            "program_expense_ratio",
            "liabilities_to_assets",
            "revenue_volatility",
            "base_risk_probability",
            "executive_compensation",
            "staff_salaries",
            "admin_salaries",
            "total_salaries",
            "executive_salary_ratio",
            "staff_salary_ratio",
            "admin_salary_ratio",
            "salaries_to_expense_ratio",
        ]
    ].copy()
    history_frame = history_frame.rename(
        columns={
            "continuity_months_raw": "continuity_months",
            "continuity_months": "continuity_months_model",
            "base_risk_probability": "risk_probability",
        }
    )

    summary = {
        # Primary, explainable outputs
        "final_risk_probability": final_risk_probability,
        "final_risk_index": final_index["final_risk_index"],
        "descriptor": _descriptor(continuity_raw, final_risk_probability, thresholds),
        "weighted_health_score": weighted_score,
        "key_drivers": explanation["top_drivers"],
        "grant_recommendation": grant_recommendation,
        "standard_grant_metrics": standard_metrics,

        # Compatibility + existing UI fields
        "continuity_months": continuity_raw,
        "continuity_months_model": continuity_months,
        "operating_margin": round(float(latest["operating_margin"]), 4),
        "program_expense_ratio": round(float(latest["program_expense_ratio"]), 4),
        "liabilities_to_assets": round(float(latest["liabilities_to_assets"]), 4),
        "revenue_volatility": round(float(latest["revenue_volatility"]), 4),
        "risk_probability": round(base_probability_raw, 4),
        "calibrated_risk_probability": calibrated_probability,
        "composite_risk_probability": final_risk_probability,
        "time_weighted_health_score": time_weighted_score,
        "normalized_features": normalized_features,
        "time_weighted_features": time_weighted_features,
        "peer_benchmark_score": peer_benchmark["peer_benchmark_score"],
        "peer_percentiles": peer_benchmark["peer_percentiles"],
        "peer_z_scores": peer_benchmark["peer_z_scores"],
        "peer_percentile_score": peer_benchmark["peer_percentile_score"],
        "peer_zscore_score": peer_benchmark["peer_zscore_score"],
        "peer_benchmark_mode": peer_benchmark["peer_benchmark_mode"],
        "peer_group_filters": peer_benchmark["peer_group_filters"],
        "peer_group_rows": peer_benchmark["peer_group_rows"],
        "data_confidence_score": data_confidence,
        "compliance_score": round(compliance_score, 4) if compliance_score is not None else None,
        "irs_teos_status_risk": external_signals["irs_teos_status_risk"],
        "charity_navigator_score": external_signals["charity_navigator_score"],
        "trend_metrics": trend_metrics,
        "composite_components": composite_components,
        "scoring_preset": preset_name,
        "altman": altman,
        **uncertainty,
        **final_index,
        **donor_quality,
        **cashflow,
        **financial_depth,
        **compensation,
        **organizational_health,
        **scenario_testing,
        "continuity_inputs": {
            "unrestricted_net_assets": round(unrestricted_net_assets, 2),
            "annual_expenses": round(annual_expenses, 2),
            "monthly_burn": round(monthly_burn, 2) if monthly_burn is not None else None,
        },
        **explanation,
    }
    summary["plain_language_explanation"] = _plain_language_explanation(summary)

    details = {
        "layer_1_raw_features": {
            "feature_columns": FEATURE_COLUMNS,
            "record_count": int(len(feature_frame)),
        },
        "layer_2_base_model": {
            "base_risk_probability": round(base_probability_raw, 4),
            "calibrated_risk_probability": calibrated_probability,
            "model_type": "logistic_regression",
            "random_state": int(config.get("model", {}).get("random_state", 42)),
        },
        "layer_3_adjustments": {
            "preset": preset_name,
            "weights": {k: round(float(v), 4) for k, v in preset_weights.items()},
            "signals": {k: (round(float(v), 4) if isinstance(v, (float, int)) else None) for k, v in adjustments.items()},
            "components": composite_components,
        },
        "layer_4_final": {
            "final_risk_probability": final_risk_probability,
            "final_risk_index": final_index["final_risk_index"],
            "descriptor": summary["descriptor"],
            "altman_z_score": altman.get("altman_z_score"),
            "altman_zone": altman.get("altman_zone"),
        },
    }

    result = {
        "entity_type": entity_type,
        "horizon": horizon,
        "metadata": payload.get("metadata", {}),
        "summary": summary,
        "details": details,
        "history": history_frame.round(4).replace({np.nan: None}).to_dict(orient="records"),
        "scored_at": datetime.now().isoformat(),
    }
    write_json(out_file, result)
    logging.info("Scored %s into %s", entity_type, out_file)
    return result

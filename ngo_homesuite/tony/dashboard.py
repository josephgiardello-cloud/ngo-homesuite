import json
import os
import tempfile
from pathlib import Path

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from flask import Flask, render_template, request
from werkzeug.utils import secure_filename

from . import calibration, compliance, ingest, score
from .utils import parse_years
from .utils import read_json


BASELINES_DIR = Path(__file__).resolve().parent.parent / "baselines"
DEFAULT_CALIBRATION_FILE = BASELINES_DIR / "calibration_external_benchmark.csv"
DEFAULT_COMPLIANCE_FILE = BASELINES_DIR / "compliance_external_baseline_profile.json"


def _pretty_label(value: str) -> str:
    return value.replace("_", " ").strip().title()


def _chart_layout(title: str, xaxis_title: str = "Year", yaxis_title: str = "Value") -> dict:
    return {
        "title": {
            "text": title,
            "x": 0.01,
            "xanchor": "left",
            "font": {"size": 17, "family": "Manrope, sans-serif", "color": "#12343b"},
        },
        "template": "plotly_white",
        "paper_bgcolor": "rgba(0,0,0,0)",
        "plot_bgcolor": "#ffffff",
        "margin": {"l": 48, "r": 20, "t": 58, "b": 48},
        "font": {"family": "Manrope, sans-serif", "size": 12, "color": "#1f2a37"},
        "colorway": ["#0b6e4f", "#12749a", "#f59e0b", "#ef4444", "#334155"],
        "xaxis": {
            "title": xaxis_title,
            "showgrid": False,
            "linecolor": "#bfc7d1",
            "tickcolor": "#bfc7d1",
            "tickmode": "linear",
            "dtick": 1,
            "automargin": True,
            "zeroline": False,
        },
        "yaxis": {
            "title": yaxis_title,
            "gridcolor": "#e6ebf1",
            "linecolor": "#bfc7d1",
            "tickcolor": "#bfc7d1",
            "automargin": True,
            "zeroline": False,
        },
        "legend": {
            "orientation": "v",
            "yanchor": "top",
            "y": 1.0,
            "xanchor": "left",
            "x": 1.02,
            "bgcolor": "rgba(255,255,255,0.65)",
            "bordercolor": "#dbe2ea",
            "borderwidth": 1,
            "font": {"size": 11},
        },
        "hovermode": "x unified",
        "hoverlabel": {"bgcolor": "#0f172a", "font": {"color": "#f8fafc"}},
    }


def _build_charts(payload: dict) -> dict[str, str]:
    history = pd.DataFrame(payload.get("history", []))
    if history.empty:
        return {"trend": "{}", "mix": "{}", "risk": "{}"}

    continuity_column = "continuity_months"
    if "continuity_months" not in history.columns and "continuity_months_model" in history.columns:
        continuity_column = "continuity_months_model"

    trend = px.line(
        history,
        x="year",
        y=[continuity_column, "operating_margin", "program_expense_ratio"],
        markers=True,
        title="Financial Resilience Trends",
    )
    mix = px.bar(
        history,
        x="year",
        y=["liabilities_to_assets", "revenue_volatility"],
        barmode="group",
        title="Balance Sheet Pressure",
    )
    risk = px.area(history, x="year", y="risk_probability", title="Model Risk Probability")

    trend_names = {
        continuity_column: "Continuity Months",
        "operating_margin": "Operating Margin",
        "program_expense_ratio": "Program Expense Ratio",
    }
    mix_names = {
        "liabilities_to_assets": "Liabilities / Assets",
        "revenue_volatility": "Revenue Volatility",
    }

    trend.for_each_trace(
        lambda trace: trace.update(
            name=trend_names.get(trace.name, _pretty_label(trace.name)),
            hovertemplate="%{x}: %{y:.3f}<extra>%{fullData.name}</extra>",
        )
    )
    mix.for_each_trace(
        lambda trace: trace.update(
            name=mix_names.get(trace.name, _pretty_label(trace.name)),
            hovertemplate="%{x}: %{y:.3f}<extra>%{fullData.name}</extra>",
        )
    )
    risk.for_each_trace(lambda trace: trace.update(name="Risk Probability", hovertemplate="%{x}: %{y:.1%}<extra></extra>"))

    trend.update_traces(line={"width": 3}, marker={"size": 7})
    mix.update_traces(marker_line_color="#f8fafc", marker_line_width=1)
    risk.update_traces(line={"width": 2.5}, fillcolor="rgba(15, 118, 110, 0.22)")

    trend.update_layout(_chart_layout("Financial Resilience Trends", yaxis_title="Normalized financial metrics"))
    mix.update_layout(_chart_layout("Balance Sheet Pressure", yaxis_title="Risk pressure metrics"))
    risk.update_layout(_chart_layout("Model Risk Probability", yaxis_title="Probability"))
    risk.update_yaxes(range=[0, 1], tickformat=".0%")
    risk.add_hrect(y0=0.0, y1=0.35, fillcolor="rgba(16,185,129,0.12)", line_width=0)
    risk.add_hrect(y0=0.35, y1=0.6, fillcolor="rgba(245,158,11,0.12)", line_width=0)
    risk.add_hrect(y0=0.6, y1=1.0, fillcolor="rgba(239,68,68,0.1)", line_width=0)

    return {
        "trend": trend.to_json(),
        "mix": mix.to_json(),
        "risk": risk.to_json(),
    }


def _build_calibration_chart(calibration_result: dict | None) -> str:
    if not calibration_result:
        return "{}"

    curve = calibration_result.get("curve", [])
    if not curve:
        return "{}"

    frame = pd.DataFrame(curve)
    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=frame["mean_pred"],
            y=frame["observed_rate"],
            mode="markers+lines",
            name="Observed",
            hovertemplate="Predicted: %{x:.3f}<br>Observed: %{y:.3f}<extra></extra>",
        )
    )
    fig.add_trace(
        go.Scatter(
            x=[0, 1],
            y=[0, 1],
            mode="lines",
            name="Perfect Calibration",
            line={"dash": "dash"},
            hovertemplate="Predicted: %{x:.3f}<br>Observed: %{y:.3f}<extra></extra>",
        )
    )
    fig.update_layout(
        _chart_layout("Calibration Curve", xaxis_title="Predicted probability", yaxis_title="Observed outcome rate")
    )
    return fig.to_json()


def _build_compliance_chart(compliance_result: dict | None) -> str:
    if not compliance_result:
        return "{}"

    domain_summary = compliance_result.get("domain_summary", {})
    if not domain_summary:
        return "{}"

    frame = pd.DataFrame(
        [
            {"domain": _pretty_label(domain), "met": values.get("met", 0), "missing": values.get("missing", 0)}
            for domain, values in domain_summary.items()
        ]
    )
    fig = go.Figure()
    fig.add_trace(go.Bar(name="Met", x=frame["domain"], y=frame["met"]))
    fig.add_trace(go.Bar(name="Missing", x=frame["domain"], y=frame["missing"]))
    fig.update_layout(
        barmode="group",
        **_chart_layout("Compliance Controls by Domain", xaxis_title="Domain", yaxis_title="Control count"),
    )
    return fig.to_json()


def _empty_payload() -> dict:
    return {
        "summary": {},
        "metadata": {},
        "history": [],
    }


def _load_payload(data_path: str) -> dict:
    try:
        payload = read_json(data_path)
        if isinstance(payload, dict):
            return payload
    except Exception:
        pass
    return _empty_payload()


def create_app(data_path: str) -> Flask:
    app = Flask(__name__, template_folder="templates")

    @app.route("/", methods=["GET", "POST"])
    def index() -> str:
        payload = _load_payload(data_path)
        query_mode = (request.args.get("mode") or "").strip().lower()
        initial_mode = query_mode if query_mode in {"basic", "full"} else "basic"
        error_message = ""
        status_message = ""
        calibration_result: dict | None = None
        compliance_result: dict | None = None
        form_state = {
            "entity_type": "nonprofit",
            "horizon": "12",
            "ein": "",
            "years": "",
            "view_mode": initial_mode,
        }

        if request.method == "POST":
            form_state["entity_type"] = request.form.get("entity_type", "nonprofit")
            form_state["horizon"] = request.form.get("horizon", form_state["horizon"])
            form_state["ein"] = request.form.get("ein", "")
            form_state["years"] = request.form.get("years", "")
            posted_mode = (request.form.get("view_mode") or "").strip().lower()
            form_state["view_mode"] = posted_mode if posted_mode in {"basic", "full"} else form_state["view_mode"]
            action = request.form.get("action", "").strip().lower()

            try:
                entity_type = (form_state["entity_type"] or "nonprofit").strip() or "nonprofit"
                horizon = int(form_state["horizon"] or "12")
                score_horizon = horizon
                ingest_payload: dict | None = None

                with tempfile.TemporaryDirectory(prefix="tony_dashboard_") as work_dir:
                    normalized_path = os.path.join(work_dir, "normalized.json")
                    scored_path = os.path.join(work_dir, "scored.json")

                    if action == "upload":
                        uploaded = request.files.get("data_file")
                        if not uploaded or not uploaded.filename:
                            raise ValueError("Select a CSV, Excel, or PDF file to upload.")

                        filename = secure_filename(uploaded.filename) or "uploaded_data.csv"
                        source_path = os.path.join(work_dir, filename)
                        uploaded.save(source_path)
                        ingest_payload = ingest.run(source_path, None, [], normalized_path)
                    elif action == "propublica":
                        ein = (form_state["ein"] or "").strip()
                        if not ein:
                            raise ValueError("EIN is required for ProPublica lookup.")

                        years_raw = (form_state["years"] or "").strip()
                        years = parse_years(years_raw) if years_raw else []
                        ingest_payload = ingest.run("propublica", ein, years, normalized_path)
                        filing_years = ingest_payload.get("metadata", {}).get("years", []) if ingest_payload else []
                        if isinstance(filing_years, list) and filing_years:
                            score_horizon = len(filing_years)
                    elif action == "calibrate":
                        uploaded = request.files.get("calibration_file")
                        if uploaded and uploaded.filename:
                            filename = secure_filename(uploaded.filename) or "calibration.csv"
                            source_path = os.path.join(work_dir, filename)
                            uploaded.save(source_path)
                        elif DEFAULT_CALIBRATION_FILE.exists():
                            source_path = str(DEFAULT_CALIBRATION_FILE)
                        else:
                            raise ValueError("Select a calibration CSV with risk_probability and outcome columns.")

                        calibration_result = calibration.run(source_path, os.path.join(work_dir, "calibration.json"))
                        status_message = "Calibration completed with external benchmark data."
                    elif action == "compliance":
                        uploaded = request.files.get("compliance_file")
                        if uploaded and uploaded.filename:
                            filename = secure_filename(uploaded.filename) or "compliance_profile.json"
                            source_path = os.path.join(work_dir, filename)
                            uploaded.save(source_path)
                        elif DEFAULT_COMPLIANCE_FILE.exists():
                            source_path = str(DEFAULT_COMPLIANCE_FILE)
                        else:
                            raise ValueError("Select a compliance profile JSON file.")

                        compliance_result = compliance.run(source_path, os.path.join(work_dir, "compliance_report.json"))
                        status_message = "Compliance gap assessment completed."
                    else:
                        raise ValueError("Unsupported dashboard action.")

                    if action in {"upload", "propublica"}:
                        payload = score.run(normalized_path, entity_type, score_horizon, scored_path)
                        status_message = "Dashboard updated with fresh scoring output."
            except Exception as exc:
                error_message = str(exc)

        charts = _build_charts(payload)
        calibration_chart = _build_calibration_chart(calibration_result)
        compliance_chart = _build_compliance_chart(compliance_result)
        return render_template(
            "dashboard.html",
            summary=payload.get("summary", {}),
            metadata=payload.get("metadata", {}),
            history=json.dumps(payload.get("history", [])),
            charts=charts,
            calibration_result=calibration_result,
            calibration_chart=calibration_chart,
            compliance_result=compliance_result,
            compliance_chart=compliance_chart,
            error_message=error_message,
            status_message=status_message,
            form_state=form_state,
            view_mode=form_state.get("view_mode", "basic"),
        )

    return app


def main(data_path: str, host: str = "127.0.0.1", port: int = 8000) -> None:
    app = create_app(data_path)
    app.run(host=host, port=port, debug=True)
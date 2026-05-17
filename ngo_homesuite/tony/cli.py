import argparse
import json
import logging
from . import calibration, compliance, evaluation, ingest, report, score
from .config import DEFAULT_CONFIG
from .dashboard import main as dashboard_main
from .utils import parse_years
import os
from datetime import datetime

# Dual logging: console + file
log_dir = os.path.join(os.path.dirname(__file__), "..", "logs")
os.makedirs(log_dir, exist_ok=True)
log_file = os.path.join(log_dir, f"tony_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(log_file, encoding="utf-8")
    ]
)

def main() -> None:
    parser = argparse.ArgumentParser(description="TONY Diagnostic Tool")
    subparsers = parser.add_subparsers(dest="command")

    p_ingest = subparsers.add_parser("ingest")
    p_ingest.add_argument("--source", required=True, help="Path to CSV/Excel/PDF or the literal 'propublica'")
    p_ingest.add_argument("--ein", help="Required for source=propublica unless PROPUBLICA_EIN or TONY_EIN is set")
    p_ingest.add_argument("--years", default="", type=parse_years)
    p_ingest.add_argument("--config", help="Path to config JSON (falls back to TONY_CONFIG env var)")
    p_ingest.add_argument("--out", required=True)

    p_score = subparsers.add_parser("score")
    p_score.add_argument("--input", required=True)
    p_score.add_argument("--entity-type", default="nonprofit")
    p_score.add_argument("--horizon", type=int, default=10)
    p_score.add_argument("--config", help="Path to config JSON (falls back to TONY_CONFIG env var)")
    p_score.add_argument("--out", required=True)

    p_report = subparsers.add_parser("report")
    p_report.add_argument("--input", required=True)
    p_report.add_argument("--format", required=True, choices=["md", "html", "json"])
    p_report.add_argument("--out")

    p_dashboard = subparsers.add_parser("dashboard")
    p_dashboard.add_argument("--input", required=True)
    p_dashboard.add_argument("--host", default="127.0.0.1")
    p_dashboard.add_argument("--port", type=int, default=8000)

    p_calibrate = subparsers.add_parser("calibrate")
    p_calibrate.add_argument("--input", required=True, help="CSV with risk_probability and outcome columns")
    p_calibrate.add_argument("--bins", type=int, default=10)
    p_calibrate.add_argument("--out", required=True)

    p_evaluate = subparsers.add_parser("evaluate")
    p_evaluate.add_argument("--input", required=True, help="CSV with risk_probability and outcome columns")
    p_evaluate.add_argument("--bins", type=int, default=10)
    p_evaluate.add_argument("--method", choices=["holdout", "kfold"], default="holdout")
    p_evaluate.add_argument("--test-size", type=float, default=0.3)
    p_evaluate.add_argument("--folds", type=int, default=5)
    p_evaluate.add_argument("--random-state", type=int, default=42)
    p_evaluate.add_argument("--no-stratified", action="store_true")
    p_evaluate.add_argument("--out", required=True)

    p_compliance = subparsers.add_parser("compliance-audit")
    p_compliance.add_argument("--input", required=True, help="JSON profile for governance/compliance controls")
    p_compliance.add_argument("--out", required=True)

    subparsers.add_parser("print-config")

    args = parser.parse_args()

    if args.command == "ingest":
        resolved_config = args.config or os.getenv("TONY_CONFIG")
        resolved_ein = args.ein
        if args.source.strip().lower() == "propublica" and not resolved_ein:
            resolved_ein = os.getenv("PROPUBLICA_EIN") or os.getenv("TONY_EIN")
        ingest.run(args.source, resolved_ein, args.years, args.out, resolved_config)
    elif args.command == "score":
        resolved_config = args.config or os.getenv("TONY_CONFIG")
        score.run(args.input, args.entity_type, args.horizon, args.out, resolved_config)
    elif args.command == "report":
        report.run(args.input, args.format, args.out)
    elif args.command == "dashboard":
        dashboard_main(args.input, args.host, args.port)
    elif args.command == "calibrate":
        calibration.run(args.input, args.out, bins=args.bins)
    elif args.command == "evaluate":
        evaluation.run(
            args.input,
            args.out,
            bins=args.bins,
            method=args.method,
            test_size=args.test_size,
            folds=args.folds,
            random_state=args.random_state,
            stratified=not args.no_stratified,
        )
    elif args.command == "compliance-audit":
        compliance.run(args.input, args.out)
    elif args.command == "print-config":
        print(json.dumps(DEFAULT_CONFIG, indent=2))
    else:
        parser.print_help()



if __name__ == "__main__":
    main()

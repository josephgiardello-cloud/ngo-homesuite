import json
from pathlib import Path

import pandas as pd

from .utils import read_json


def _markdown_report(result: dict) -> str:
    summary = result["summary"]
    history = pd.DataFrame(result["history"])
    lines = [
        "# TONY Risk Report",
        "",
        f"- Entity type: {result['entity_type']}",
        f"- Horizon: {result['horizon']} months",
        f"- Continuity: {summary['continuity_months']} months",
        f"- Risk probability: {summary['risk_probability']}",
        f"- Descriptor: {summary['descriptor']}",
        "",
        "## Historical features",
        "",
        history.to_markdown(index=False),
        "",
    ]
    return "\n".join(lines)


def _html_report(result: dict) -> str:
    summary = result["summary"]
    history = pd.DataFrame(result["history"])
    return f"""
<!doctype html>
<html lang=\"en\">
<head>
  <meta charset=\"utf-8\" />
  <title>TONY Risk Report</title>
  <style>
    body {{ font-family: Georgia, serif; margin: 2rem auto; max-width: 960px; color: #1e293b; }}
    .hero {{ background: linear-gradient(120deg, #f4f1de, #d9ed92); padding: 1.5rem; border-radius: 16px; }}
    table {{ border-collapse: collapse; width: 100%; margin-top: 1rem; }}
    th, td {{ padding: 0.75rem; border-bottom: 1px solid #cbd5e1; text-align: left; }}
  </style>
</head>
<body>
  <section class=\"hero\">
    <h1>TONY Risk Report</h1>
    <p><strong>Descriptor:</strong> {summary['descriptor']}</p>
    <p><strong>Continuity:</strong> {summary['continuity_months']} months</p>
    <p><strong>Risk probability:</strong> {summary['risk_probability']}</p>
  </section>
  {history.to_html(index=False)}
</body>
</html>
""".strip()


def run(input_file: str, fmt: str, out_file: str | None = None) -> str:
    result = read_json(input_file)
    if fmt == "json":
        content = json.dumps(result, indent=2)
    elif fmt == "html":
        content = _html_report(result)
    else:
        content = _markdown_report(result)

    if out_file:
        Path(out_file).write_text(content, encoding="utf-8")
    else:
        print(content)
    return content

import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

import pandas as pd
import requests
from PyPDF2 import PdfReader

from .config import load_config
from .utils import coalesce_number, resolve_path, write_json


BASELINES_DIR = Path(__file__).resolve().parent.parent / "baselines"
EXTERNAL_BASELINE_FILE = BASELINES_DIR / "external_calibration_baseline.csv"


def _derive_total_salaries(
    executive_compensation: float | None,
    staff_salaries: float | None,
    admin_salaries: float | None,
    explicit_total: float | None,
) -> float | None:
    if explicit_total is not None:
        return explicit_total
    components = [value for value in [executive_compensation, staff_salaries, admin_salaries] if value is not None]
    if len(components) >= 2:
        return float(sum(components))
    return None


def _find_column(frame: pd.DataFrame, aliases: Iterable[str]) -> str | None:
    normalized = {column.strip().lower(): column for column in frame.columns}
    for alias in aliases:
        column = normalized.get(alias.lower())
        if column:
            return column
    return None


def _normalize_frame(frame: pd.DataFrame, config: dict[str, Any], source_name: str, ein: str | None) -> list[dict[str, Any]]:
    aliases = config["column_aliases"]
    columns = {key: _find_column(frame, values) for key, values in aliases.items()}
    year_column = columns["year"]
    if not year_column:
        raise ValueError("Input data must contain a year column.")

    normalized_records: list[dict[str, Any]] = []
    working = frame.copy().where(pd.notnull(frame), None)
    for record in working.to_dict(orient="records"):
        year = int(record[year_column])
        revenue = coalesce_number(record.get(columns["revenue"])) or 0.0
        expenses = coalesce_number(record.get(columns["expenses"])) or 0.0
        assets = coalesce_number(record.get(columns["assets"]))
        liabilities = coalesce_number(record.get(columns["liabilities"]))
        net_assets = coalesce_number(record.get(columns["net_assets"]))
        if net_assets is None and assets is not None and liabilities is not None:
            net_assets = assets - liabilities
        program_expenses = coalesce_number(record.get(columns["program_expenses"]))
        executive_compensation = coalesce_number(record.get(columns.get("executive_compensation")))
        staff_salaries = coalesce_number(record.get(columns.get("staff_salaries")))
        admin_salaries = coalesce_number(record.get(columns.get("admin_salaries")))
        total_salaries = _derive_total_salaries(
            executive_compensation,
            staff_salaries,
            admin_salaries,
            coalesce_number(record.get(columns.get("total_salaries"))),
        )

        executive_salary_ratio = (executive_compensation / expenses) if executive_compensation is not None and expenses > 0 else None
        staff_salary_ratio = (staff_salaries / expenses) if staff_salaries is not None and expenses > 0 else None
        admin_salary_ratio = (admin_salaries / expenses) if admin_salaries is not None and expenses > 0 else None
        salaries_to_expense_ratio = (total_salaries / expenses) if total_salaries is not None and expenses > 0 else None

        normalized_records.append(
            {
                "year": year,
                "revenue": revenue,
                "expenses": expenses,
                "assets": assets,
                "liabilities": liabilities,
                "unrestricted_net_assets": net_assets,
                "program_expenses": program_expenses,
                "executive_compensation": executive_compensation,
                "staff_salaries": staff_salaries,
                "admin_salaries": admin_salaries,
                "total_salaries": total_salaries,
                "executive_salary_ratio": round(executive_salary_ratio, 6) if executive_salary_ratio is not None else None,
                "staff_salary_ratio": round(staff_salary_ratio, 6) if staff_salary_ratio is not None else None,
                "admin_salary_ratio": round(admin_salary_ratio, 6) if admin_salary_ratio is not None else None,
                "salaries_to_expense_ratio": round(salaries_to_expense_ratio, 6) if salaries_to_expense_ratio is not None else None,
                "source": source_name,
                "ein": ein,
            }
        )

    return sorted(normalized_records, key=lambda item: item["year"])


def _load_table_file(source: str) -> pd.DataFrame:
    path = resolve_path(source)
    suffix = Path(path).suffix.lower()
    if suffix == ".csv":
        return pd.read_csv(path)
    if suffix in {".xlsx", ".xls"}:
        return pd.read_excel(path)
    raise ValueError(f"Unsupported tabular file type: {suffix}")


def _load_pdf_tables(source: str) -> pd.DataFrame:
    path = resolve_path(source)
    frames: list[pd.DataFrame] = []

    try:
        import camelot  # type: ignore

        tables = camelot.read_pdf(path, pages="all")
        frames.extend(table.df for table in tables if not table.df.empty)
    except Exception:
        pass

    if not frames:
        try:
            import tabula  # type: ignore

            tabula_frames = tabula.read_pdf(path, pages="all", multiple_tables=True)
            frames.extend(frame for frame in tabula_frames if not frame.empty)
        except Exception:
            pass

    if not frames:
        reader = PdfReader(path)
        text = "\n".join(page.extract_text() or "" for page in reader.pages)
        raise ValueError(
            "No tabular PDF parser is installed or no tables were detected. "
            f"Extracted text length: {len(text)}. Install camelot-py or tabula-py to parse filing PDFs."
        )

    return pd.concat(frames, ignore_index=True)


def _first_numeric(mapping: dict[str, Any], keys: list[str]) -> float | None:
    for key in keys:
        if key in mapping:
            value = coalesce_number(mapping.get(key))
            if value is not None:
                return value
    return None


def _pdf_text_key_metrics(path: str) -> dict[str, float | int | None]:
    reader = PdfReader(path)
    text = "\n".join(page.extract_text() or "" for page in reader.pages)

    if len(text.strip()) < 200:
        # Optional OCR path for scanned/image-only PDFs.
        try:
            from pdf2image import convert_from_path  # type: ignore
            import pytesseract  # type: ignore

            images = convert_from_path(path, dpi=250)
            ocr_text = []
            for image in images:
                ocr_text.append(pytesseract.image_to_string(image))
            text = "\n".join(ocr_text)
        except Exception:
            pass

    compact = re.sub(r"\s+", " ", text)

    def find_number(patterns: list[str]) -> float | None:
        for pattern in patterns:
            match = re.search(pattern, compact, flags=re.IGNORECASE)
            if not match:
                continue
            raw = match.group(1).replace(",", "").replace("$", "")
            parsed = coalesce_number(raw)
            if parsed is not None:
                return parsed
        return None

    def find_year() -> int | None:
        match = re.search(r"tax\s+year\s+(\d{4})", compact, flags=re.IGNORECASE)
        if match:
            return int(match.group(1))
        years = re.findall(r"\b(20\d{2}|19\d{2})\b", compact)
        if years:
            return max(int(y) for y in years)
        return None

    return {
        "year": find_year(),
        "revenue": find_number([r"total\s+revenue[^\d\-]*([\(\)\$\-\d,\.]+)", r"revenue\s+less\s+expenses[^\d\-]*([\(\)\$\-\d,\.]+)"]),
        "expenses": find_number([r"total\s+expenses[^\d\-]*([\(\)\$\-\d,\.]+)"]),
        "assets": find_number([r"total\s+assets[^\d\-]*([\(\)\$\-\d,\.]+)"]),
        "liabilities": find_number([r"total\s+liabilities[^\d\-]*([\(\)\$\-\d,\.]+)"]),
        "unrestricted_net_assets": find_number([r"net\s+assets\s+or\s+fund\s+balances[^\d\-]*([\(\)\$\-\d,\.]+)", r"unrestricted\s+net\s+assets[^\d\-]*([\(\)\$\-\d,\.]+)"]),
        "executive_compensation": find_number([r"compensation\s+of\s+current\s+officers[^\d\-]*([\(\)\$\-\d,\.]+)", r"officers\s+directors\s+trustees\s+key\s+employees\s+compensation[^\d\-]*([\(\)\$\-\d,\.]+)"]),
        "staff_salaries": find_number([r"salaries\s+and\s+wages[^\d\-]*([\(\)\$\-\d,\.]+)", r"other\s+employee\s+benefits[^\d\-]*([\(\)\$\-\d,\.]+)"]),
        "admin_salaries": find_number([r"management\s+and\s+general[^\d\-]*([\(\)\$\-\d,\.]+)", r"administrative[^\d\-]*([\(\)\$\-\d,\.]+)"]),
    }


def _normalize_pdf_with_fallback(source: str, config: dict[str, Any], ein: str | None) -> list[dict[str, Any]]:
    try:
        frame = _load_pdf_tables(source)
        return _normalize_frame(frame, config, source, ein)
    except Exception:
        metrics = _pdf_text_key_metrics(source)
        if not metrics.get("year"):
            raise ValueError(
                "Could not parse structured 990 metrics from PDF text. Install camelot-py or tabula-py for table extraction."
            )

        expenses = float(metrics.get("expenses") or 0.0)
        exec_comp = metrics.get("executive_compensation")
        staff_salaries = metrics.get("staff_salaries")
        admin_salaries = metrics.get("admin_salaries")
        total_salaries = sum(v or 0.0 for v in [exec_comp, staff_salaries, admin_salaries]) or None
        salaries_to_expense_ratio = (total_salaries / expenses) if total_salaries is not None and expenses > 0 else None

        return [
            {
                "year": int(metrics["year"]),
                "revenue": float(metrics.get("revenue") or 0.0),
                "expenses": expenses,
                "assets": metrics.get("assets"),
                "liabilities": metrics.get("liabilities"),
                "unrestricted_net_assets": metrics.get("unrestricted_net_assets"),
                "program_expenses": None,
                "executive_compensation": exec_comp,
                "staff_salaries": staff_salaries,
                "admin_salaries": admin_salaries,
                "total_salaries": total_salaries,
                "executive_salary_ratio": (exec_comp / expenses) if exec_comp is not None and expenses > 0 else None,
                "staff_salary_ratio": (staff_salaries / expenses) if staff_salaries is not None and expenses > 0 else None,
                "admin_salary_ratio": (admin_salaries / expenses) if admin_salaries is not None and expenses > 0 else None,
                "salaries_to_expense_ratio": round(salaries_to_expense_ratio, 6) if salaries_to_expense_ratio is not None else None,
                "source": source,
                "ein": ein,
            }
        ]


def _normalize_propublica_payload(payload: dict[str, Any], years: list[int]) -> tuple[str | None, list[dict[str, Any]]]:
    organization = payload.get("organization", payload)
    filings = payload.get("filings_with_data") or organization.get("filings_with_data") or []
    ein = str(organization.get("ein")) if organization.get("ein") else None
    records: list[dict[str, Any]] = []
    for filing in filings:
        year = int(filing.get("tax_prd_yr"))
        if years and year not in years:
            continue
        expenses = coalesce_number(filing.get("totfuncexpns")) or 0.0
        executive_compensation = _first_numeric(
            filing,
            [
                "compnsatncurrofcr",
                "officerdirtrstkeyemplycomp",
                "officers_compensation",
                "compensation_of_current_officers",
            ],
        )
        staff_salaries = _first_numeric(
            filing,
            [
                "salariesothercomp",
                "salaries_and_wages",
                "totalsalaries",
                "other_employee_compensation",
            ],
        )
        admin_salaries = _first_numeric(
            filing,
            [
                "managementandgeneral",
                "mgmtandgenlexpns",
                "admin_salaries",
            ],
        )
        total_salaries = _derive_total_salaries(
            executive_compensation,
            staff_salaries,
            admin_salaries,
            _first_numeric(filing, ["totalsalaries", "salary_wages_total"]),
        )

        records.append(
            {
                "year": year,
                "revenue": coalesce_number(filing.get("totrevenue")) or 0.0,
                "expenses": expenses,
                "assets": coalesce_number(filing.get("totassetsend")),
                "liabilities": coalesce_number(filing.get("totliabend")),
                "unrestricted_net_assets": coalesce_number(filing.get("totnetassetsend")),
                "program_expenses": _first_numeric(
                    filing,
                    [
                        "program_service_expenses",
                        "program_expenses",
                        "programservicexpns",
                        "totprgserviceexpns",
                        "program_service_expense",
                    ],
                ),
                "executive_compensation": executive_compensation,
                "staff_salaries": staff_salaries,
                "admin_salaries": admin_salaries,
                "total_salaries": total_salaries,
                "executive_salary_ratio": (executive_compensation / expenses) if executive_compensation is not None and expenses > 0 else None,
                "staff_salary_ratio": (staff_salaries / expenses) if staff_salaries is not None and expenses > 0 else None,
                "admin_salary_ratio": (admin_salaries / expenses) if admin_salaries is not None and expenses > 0 else None,
                "salaries_to_expense_ratio": (total_salaries / expenses) if total_salaries is not None and expenses > 0 else None,
                "source": "propublica",
                "ein": ein,
            }
        )
    return ein, sorted(records, key=lambda item: item["year"])


def _fetch_propublica(ein: str, config: dict[str, Any]) -> dict[str, Any]:
    base_url = config["sources"]["propublica"]["base_url"].rstrip("/")
    response = requests.get(f"{base_url}/organizations/{ein}.json", timeout=30)
    response.raise_for_status()
    return response.json()


def _normalize_ein(value: str | None) -> str | None:
    if not value:
        return None
    digits = "".join(ch for ch in str(value) if ch.isdigit())
    return digits.zfill(9) if digits else None


def _charity_navigator_live_signal(ein: str) -> dict[str, Any]:
    url = f"https://www.charitynavigator.org/ein/{ein}"
    try:
        response = requests.get(url, timeout=20)
        response.raise_for_status()
        text = response.text
        pct_match = re.search(r"(\d{2,3})%\s*Four-Star", text, flags=re.IGNORECASE)
        if pct_match:
            score = max(min(float(pct_match.group(1)) / 100.0, 1.0), 0.0)
            return {
                "charity_navigator_score": round(score, 4),
                "charity_navigator_status": f"four_star_{pct_match.group(1)}pct",
                "charity_navigator_url": url,
                "charity_navigator_source": "live",
            }
    except Exception:
        pass
    return {
        "charity_navigator_score": None,
        "charity_navigator_status": None,
        "charity_navigator_url": url,
        "charity_navigator_source": "unavailable",
    }


def _external_health_signals(ein: str | None) -> dict[str, Any]:
    normalized = _normalize_ein(ein)
    base = {
        "irs_teos_status": None,
        "irs_teos_status_risk": None,
        "irs_teos_source": str(EXTERNAL_BASELINE_FILE),
        "charity_navigator_score": None,
        "charity_navigator_status": None,
        "charity_navigator_url": None,
        "charity_navigator_source": "none",
    }
    if not normalized or not EXTERNAL_BASELINE_FILE.exists():
        return base

    try:
        baseline = pd.read_csv(EXTERNAL_BASELINE_FILE)
        baseline["ein_norm"] = baseline["ein"].astype(str).apply(_normalize_ein)
        row = baseline.loc[baseline["ein_norm"] == normalized].head(1)
        if not row.empty:
            status = str(row.iloc[0].get("external_status") or "").strip().lower()
            if status:
                if "four_star" in status:
                    base["irs_teos_status"] = "active"
                    base["irs_teos_status_risk"] = 0.0
                    pct = re.search(r"(\d{2,3})", status)
                    if pct:
                        base["charity_navigator_score"] = round(min(max(float(pct.group(1)) / 100.0, 0.0), 1.0), 4)
                    base["charity_navigator_status"] = status
                    base["charity_navigator_url"] = str(row.iloc[0].get("fact_source") or "")
                    base["charity_navigator_source"] = "baseline"
                else:
                    base["irs_teos_status"] = status
                    if status in {"revoked", "terminated", "noncompliant"}:
                        base["irs_teos_status_risk"] = 1.0
                    elif status in {"active", "current", "good"}:
                        base["irs_teos_status_risk"] = 0.0
                    else:
                        base["irs_teos_status_risk"] = 0.5
    except Exception:
        return base

    if base["charity_navigator_score"] is None and normalized:
        live = _charity_navigator_live_signal(normalized)
        base.update(live)

    return base


def run(
    source: str,
    ein: str | None,
    years: list[int],
    out_file: str,
    config_path: str | None = None,
) -> dict[str, Any]:
    config = load_config(config_path)
    source_value = source.strip()
    if source_value.lower() == "propublica":
        if not ein:
            raise ValueError("EIN is required when source is 'propublica'.")
        payload = _fetch_propublica(ein, config)
        normalized_ein, records = _normalize_propublica_payload(payload, years)
        external_signals = _external_health_signals(normalized_ein or ein)
        metadata = {
            "source": "propublica",
            "ein": normalized_ein or ein,
            "organization": payload.get("organization", {}).get("name"),
            **external_signals,
        }
    else:
        resolved = resolve_path(source_value)
        suffix = Path(resolved).suffix.lower()
        if suffix == ".pdf":
            records = _normalize_pdf_with_fallback(resolved, config, ein)
        else:
            frame = _load_table_file(resolved)
            records = _normalize_frame(frame, config, resolved, ein)
        if years:
            records = [record for record in records if record["year"] in years]
        metadata = {
            "source": resolved,
            "ein": ein,
            **_external_health_signals(ein),
        }

    result = {
        "metadata": {
            **metadata,
            "ingested_at": datetime.now().isoformat(),
            "record_count": len(records),
            "years": [record["year"] for record in records],
        },
        "records": records,
    }
    write_json(out_file, result)
    logging.info("Ingested %s records into %s", len(records), out_file)
    return result

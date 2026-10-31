"""Reporting governance: data dictionary, Power BI exports, QA log (append-only)."""
import json
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs"
GOV = OUT / "governance"
PBI = OUT / "powerbi"
KPI_DEFS = json.loads((ROOT / "config" / "kpi_definitions.json").read_text(encoding="utf-8"))

DESCRIPTIONS = {
    "source_row": "Row number in the source file (no request ID is published)",
    "status_raw": "Status as published", "status_group": "Closed / Open (derived)",
    "is_closed": "Closed status (A: CLOSED; B: Closed or Closed as Duplicate)",
    "is_duplicate": "B: status 'Closed as Duplicate'",
    "date_received": "A: date request received", "date_completed": "A: date finalised",
    "days_field": "A: published days_to_complete (closed: completion days, same-day = 1; open: age at extract)",
    "days_calendar": "A: date_completed - date_received", "days_calendar_min1": "A: calendar days, minimum 1",
    "age_at_extract": "Open requests: extract date - date received", "extract_date": "Assumed extract date (AS01)",
    "intake_month": "Month received (B: Melbourne time)", "intake_month_utc": "B: month opened in UTC (for sensitivity only)",
    "opened_utc": "B: date_opened as published (UTC)", "closed_utc": "B: date_closed as published (UTC)",
    "opened_local": "B: opened, Australia/Melbourne", "closed_local": "B: closed, Australia/Melbourne",
    "date_opened": "B: local business date opened", "date_closed": "B: local business date closed",
    "days_to_close": "B: closed - opened in days (fractional)", "case_origin": "B: intake channel",
    "channel_group": "B: Digital / Assisted / Council-initiated", "suburb_raw": "B: suburb as published",
    "suburb": "Suburb (B: normalised spelling)", "category": "A: high-level category",
    "service_desc": "A: detailed service", "service_type": "B: service type",
}

EXPORTS = {
    "fact_requests_a": "SELECT * FROM stg_requests_a",
    "fact_requests_b": "SELECT * EXCLUDE (opened_utc, closed_utc) FROM stg_requests_b",
    "kpi_summary": "SELECT * FROM kpi_sql_summary",
    "kpi_cohort": "SELECT * FROM kpi_sql_cohort",
    "kpi_meaningfulness": "SELECT * FROM kpi_meaningfulness",
    "kpi_integrity_recompute": "SELECT * FROM kpi_integrity_recompute",
    "kpi_period_comparability": "SELECT * FROM kpi_period_comparability",
    "stress_kpi_values": "SELECT * FROM stress_kpi_values",
    "stress_reversals": "SELECT * FROM stress_reversals",
    "stress_rank_agreement": "SELECT * FROM stress_rank_agreement",
    "sensitivity_scenarios": "SELECT * FROM sensitivity_scenarios",
    "sensitivity_trend": "SELECT * FROM sensitivity_trend",
    "km_curves": "SELECT * FROM km_curves",
    "coverage_assessment": "SELECT * FROM coverage_assessment",
    "backlog_reconstruction": "SELECT * FROM backlog_reconstruction",
    "backlog_limitations": "SELECT * FROM backlog_limitations",
    "qa_dq_results": "SELECT * FROM qa_dq_results",
    "qa_row_lineage": "SELECT * FROM qa_row_lineage",
    "gov_source_manifest": "SELECT * FROM gov_source_manifest",
}


def data_dictionary(con) -> pd.DataFrame:
    rows = []
    for t in ["stg_requests_a", "stg_requests_b"]:
        for name, dtype, *_ in con.execute(f"DESCRIBE {t}").fetchall():
            desc = DESCRIPTIONS.get(name, "Record-level QA flag (see qa_dq_results)" if name.startswith("dq_") else "")
            rows.append(dict(table=t, column=name, type=dtype, description=desc))
    for k in KPI_DEFS["kpis"]:
        rows.append(dict(table="KPI", column=k["id"], type="measure",
                         description=f"{k['name']}: {k['formula']} | population: {k['population']} | blind spot: {k['blind_spot']}"))
    dd = pd.DataFrame(rows)
    GOV.mkdir(parents=True, exist_ok=True)
    dd.to_csv(GOV / "data_dictionary.csv", index=False)
    return dd


def export(con) -> None:
    PBI.mkdir(parents=True, exist_ok=True)
    for name, sql in EXPORTS.items():
        con.execute(f"COPY ({sql}) TO '{(PBI / name).as_posix()}.csv' (HEADER, DELIMITER ',')")


def run_tests() -> dict:
    r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", str(ROOT / "tests"),
                        "--junit-xml", str(GOV / "test_results.xml")],
                       capture_output=True, text=True, cwd=ROOT)
    tail = r.stdout.strip().splitlines()[-1] if r.stdout.strip() else r.stderr[-300:]
    return dict(exit_code=r.returncode, summary=tail)


def _git_commit() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True,
                              cwd=ROOT).stdout.strip() or "uncommitted"
    except OSError:
        return "unknown"


def log_run(con, tests: dict) -> dict:
    man = con.execute("SELECT dataset, sha256, rows FROM gov_source_manifest").df()
    dq = con.execute("SELECT status, count(*) n FROM qa_dq_results GROUP BY 1").df()
    rec = con.execute("SELECT computed_correctly, count(*) n FROM kpi_integrity_recompute GROUP BY 1").df()
    entry = dict(
        run_id=str(uuid.uuid4())[:8],
        run_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        code_version=_git_commit(),
        kpi_definitions_version=KPI_DEFS["version"],
        sources={r.dataset: dict(sha256=r.sha256, rows=int(r.rows)) for r in man.itertuples()},
        dq_status=dict(zip(dq.status, dq.n.astype(int))),
        kpi_recompute=dict(zip(rec.computed_correctly, rec.n.astype(int))),
        tests=tests,
    )
    with open(GOV / "qa_log.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")
    return entry


def run(con: duckdb.DuckDBPyConnection) -> None:
    data_dictionary(con)
    export(con)
    print(f"governance: dictionary + {len(EXPORTS)} Power BI tables written")

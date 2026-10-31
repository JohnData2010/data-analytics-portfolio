"""Rerunnable pipeline: source files -> DuckDB -> QA -> KPIs -> stress/sensitivity -> exports -> tests -> report."""
import sys
from pathlib import Path

import duckdb

sys.path.insert(0, str(Path(__file__).resolve().parent))
import coverage  # noqa: E402
import dq_engine  # noqa: E402
import governance  # noqa: E402
import ingest  # noqa: E402
import kpi_engine  # noqa: E402
import report  # noqa: E402
import stress_sensitivity  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    ingest.main()
    con = duckdb.connect(str(ingest.DB))
    for f in sorted((ROOT / "sql").glob("*.sql")):
        print(f"running {f.name}")
        con.execute(f.read_text(encoding="utf-8"))
    dq_engine.run(con)
    df = kpi_engine.run(con)
    stress_sensitivity.run(con, df)
    coverage.run(con, df)
    governance.run(con)
    report.run(con)            # first pass so the tests can check the report text
    con.close()                # release the DuckDB lock for the test process

    tests = governance.run_tests()
    print(f"tests: {tests['summary']}")
    con = duckdb.connect(str(ingest.DB))
    report.run(con, tests)
    entry = governance.log_run(con, tests)
    con.close()
    print(f"run {entry['run_id']} logged (kpi definitions v{entry['kpi_definitions_version']})")
    return tests["exit_code"]


if __name__ == "__main__":
    sys.exit(main())

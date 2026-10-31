"""Rerunnable pipeline: parquet -> DuckDB -> staging -> demand recovery -> backtest -> DQ -> exports -> tests -> report."""
import sys
from pathlib import Path

import duckdb

sys.path.insert(0, str(Path(__file__).resolve().parent))
import dq  # noqa: E402
import evaluate  # noqa: E402
import export  # noqa: E402
import ingest  # noqa: E402
import recovery  # noqa: E402
import report  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


def connect():
    con = duckdb.connect(str(ingest.DB))
    con.execute("SET enable_progress_bar = false")
    return con


def main() -> int:
    ingest.main()
    con = connect()
    for f in sorted((ROOT / "sql").glob("*.sql")):
        print(f"running {f.name}")
        con.execute(f.read_text(encoding="utf-8"))
    recovery.run(con)
    chosen = evaluate.run(con)
    if not dq.run(con):
        print("a critical data quality rule failed: stopping before export")
        return 1
    export.run(con)
    report.run(con)            # first pass so the tests can check the report text
    con.close()                # release the DuckDB lock for the test process

    tests = export.run_tests()
    print(f"tests: {tests['summary']}")
    con = connect()
    report.run(con, tests)
    entry = export.log_run(con, tests, chosen)
    con.close()
    print(f"run {entry['run_id']} logged (params v{entry['params_version']})")
    return tests["exit_code"]


if __name__ == "__main__":
    sys.exit(main())

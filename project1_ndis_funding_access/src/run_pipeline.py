"""End-to-end rebuild: raw files -> DuckDB -> marts -> analysis -> Power BI exports -> report."""
import sys
from pathlib import Path

import duckdb

sys.path.insert(0, str(Path(__file__).resolve().parent))
import analysis  # noqa: E402
import evidence  # noqa: E402
import export  # noqa: E402
import ingest  # noqa: E402
import reconcile  # noqa: E402
import report  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


def run_sql(con: duckdb.DuckDBPyConnection) -> None:
    for f in sorted((ROOT / "sql").glob("*.sql")):
        print(f"running {f.name}")
        con.execute(f.read_text(encoding="utf-8"))


def main() -> None:
    ingest.main()
    con = duckdb.connect(str(ingest.DB))
    run_sql(con)
    failures = reconcile.run(con)
    analysis.run(con)
    evidence.run(con)
    export.run(con)
    report.run(con)
    con.close()
    if failures:
        print(f"WARNING: {failures} reconciliation check(s) failed - see outputs/reconciliation.csv")


if __name__ == "__main__":
    main()

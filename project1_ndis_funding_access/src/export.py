"""Write the Power BI model tables (star schema) to outputs/powerbi/*.csv."""
from pathlib import Path

import duckdb

OUT = Path(__file__).resolve().parents[1] / "outputs" / "powerbi"

TABLES = {
    # dimensions
    "dim_period": "SELECT * FROM dim_period",
    "dim_district": "SELECT * FROM dim_district",
    # Module A - Funding-Utilisation Explorer
    "fact_funding_util_district": "SELECT * FROM mart_funding_util_district",
    "fact_util_national": "SELECT * FROM mart_util_national",
    "fact_util_standardised": "SELECT * FROM mart_util_standardised",
    "fact_util_cells": "SELECT * FROM mart_util_cells",
    "an_util_persistence": "SELECT * FROM an_util_persistence",
    "an_mix_decomposition": "SELECT * FROM an_mix_decomposition",
    # Module B - SDA Access Gap Monitor
    "fact_sda_district": "SELECT * FROM mart_sda_district",
    "fact_sda_sa4_context": "SELECT * FROM mart_sda_sa4_context",
    "an_sda_persistence": "SELECT * FROM an_sda_persistence",
    # Module C + advanced
    "an_evidence_register": "SELECT * FROM an_evidence_register",
    "an_sensitivity_flags": "SELECT * FROM an_sensitivity_flags",
    "an_sensitivity_overlap": "SELECT * FROM an_sensitivity_overlap",
    "an_sensitivity_threshold": "SELECT * FROM an_sensitivity_threshold",
    "an_scenario_grid": "SELECT * FROM an_scenario_grid",
    "qa_reconciliation": "SELECT * FROM qa_reconciliation",
}


def run(con: duckdb.DuckDBPyConnection) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for name, sql in TABLES.items():
        con.execute(f"COPY ({sql}) TO '{(OUT / name).as_posix()}.csv' (HEADER, DELIMITER ',')")
    print(f"export: {len(TABLES)} tables -> {OUT}")

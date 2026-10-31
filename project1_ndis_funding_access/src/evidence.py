"""Module C - evidence register.

Every statement the report or dashboard might make is written here with an explicit
status and the rule that produced it:

  Observed pattern        - directly measured in NDIA data at a valid grain, robust to
                            the definition used (>=3 of 5 sensitivity definitions or
                            persistent in >=6 of 8 quarters), no masking in the cells used.
  Requires investigation  - a signal exists but the data cannot say why; lists the
                            hypotheses and the extra information needed to test them.
  Unsupported conclusion  - a tempting causal or capacity claim the published data
                            cannot support; states what is missing.
"""
import duckdb
import pandas as pd

OBS, INV, UNS = "Observed pattern", "Requires investigation", "Unsupported conclusion"

SDA_HYPOTHESES = [
    ("Dwelling suitability", "eligible SDA design category vs design category of vacancies in the area"),
    ("Location", "participant's preferred location vs location of enrolled dwellings with vacancies"),
    ("Personal choice", "participant preference / decision records (e.g. chose to stay with family)"),
    ("Transition timing", "date found eligible; participants recently approved through the home and living process"),
    ("Service availability", "SIL / support provider availability for the dwelling, provider vacancy data"),
    ("Recording", "address matching to enrolled dwellings and service bookings (NDIA notes PACE limitations)"),
]
UTIL_HYPOTHESES = [
    ("Thin provider market", "provider counts and active registrations by district and support category"),
    ("Remoteness / travel", "remoteness classification of participants, travel claims"),
    ("Payment lag", "payments made after the 3-month lag window; plan-managed vs agency-managed timing"),
    ("Plan design", "budget flexibility, stated supports, plan length and reassessment timing"),
    ("Participant circumstances", "plan reviews, hospital stays, informal support changes"),
]


def _pct(x):
    return "n/a" if pd.isna(x) else f"{x * 100:.1f}%"


def build(con) -> pd.DataFrame:
    rows = []
    add = lambda **k: rows.append(k)  # noqa: E731

    # ---------------- national utilisation and mix
    mix = con.execute("SELECT * FROM an_mix_decomposition WHERE span = 'first-to-last'").df()
    for _, m in mix.iterrows():
        add(module="A", scope="National", topic="Utilisation trend",
            claim=(f"National utilisation moved from {_pct(m.published_from)} to {_pct(m.published_to)} "
                   f"(30-Sep-2024 to 30-Jun-2026). Changes in {m.decomposition} account for "
                   f"{m.mix_effect * 100:+.2f} pts; changes in within-group rates account for {m.rate_effect * 100:+.2f} pts."),
            status=OBS, rule="published rates; budget-weighted shift-share reconstructs published rate within 1 pt",
            evidence="an_mix_decomposition", caveats="NDIA publishes utilisation as whole percentages (+/-0.5 pt rounding); "
                     "utilisation uses a lagged 6-month window; weights are committed budgets",
            information_needed="")
    add(module="A", scope="National", topic="Utilisation trend",
        claim="The fall in national utilisation shows that participants are getting less support than in 2024.",
        status=UNS, rule="causal claim from a ratio",
        evidence="an_mix_decomposition",
        caveats="Utilisation = payments / committed budget. A fall can come from larger budgets, slower claiming, "
                "payment timing or less support delivered; the data do not separate these.",
        information_needed="payments and committed budgets in dollars by period; claim lag distribution")

    # ---------------- district utilisation
    util = con.execute("SELECT * FROM an_util_persistence").df()
    flags = con.execute("SELECT * FROM an_sensitivity_flags WHERE module = 'Utilisation'").df().set_index("district_key")
    for _, u in util.iterrows():
        k = u.district_key
        robust = k in flags.index and flags.loc[k, "definitions_flagging"] >= 3
        if u.persistent_low_adjusted and robust:
            add(module="A", scope=k, topic="Low utilisation",
                claim=(f"{k}: utilisation in the bottom fifth of districts in {int(u.quarters_bottom_quintile_adjusted)}/8 "
                       f"quarters after adjusting for disability x age mix (mean gap {u.mean_like_for_like_gap * 100:+.1f} pts; "
                       f"mix effect {u.mean_mix_effect * 100:+.1f} pts)."),
                status=OBS, rule=f"persistent >= 6 quarters AND flagged by {int(flags.loc[k, 'definitions_flagging'])}/5 definitions",
                evidence="an_util_persistence; an_sensitivity_flags",
                caveats=f"within-cell comparison covers {_pct(u.mean_within_cell_coverage)} of budget (small cells suppressed)",
                information_needed="")
            add(module="A", scope=k, topic="Low utilisation",
                claim=f"{k}: why is utilisation persistently lower than comparable participants elsewhere?",
                status=INV, rule="persistent like-for-like gap; cause not in data",
                evidence="an_util_persistence",
                caveats="Hypotheses: " + "; ".join(h for h, _ in UTIL_HYPOTHESES),
                information_needed="; ".join(f"{h}: {need}" for h, need in UTIL_HYPOTHESES))
            add(module="A", scope=k, topic="Low utilisation",
                claim=f"{k}: participants receive poorer quality services / need less support than elsewhere.",
                status=UNS, rule="quality and need are not measured in utilisation data",
                evidence="", caveats="Utilisation measures spending against budget, not quality, need or outcomes.",
                information_needed="outcomes data, unmet-need surveys, provider quality data")
        elif u.low_mostly_mix:
            add(module="A", scope=k, topic="Low utilisation",
                claim=(f"{k}: low raw utilisation ({int(u.quarters_bottom_quintile_raw)}/8 quarters in bottom fifth) "
                       f"is largely not present after adjusting for participant mix "
                       f"({int(u.quarters_bottom_quintile_adjusted)}/8 quarters)."),
                status=OBS, rule="raw persistent, adjusted not persistent, negative mix effect",
                evidence="an_util_persistence", caveats="mix adjustment uses disability x age only", information_needed="")
        elif u.persistent_low_raw or u.persistent_low_adjusted:
            add(module="A", scope=k, topic="Low utilisation",
                claim=f"{k}: low utilisation in several quarters, but the ranking depends on the definition used.",
                status=INV, rule="persistent on one measure only or flagged by < 3 definitions",
                evidence="an_util_persistence; an_sensitivity_flags",
                caveats="definition-dependent; check before prioritising",
                information_needed="confirm with dollar-level payments data")

    # ---------------- SDA
    sda = con.execute("SELECT * FROM an_sda_persistence").df()
    sflags = con.execute("SELECT * FROM an_sensitivity_flags WHERE module = 'SDA non-use'").df().set_index("district_key")
    nat = con.execute("""SELECT report_date, national_not_using, national_eligible, national_non_use_share
                         FROM mart_sda_district GROUP BY ALL ORDER BY 1""").df()
    f, l = nat.iloc[0], nat.iloc[-1]
    add(module="B", scope="National", topic="SDA non-use",
        claim=(f"Participants eligible for SDA but not yet using it: {int(f.national_not_using):,} at 30-Sep-2024 and "
               f"{int(l.national_not_using):,} at 30-Jun-2026 ({_pct(f.national_non_use_share)} -> "
               f"{_pct(l.national_non_use_share)} of eligible participants)."),
        status=OBS, rule="sum of published (unmasked) district cells; reconciles to SDA supplement P.1",
        evidence="mart_sda_district; qa_reconciliation", caveats="masked cells (<11) excluded from sums",
        information_needed="")
    for _, s in sda.iterrows():
        k = s.district_key
        n_flags = int(sflags.loc[k, "definitions_flagging"]) if k in sflags.index else 0
        if s.persistent_above_national and n_flags >= 3 and not s.latest_any_masked:
            add(module="B", scope=k, topic="SDA non-use",
                claim=(f"{k}: {int(s.latest_not_using)} of {int(s.latest_eligible_total)} SDA-eligible participants "
                       f"not yet using SDA at 30-Jun-2026 ({_pct(s.latest_non_use_share)}); above the national share in "
                       f"{int(s.quarters_above_national)}/8 quarters (change since Sep-2024: {int(s.change_not_using):+d} participants)."),
                status=OBS, rule=f"persistent >= 6 quarters AND flagged by {n_flags}/5 definitions AND unmasked",
                evidence="an_sda_persistence; an_sensitivity_flags", caveats="", information_needed="")
            add(module="B", scope=k, topic="SDA non-use",
                claim=f"{k}: why do eligible participants persistently not use SDA?",
                status=INV, rule="persistent eligibility-use gap; reason not recorded in published data",
                evidence="an_sda_persistence; mart_sda_sa4_context (context only, SA4 grain)",
                caveats="Hypotheses: " + "; ".join(h for h, _ in SDA_HYPOTHESES),
                information_needed="; ".join(f"{h}: {need}" for h, need in SDA_HYPOTHESES))
            add(module="B", scope=k, topic="SDA non-use",
                claim=f"{k} has a shortage of SDA housing.",
                status=UNS, rule="capacity claim without vacancy or suitability data",
                evidence="mart_sda_sa4_context",
                caveats="Dwelling counts are published by SA4, not service district; an enrolled dwelling is not a "
                        "vacancy and may not match the participant's design category, location or preference.",
                information_needed="vacancy data by design category and location; participant preferences; matching outcomes")
        elif s.persistent_above_national or n_flags >= 2:
            add(module="B", scope=k, topic="SDA non-use",
                claim=(f"{k}: SDA non-use is elevated on some measures ({n_flags}/5 definitions; above national "
                       f"in {int(s.quarters_above_national)}/8 quarters)."),
                status=INV, rule="signal depends on the measure, or masked cells present",
                evidence="an_sda_persistence; an_sensitivity_flags",
                caveats="check absolute count vs share before prioritising",
                information_needed="as for persistent districts")

    # ---------------- cross-dataset guardrails
    add(module="B", scope="All districts", topic="Grain",
        claim="District-level SDA non-use can be divided by district-level enrolled dwellings to measure unmet housing need.",
        status=UNS, rule="grain mismatch",
        evidence="mart_sda_sa4_context",
        caveats="Dwellings are published by SA4/SA3, participants by service district; the boundaries do not nest.",
        information_needed="dwelling data at service district grain or a published SA4-district concordance")
    add(module="A", scope="All districts", topic="Grain",
        claim="Utilisation at a reporting date reflects support used at that date.",
        status=UNS, rule="reference period mismatch",
        evidence="dim_period",
        caveats="Utilisation covers a 6-month window ending 3 months before the reporting date; budgets are point-in-time.",
        information_needed="")
    out = pd.DataFrame(rows)
    out.insert(0, "claim_id", [f"C{i:03d}" for i in range(1, len(out) + 1)])
    return out


def run(con: duckdb.DuckDBPyConnection) -> None:
    df = build(con)
    con.register("ev", df)
    con.execute("CREATE OR REPLACE TABLE an_evidence_register AS SELECT * FROM ev")
    con.unregister("ev")
    print("evidence register:", df.status.value_counts().to_dict())


if __name__ == "__main__":
    from pathlib import Path
    c = duckdb.connect(str(Path(__file__).resolve().parents[1] / "data" / "ndis.duckdb"))
    run(c)

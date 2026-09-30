"""
Pytest sanity checks for the process-monitoring pipeline.

Run after `python run_all.py` (the run_all fixture below re-runs the
pipeline itself if outputs are missing, so `pytest -q` works standalone too).
"""
import sqlite3
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

import simulate_mes_historian_data as sim
import spc_alerting as spc
import predictive_maintenance as pm_mod

DB_PATH = ROOT / "data" / "process_monitoring.db"
OUTPUT_DIR = ROOT / "output"


@pytest.fixture(scope="module")
def pipeline_outputs():
    if not DB_PATH.exists():
        sim.main()
    conn = sqlite3.connect(DB_PATH)
    mes = pd.read_sql("SELECT * FROM mes_batches", conn, parse_dates=["batch_start", "batch_end"])
    historian = pd.read_sql("SELECT * FROM historian_sensor_data", conn, parse_dates=["timestamp"])
    conn.close()
    alerts = spc.run_spc(historian)
    summary = pm_mod.per_batch_summary(historian)
    warnings_df = pm_mod.compute_early_warnings(summary)
    return {
        "mes": mes,
        "historian": historian,
        "alerts": alerts,
        "pm_summary": summary,
        "pm_warnings": warnings_df,
    }


def test_mes_batch_counts(pipeline_outputs):
    mes = pipeline_outputs["mes"]
    assert len(mes) == len(sim.LINES) * sim.N_BATCHES_PER_LINE
    assert mes["line_id"].nunique() == len(sim.LINES)


def test_historian_schema_and_coverage(pipeline_outputs):
    historian = pipeline_outputs["historian"]
    expected_cols = {
        "equipment_id", "line_id", "batch_id", "timestamp",
        "temperature_c", "pressure_bar", "flow_lpm",
    }
    assert expected_cols.issubset(set(historian.columns))
    assert historian["equipment_id"].nunique() == len(sim.LINES) * 3  # 3 equip per line
    assert len(historian) > 50_000  # 5 min resolution over 200 batches x 8h across 15 units


def test_sqlite_database_is_real_and_queryable():
    assert DB_PATH.exists(), "SQLite database file must exist on disk"
    conn = sqlite3.connect(DB_PATH)
    tables = pd.read_sql(
        "SELECT name FROM sqlite_master WHERE type='table'", conn
    )["name"].tolist()
    assert "mes_batches" in tables
    assert "historian_sensor_data" in tables
    n = conn.execute("SELECT COUNT(*) FROM historian_sensor_data").fetchone()[0]
    assert n > 0
    conn.close()


def test_spc_detects_injected_violation(pipeline_outputs):
    """
    Sanity check: a batch on LINE-05's reactor has an injected temperature
    runaway (a sustained +6-sigma-scale ramp). SPC rules must fire an alert
    for that specific equipment/batch, and must NOT fire Rule-1 (3-sigma)
    alerts en masse on equipment with no injected anomaly.
    """
    alerts = pipeline_outputs["alerts"]
    assert not alerts.empty, "SPC must generate at least one alert given the injected violation"

    viol_equip = f"{sim.SPC_VIOLATION_LINE}-REACTOR"
    equip_alerts = alerts[alerts["equipment_id"] == viol_equip]
    assert not equip_alerts.empty, "Injected SPC violation on LINE-05 reactor must be detected"
    assert "WE Rule 1" in equip_alerts["rule_triggered"].values, (
        "Injected 6-sigma-scale runaway must trigger Western Electric Rule 1 "
        "(single point beyond 3 sigma)"
    )

    # Identify the specific batch the runaway was injected into and confirm
    # the alert(s) land on THAT batch/sensor (not just anywhere in the
    # equipment's long history, where random 3-sigma noise can also occur
    # at low rates over tens of thousands of samples).
    historian = pipeline_outputs["historian"]
    equip_batches = (
        historian[historian["equipment_id"] == viol_equip]
        .sort_values("timestamp")["batch_id"].drop_duplicates().tolist()
    )
    injected_batch_id = equip_batches[sim.SPC_VIOLATION_BATCH_INDEX]
    rule1_batches = set(
        equip_alerts[equip_alerts["rule_triggered"] == "WE Rule 1"]["batch_id"]
    )
    assert injected_batch_id in rule1_batches, (
        f"Expected the injected-violation batch {injected_batch_id} to trigger "
        f"a WE Rule 1 alert; batches that did: {rule1_batches}"
    )


def test_predictive_maintenance_flags_degrading_equipment(pipeline_outputs):
    """
    Sanity check: the designated degrading equipment (LINE-03-PUMP) has an
    injected slow rising temperature baseline + growing pressure noise.
    The trend-based early-warning indicator must flag it, and must NOT flag
    a healthy peer pump on a different line.
    """
    warnings_df = pipeline_outputs["pm_warnings"]
    assert not warnings_df.empty

    degrading_row = warnings_df[warnings_df["equipment_id"] == sim.DEGRADING_EQUIPMENT]
    assert not degrading_row.empty
    assert bool(degrading_row.iloc[0]["maintenance_recommended"]) is True

    healthy_row = warnings_df[warnings_df["equipment_id"] == "LINE-01-PUMP"]
    if not healthy_row.empty:
        assert bool(healthy_row.iloc[0]["maintenance_recommended"]) is False


def test_western_electric_rule1_synthetic_signal():
    """
    Direct unit-level sanity check on the SPC rule function itself: a clean
    in-control synthetic signal should trigger no Rule-1 alerts, while the
    same signal with a single injected 5-sigma spike must trigger exactly a
    Rule-1 alert at that point.
    """
    rng = np.random.default_rng(123)
    mu, sigma = 50.0, 1.0
    values = rng.normal(mu, sigma, 200)
    clean_violations = spc.western_electric_rules(values, mu, sigma)
    clean_rule1 = [v for v in clean_violations if v["rule"] == "WE Rule 1"]
    assert len(clean_rule1) == 0

    spiked = values.copy()
    spiked[100] = mu + 5 * sigma
    spiked_violations = spc.western_electric_rules(spiked, mu, sigma)
    spiked_rule1 = [v for v in spiked_violations if v["rule"] == "WE Rule 1"]
    assert len(spiked_rule1) >= 1
    assert spiked_rule1[0]["index"] == 100


def test_dashboard_extracts_exist_after_full_run(pipeline_outputs):
    """
    Confirm every dashboard-extract deliverable file lands. Builds extracts
    directly from the already-computed pipeline_outputs fixture (rather than
    re-running the full simulate-from-scratch pipeline again) to avoid
    redundant disk I/O in this disk-constrained environment -- see README's
    disk-space note.
    """
    import build_dashboard_extracts as bde

    bde.build_extracts(
        pipeline_outputs["mes"], pipeline_outputs["historian"],
        pipeline_outputs["alerts"], pipeline_outputs["pm_warnings"],
    )
    bde.build_figures(pipeline_outputs["historian"], pipeline_outputs["alerts"], pipeline_outputs["pm_warnings"])
    bde.write_report(
        pipeline_outputs["mes"], pipeline_outputs["historian"],
        pipeline_outputs["alerts"], pipeline_outputs["pm_warnings"],
    )

    for fname in [
        "spc_alerts.csv",
        "predictive_maintenance_signals.csv",
        "bi_extract_hourly_sensor_trends.csv",
        "bi_extract_batch_summary.csv",
        "bi_extract_spc_alerts.csv",
        "bi_extract_predictive_maintenance.csv",
        "report.md",
    ]:
        assert (OUTPUT_DIR / fname).exists(), f"missing output/{fname}"

    for fig in [
        "equipment_temperature_trend.png",
        "spc_alerts_by_rule.png",
        "spc_control_chart_example.png",
    ]:
        assert (OUTPUT_DIR / "figures" / fig).exists(), f"missing figure {fig}"

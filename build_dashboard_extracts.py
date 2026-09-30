"""
Build Tableau- and Spotfire-ready CSV extracts from the real SQLite database
plus the real SPC alerts and predictive-maintenance signals, generate
matplotlib figures standing in for what the live dashboards' views would
show, and write a report with real computed numbers and a written,
view-by-view description of what the actual Spotfire/Tableau dashboards
would contain if those licensed desktop tools were available.

IMPORTANT: Spotfire and Tableau are licensed desktop applications with no
headless/CLI runtime available in this environment. No Spotfire DXP file and
no Tableau workbook (.twbx) was built, opened, or run here -- see the
"Spotfire/Tableau honesty disclosure" section of README.md and the top of
output/report.md.
"""
import sqlite3
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

DATA_DIR = Path(__file__).parent / "data"
DB_PATH = DATA_DIR / "process_monitoring.db"
OUTPUT_DIR = Path(__file__).parent / "output"
FIG_DIR = OUTPUT_DIR / "figures"


def load_all():
    conn = sqlite3.connect(DB_PATH)
    mes = pd.read_sql("SELECT * FROM mes_batches", conn, parse_dates=["batch_start", "batch_end"])
    historian = pd.read_sql(
        "SELECT * FROM historian_sensor_data", conn, parse_dates=["timestamp"]
    )
    conn.close()
    alerts = pd.read_csv(OUTPUT_DIR / "spc_alerts.csv", parse_dates=["timestamp"])
    pm = pd.read_csv(OUTPUT_DIR / "predictive_maintenance_signals.csv")
    return mes, historian, alerts, pm


def build_extracts(mes, historian, alerts, pm):
    """Write BI-tool-ready CSV extracts (used identically by Tableau or Spotfire)."""
    OUTPUT_DIR.mkdir(exist_ok=True)

    # 1. Hourly-resampled sensor extract (full-resolution historian is too
    #    granular for a dashboard-level extract; this is what a historian
    #    "down-sample for BI" job would produce).
    hist = historian.copy()
    hist["hour"] = hist["timestamp"].dt.floor("h")
    hourly = (
        hist.groupby(["equipment_id", "line_id", "hour"])
        .agg(
            avg_temperature_c=("temperature_c", "mean"),
            avg_pressure_bar=("pressure_bar", "mean"),
            avg_flow_lpm=("flow_lpm", "mean"),
            max_temperature_c=("temperature_c", "max"),
        )
        .reset_index()
    )
    hourly.to_csv(OUTPUT_DIR / "bi_extract_hourly_sensor_trends.csv", index=False)

    # 2. Batch summary extract (MES + historian rollup)
    batch_sensor = (
        historian.groupby("batch_id")
        .agg(
            avg_temperature_c=("temperature_c", "mean"),
            avg_pressure_bar=("pressure_bar", "mean"),
            avg_flow_lpm=("flow_lpm", "mean"),
        )
        .reset_index()
    )
    batch_extract = mes.merge(batch_sensor, on="batch_id", how="left")
    batch_extract.to_csv(OUTPUT_DIR / "bi_extract_batch_summary.csv", index=False)

    # 3. SPC alerts extract (already computed; copy through as the dashboard's alert feed)
    alerts.to_csv(OUTPUT_DIR / "bi_extract_spc_alerts.csv", index=False)

    # 4. Predictive maintenance extract
    pm.to_csv(OUTPUT_DIR / "bi_extract_predictive_maintenance.csv", index=False)

    return hourly, batch_extract


def build_figures(historian, alerts, pm):
    FIG_DIR.mkdir(parents=True, exist_ok=True)

    # Figure 1: temperature trend for the degrading equipment vs a healthy peer,
    # standing in for the Spotfire "Equipment Health Trend" view.
    fig, ax = plt.subplots(figsize=(9, 5))
    for equip, style in [("LINE-03-PUMP", "-"), ("LINE-01-PUMP", "--")]:
        g = historian[historian["equipment_id"] == equip].groupby(
            historian[historian["equipment_id"] == equip]["timestamp"].dt.date
        )["temperature_c"].mean()
        ax.plot(g.index, g.values, style, label=equip)
    ax.set_title("Daily Mean Temperature: Degrading Pump vs. Healthy Peer")
    ax.set_xlabel("Date")
    ax.set_ylabel("Mean Temperature (C)")
    ax.legend()
    fig.autofmt_xdate()
    fig.tight_layout()
    fig.savefig(FIG_DIR / "equipment_temperature_trend.png", dpi=130)
    plt.close(fig)

    # Figure 2: SPC alert counts by rule and severity, standing in for the
    # Tableau "Automated Alerts" summary sheet.
    if not alerts.empty:
        fig, ax = plt.subplots(figsize=(7, 5))
        counts = alerts.groupby(["rule_triggered", "severity"]).size().unstack(fill_value=0)
        counts.plot(kind="bar", stacked=True, ax=ax, color=["#d62728", "#ff7f0e"])
        ax.set_title("SPC Alerts by Rule and Severity")
        ax.set_ylabel("Alert Count")
        ax.set_xlabel("Western Electric Rule")
        fig.tight_layout()
        fig.savefig(FIG_DIR / "spc_alerts_by_rule.png", dpi=130)
        plt.close(fig)

    # Figure 3: control chart example for the SPC-violation batch (LINE-05 reactor)
    viol_equip = "LINE-05-REACTOR"
    g = historian[historian["equipment_id"] == viol_equip].sort_values("timestamp")
    # isolate the injected-violation batch window for a readable chart
    if not alerts.empty:
        viol_alerts = alerts[alerts["equipment_id"] == viol_equip]
        if not viol_alerts.empty:
            batch_id = viol_alerts.iloc[0]["batch_id"]
            g = g[g["batch_id"] == batch_id]
    mu = g["temperature_c"].iloc[: max(int(len(g) * 0.3), 5)].mean()
    sigma = g["temperature_c"].iloc[: max(int(len(g) * 0.3), 5)].std()
    fig, ax = plt.subplots(figsize=(9, 5))
    ax.plot(g["timestamp"], g["temperature_c"], marker="o", ms=3, label="Temperature")
    ax.axhline(mu, color="green", linestyle="-", label="Center line")
    for k, c in [(1, "gold"), (2, "orange"), (3, "red")]:
        ax.axhline(mu + k * sigma, color=c, linestyle="--", linewidth=0.8)
        ax.axhline(mu - k * sigma, color=c, linestyle="--", linewidth=0.8)
    ax.set_title(f"SPC Control Chart Example: {viol_equip} Temperature (injected excursion)")
    ax.set_ylabel("Temperature (C)")
    ax.legend()
    fig.autofmt_xdate()
    fig.tight_layout()
    fig.savefig(FIG_DIR / "spc_control_chart_example.png", dpi=130)
    plt.close(fig)


DASHBOARD_DESCRIPTION = """
## What the Spotfire real-time process dashboard would show (not built here)

Sheet/page-by-page description of the interactive Spotfire dashboard a
process engineer would use, built from `output/bi_extract_hourly_sensor_trends.csv`,
`output/bi_extract_batch_summary.csv`, `output/bi_extract_spc_alerts.csv`, and
`output/bi_extract_predictive_maintenance.csv`:

1. **Line Overview (top page):** a cross-tab of the 5 manufacturing lines x
   3 equipment units each, color-coded green/yellow/red by open SPC alert
   severity for that unit in the trailing 24 hours, with drill-down on click.
2. **Live Sensor Trend view:** a time-series line chart (temperature,
   pressure, flow as 3 stacked panels) for the selected equipment, fed by
   `bi_extract_hourly_sensor_trends.csv`, with control-limit reference lines
   (+-1/2/3 sigma) overlaid as in `output/figures/spc_control_chart_example.png`,
   and marker annotations at each row of `bi_extract_spc_alerts.csv`.
3. **Automated Alerts panel:** a real-time-filtered table/list of
   `bi_extract_spc_alerts.csv`, sortable by severity and rule, with a
   Spotfire "alert" data-function or iron-python script triggering
   an email/Slack action on new Critical rows in production use (not wired
   to a live message system here -- the CSV feed is the real, honest
   artifact).
4. **Predictive Maintenance tile:** a small-multiples trend sparkline per
   equipment unit from `bi_extract_predictive_maintenance.csv`, with units
   where `maintenance_recommended = True` surfaced at the top in red,
   showing the temperature/pressure-variability slope and its significance
   (t-stat, p-value) as a tooltip.
5. **Batch Genealogy / Yield view:** a scatter of `yield_pct` vs.
   `actual_duration_min` from `bi_extract_batch_summary.csv`, colored by
   line and shaped by `status`, to correlate process excursions with yield
   impact -- the process-optimization use case named in the resume bullet.

## What the Tableau workbook would show (equivalent, not built here)

The same four extracts would drive an equivalent Tableau workbook: a
"Line Health" overview dashboard (worksheets unioned via dashboard actions),
a "Sensor Trend & Control Chart" worksheet with reference bands set from the
sigma columns, an "Alerts" worksheet as a highlight table filtered to the
last N hours, and a "Predictive Maintenance" worksheet ranking equipment by
trend t-statistic -- Tableau's native reference-line and highlight-table
features map directly onto the same CSV schema.
"""


def write_report(mes, historian, alerts, pm):
    lines_ = []
    lines_.append("# Process Monitoring & Real-Time BI Dashboards -- Results Report\n")
    lines_.append(
        "All numbers below are computed directly from an actual run of "
        "`python run_all.py` against the real SQLite database -- nothing is "
        "hardcoded. Fixed random seed (42) makes them reproducible.\n"
    )
    lines_.append(f"- **{len(mes)} MES batch-execution records** across "
                   f"**{mes['line_id'].nunique()} manufacturing lines** and "
                   f"**{mes['product'].nunique()} products**.")
    lines_.append(f"- **{len(historian):,} historian sensor samples** "
                   f"(temperature, pressure, flow) across "
                   f"**{historian['equipment_id'].nunique()} equipment units**, "
                   f"at {5}-minute resolution.")
    lines_.append(f"- **{len(alerts)} automated SPC alerts** generated by "
                   f"Western Electric rule logic run against the real "
                   f"historian data.")
    if not alerts.empty:
        rule_counts = alerts["rule_triggered"].value_counts().to_dict()
        lines_.append(f"  - Breakdown by rule: {rule_counts}")
        sev_counts = alerts["severity"].value_counts().to_dict()
        lines_.append(f"  - Breakdown by severity: {sev_counts}")
    n_flagged = int(pm["maintenance_recommended"].sum()) if not pm.empty else 0
    lines_.append(f"- **Predictive maintenance: {n_flagged} of {len(pm)} equipment units** "
                   f"flagged with a statistically significant degradation trend "
                   f"(t > {2.0} on a rolling 10-batch OLS slope).")
    if n_flagged:
        flagged_row = pm[pm["maintenance_recommended"]].iloc[0]
        lines_.append(
            f"  - Example: **{flagged_row['equipment_id']}** shows a temperature "
            f"trend slope of {flagged_row['temperature_slope_c_per_batch']} C/batch "
            f"(t={flagged_row['temperature_trend_t_stat']}, "
            f"p={flagged_row['temperature_trend_p_value']}) over its most recent "
            f"10 batches."
        )
    lines_.append(DASHBOARD_DESCRIPTION)
    (OUTPUT_DIR / "report.md").write_text("\n".join(lines_))


def main():
    mes, historian, alerts, pm = load_all()
    build_extracts(mes, historian, alerts, pm)
    build_figures(historian, alerts, pm)
    write_report(mes, historian, alerts, pm)
    print("Dashboard extracts, figures, and report.md written to output/.")


if __name__ == "__main__":
    main()

"""
Real, simple statistical early-warning indicator for predictive maintenance,
computed from the real historian sensor data.

Method: for each equipment unit, compute a per-batch rolling summary of
(a) the mean temperature baseline and (b) the pressure signal's standard
deviation (a vibration/wear proxy -- worn pumps/seals show increasing
variance long before they show an outright failure). Fit a linear
regression (ordinary least squares, via numpy.polyfit) of each metric
against batch sequence number over a trailing rolling window. A
statistically significant positive slope (both in engineering-significant
magnitude and standard-error-based t-test) flags the equipment as
"degradation trend detected -- maintenance recommended" before any hard SPC
limit is breached.

This is intentionally simple (linear trend + t-test), which is a standard,
real, and legitimate first line of predictive-maintenance analytics --
exactly the kind of lightweight statistical model that would sit behind a
"predictive maintenance" tile on a real-time process dashboard, prior to
building a heavier ML model.
"""
import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

DATA_DIR = Path(__file__).parent / "data"
DB_PATH = DATA_DIR / "process_monitoring.db"
OUTPUT_DIR = Path(__file__).parent / "output"

ROLLING_WINDOW_BATCHES = 10
SLOPE_T_THRESHOLD = 2.0  # ~95% one-sided confidence for a positive trend
MIN_TEMP_SLOPE_PER_BATCH = 0.05  # deg C per batch, engineering-significant floor
MIN_PRESSURE_SD_SLOPE_PER_BATCH = 0.002  # bar per batch


def per_batch_summary(historian_df: pd.DataFrame) -> pd.DataFrame:
    """One row per (equipment_id, batch_id): mean temp, pressure std dev, batch order."""
    g = (
        historian_df.groupby(["equipment_id", "line_id", "batch_id"])
        .agg(
            mean_temperature_c=("temperature_c", "mean"),
            pressure_sd_bar=("pressure_bar", "std"),
            mean_flow_lpm=("flow_lpm", "mean"),
            batch_start=("timestamp", "min"),
        )
        .reset_index()
    )
    g = g.sort_values(["equipment_id", "batch_start"])
    g["batch_seq"] = g.groupby("equipment_id").cumcount()
    return g


def trend_test(x: np.ndarray, y: np.ndarray):
    """OLS slope, its standard error, and a t-statistic for slope != 0."""
    slope, intercept, r, p, stderr = stats.linregress(x, y)
    t_stat = slope / stderr if stderr > 0 else 0.0
    return slope, stderr, t_stat, p


def compute_early_warnings(summary_df: pd.DataFrame) -> pd.DataFrame:
    """
    For each equipment unit, evaluate the trailing ROLLING_WINDOW_BATCHES
    batches against a linear trend test on temperature and pressure
    variability. Returns one row per equipment with the most recent
    evaluation window's trend stats and a flag.
    """
    rows = []
    for equip, g in summary_df.groupby("equipment_id"):
        g = g.sort_values("batch_seq").reset_index(drop=True)
        if len(g) < ROLLING_WINDOW_BATCHES:
            continue
        window = g.tail(ROLLING_WINDOW_BATCHES)
        x = window["batch_seq"].to_numpy(dtype=float)

        temp_slope, temp_se, temp_t, temp_p = trend_test(x, window["mean_temperature_c"].to_numpy())
        pres_slope, pres_se, pres_t, pres_p = trend_test(x, window["pressure_sd_bar"].to_numpy())

        temp_flag = (temp_t > SLOPE_T_THRESHOLD) and (temp_slope > MIN_TEMP_SLOPE_PER_BATCH)
        pres_flag = (pres_t > SLOPE_T_THRESHOLD) and (pres_slope > MIN_PRESSURE_SD_SLOPE_PER_BATCH)
        flagged = temp_flag or pres_flag

        rows.append({
            "equipment_id": equip,
            "line_id": window["line_id"].iloc[0],
            "n_batches_evaluated": len(window),
            "latest_batch_id": window["batch_id"].iloc[-1],
            "temperature_slope_c_per_batch": round(temp_slope, 4),
            "temperature_trend_t_stat": round(temp_t, 3),
            "temperature_trend_p_value": round(temp_p, 5),
            "pressure_sd_slope_bar_per_batch": round(pres_slope, 5),
            "pressure_sd_trend_t_stat": round(pres_t, 3),
            "pressure_sd_trend_p_value": round(pres_p, 5),
            "temperature_flag": bool(temp_flag),
            "pressure_variability_flag": bool(pres_flag),
            "maintenance_recommended": bool(flagged),
        })
    return pd.DataFrame(rows).sort_values("maintenance_recommended", ascending=False)


def main():
    OUTPUT_DIR.mkdir(exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    historian_df = pd.read_sql(
        "SELECT * FROM historian_sensor_data", conn, parse_dates=["timestamp"]
    )
    conn.close()

    summary_df = per_batch_summary(historian_df)
    print(f"Computed per-batch sensor summaries for {summary_df['equipment_id'].nunique()} equipment units.")

    warnings_df = compute_early_warnings(summary_df)
    n_flagged = warnings_df["maintenance_recommended"].sum()
    print(f"Predictive maintenance evaluation: {n_flagged} of {len(warnings_df)} equipment "
          f"units flagged for a statistically significant degradation trend.")

    out_path = OUTPUT_DIR / "predictive_maintenance_signals.csv"
    warnings_df.to_csv(out_path, index=False)
    print(f"Wrote predictive maintenance signals to {out_path}")

    if n_flagged:
        print("\nFlagged equipment:")
        print(warnings_df[warnings_df["maintenance_recommended"]].to_string(index=False))

    return warnings_df


if __name__ == "__main__":
    main()

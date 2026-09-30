"""
Real SPC (Statistical Process Control) logic run against the historian
sensor data stored in the SQLite database, generating automated alerts when
Western-Electric-style rules are violated.

Control limits are computed per (equipment_id, sensor) from that equipment's
own historian history (mean +/- 3 sigma from a stable baseline period),
matching how a real SPC dashboard would establish limits before monitoring
live-fed data for excursions.

Rules implemented (subset of the classic Western Electric rules, applied to
each sensor stream per equipment):
  Rule 1: Any single point beyond 3 sigma from the center line.
  Rule 2: 2 out of 3 consecutive points beyond 2 sigma on the same side.
  Rule 3: 4 out of 5 consecutive points beyond 1 sigma on the same side.
  Rule 4: 8 consecutive points on the same side of the center line (a run,
          indicating a sustained shift/trend rather than random variation).
"""
import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd

DATA_DIR = Path(__file__).parent / "data"
DB_PATH = DATA_DIR / "process_monitoring.db"
OUTPUT_DIR = Path(__file__).parent / "output"

SENSORS = ["temperature_c", "pressure_bar", "flow_lpm"]
BASELINE_FRACTION = 0.3  # first 30% of an equipment's history establishes control limits


def load_historian(conn):
    return pd.read_sql(
        "SELECT * FROM historian_sensor_data ORDER BY equipment_id, timestamp", conn,
        parse_dates=["timestamp"],
    )


def compute_control_limits(series: pd.Series):
    """Baseline mean/sigma from the first BASELINE_FRACTION of points."""
    n_baseline = max(int(len(series) * BASELINE_FRACTION), 30)
    baseline = series.iloc[:n_baseline]
    mu = baseline.mean()
    sigma = baseline.std(ddof=1)
    return mu, sigma


def western_electric_rules(values: np.ndarray, mu: float, sigma: float):
    """
    Return a list of dicts, one per rule violation found, with rule name,
    index of the triggering point, and a description. Vectorized-ish scan
    over the z-scored series.
    """
    z = (values - mu) / sigma
    n = len(z)
    violations = []

    # Rule 1: single point beyond 3 sigma
    for i in np.where(np.abs(z) > 3)[0]:
        violations.append({
            "rule": "WE Rule 1",
            "index": int(i),
            "description": f"Point {abs(z[i]):.2f} sigma from center line (>3 sigma)",
        })

    # Rule 2: 2 of 3 consecutive beyond 2 sigma, same side
    for i in range(n - 2):
        window = z[i:i + 3]
        pos = np.sum(window > 2)
        neg = np.sum(window < -2)
        if pos >= 2 or neg >= 2:
            violations.append({
                "rule": "WE Rule 2",
                "index": int(i + 2),
                "description": "2 of 3 consecutive points beyond 2 sigma on same side",
            })

    # Rule 3: 4 of 5 consecutive beyond 1 sigma, same side
    for i in range(n - 4):
        window = z[i:i + 5]
        pos = np.sum(window > 1)
        neg = np.sum(window < -1)
        if pos >= 4 or neg >= 4:
            violations.append({
                "rule": "WE Rule 3",
                "index": int(i + 4),
                "description": "4 of 5 consecutive points beyond 1 sigma on same side",
            })

    # Rule 4: 8 consecutive points on same side of center line
    signs = np.sign(z)
    run_len = 1
    for i in range(1, n):
        if signs[i] == signs[i - 1] and signs[i] != 0:
            run_len += 1
        else:
            run_len = 1
        if run_len >= 8:
            violations.append({
                "rule": "WE Rule 4",
                "index": int(i),
                "description": "8 consecutive points on same side of center line",
            })
    return violations


def run_spc(historian_df: pd.DataFrame) -> pd.DataFrame:
    """Run SPC rules per (equipment, sensor) and return a flat alerts table."""
    alerts = []
    for equip, g in historian_df.groupby("equipment_id"):
        g = g.sort_values("timestamp").reset_index(drop=True)
        for sensor in SENSORS:
            values = g[sensor].to_numpy()
            mu, sigma = compute_control_limits(g[sensor])
            if sigma == 0 or np.isnan(sigma):
                continue
            violations = western_electric_rules(values, mu, sigma)
            for v in violations:
                idx = v["index"]
                alerts.append({
                    "equipment_id": equip,
                    "line_id": g.loc[idx, "line_id"],
                    "batch_id": g.loc[idx, "batch_id"],
                    "sensor": sensor,
                    "timestamp": g.loc[idx, "timestamp"],
                    "value": values[idx],
                    "center_line": mu,
                    "sigma": sigma,
                    "z_score": (values[idx] - mu) / sigma,
                    "rule_triggered": v["rule"],
                    "description": v["description"],
                    "severity": "Critical" if v["rule"] == "WE Rule 1" else "Warning",
                })
    alerts_df = pd.DataFrame(alerts)
    if not alerts_df.empty:
        alerts_df = alerts_df.sort_values("timestamp").reset_index(drop=True)
    return alerts_df


def main():
    OUTPUT_DIR.mkdir(exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    historian_df = load_historian(conn)
    conn.close()

    print(f"Loaded {len(historian_df)} historian samples for SPC analysis.")
    alerts_df = run_spc(historian_df)
    print(f"Generated {len(alerts_df)} SPC rule-violation alerts.")

    alerts_path = OUTPUT_DIR / "spc_alerts.csv"
    alerts_df.to_csv(alerts_path, index=False)
    print(f"Wrote alerts to {alerts_path}")

    if not alerts_df.empty:
        print("\nExample alerts:")
        print(alerts_df.head(5).to_string(index=False))
        print("\nAlert counts by rule:")
        print(alerts_df["rule_triggered"].value_counts().to_string())

    return alerts_df


if __name__ == "__main__":
    main()

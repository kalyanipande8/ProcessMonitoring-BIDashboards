"""
Simulate MES (Manufacturing Execution System) batch-execution records and
historian time-series sensor data (temperature / pressure / flow) across
multiple manufacturing lines, and load both into a real SQLite database
(mimicking the "SQL" layer named in the source resume bullet).

All data is synthetically simulated with a fixed random seed (42). No real
plant, equipment, or process data is used anywhere.
"""
import sqlite3
import numpy as np
import pandas as pd
from pathlib import Path

SEED = 42
rng = np.random.default_rng(SEED)

DATA_DIR = Path(__file__).parent / "data"
DATA_DIR.mkdir(exist_ok=True)
DB_PATH = DATA_DIR / "process_monitoring.db"

LINES = [f"LINE-{i:02d}" for i in range(1, 6)]  # 5 manufacturing lines
EQUIPMENT_PER_LINE = {
    line: [f"{line}-REACTOR", f"{line}-PUMP", f"{line}-DRYER"] for line in LINES
}
N_BATCHES_PER_LINE = 40  # 5 lines x 40 batches = 200 batches
SAMPLE_INTERVAL_MIN = 5  # historian sample every 5 minutes
BATCH_DURATION_HOURS = 8

# One line (LINE-03) and one equipment unit will have an injected degrading
# bearing/seal wear pattern -- a real slow drift in vibration-proxy pressure
# noise + rising temperature baseline -- for the predictive-maintenance
# early-warning signal to detect. One batch on LINE-05 will have an injected
# hard SPC violation (temperature runaway) for the alerting sanity check.
DEGRADING_LINE = "LINE-03"
DEGRADING_EQUIPMENT = f"{DEGRADING_LINE}-PUMP"
SPC_VIOLATION_LINE = "LINE-05"
SPC_VIOLATION_BATCH_INDEX = 10  # 0-indexed batch on that line


def simulate_mes_batches():
    """One row per batch execution record."""
    records = []
    batch_id = 1
    start = pd.Timestamp("2025-01-01")
    for line in LINES:
        line_start = start
        for b in range(N_BATCHES_PER_LINE):
            product = rng.choice(["API-A", "API-B", "Intermediate-C"], p=[0.5, 0.3, 0.2])
            planned_duration_min = BATCH_DURATION_HOURS * 60
            actual_duration_min = planned_duration_min + rng.normal(0, 15)
            yield_pct = np.clip(rng.normal(94.5, 2.5), 80, 100)
            operator = f"OP-{rng.integers(1, 9):02d}"
            status = "Completed" if rng.random() > 0.03 else "Deviation"
            batch_start = line_start
            batch_end = batch_start + pd.Timedelta(minutes=actual_duration_min)
            records.append(
                {
                    "batch_id": f"B-{batch_id:05d}",
                    "line_id": line,
                    "product": product,
                    "batch_start": batch_start,
                    "batch_end": batch_end,
                    "planned_duration_min": planned_duration_min,
                    "actual_duration_min": round(actual_duration_min, 1),
                    "yield_pct": round(yield_pct, 2),
                    "operator_id": operator,
                    "status": status,
                }
            )
            batch_id += 1
            # small gap between batches
            line_start = batch_end + pd.Timedelta(minutes=int(rng.integers(15, 90)))
    return pd.DataFrame(records)


def simulate_historian_sensor_data(mes_df: pd.DataFrame):
    """
    One row per (equipment, timestamp) sample at SAMPLE_INTERVAL_MIN
    resolution, spanning each batch's run window. Sensors: temperature (C),
    pressure (bar), flow (L/min). Historian data is generated per equipment
    unit tied to the line that ran during that batch window.
    """
    rows = []
    for line in LINES:
        line_batches = mes_df[mes_df["line_id"] == line].sort_values("batch_start")
        for equip in EQUIPMENT_PER_LINE[line]:
            # base process setpoints per equipment type
            if "REACTOR" in equip:
                temp_base, temp_sd = 75.0, 1.2
                pres_base, pres_sd = 2.5, 0.15
                flow_base, flow_sd = 40.0, 2.0
            elif "PUMP" in equip:
                temp_base, temp_sd = 45.0, 1.0
                pres_base, pres_sd = 4.0, 0.2
                flow_base, flow_sd = 60.0, 2.5
            else:  # DRYER
                temp_base, temp_sd = 90.0, 1.5
                pres_base, pres_sd = 1.0, 0.08
                flow_base, flow_sd = 20.0, 1.5

            for bi, batch in enumerate(line_batches.itertuples()):
                n_samples = int(
                    (batch.batch_end - batch.batch_start).total_seconds()
                    / 60
                    / SAMPLE_INTERVAL_MIN
                )
                timestamps = pd.date_range(
                    batch.batch_start, periods=n_samples, freq=f"{SAMPLE_INTERVAL_MIN}min"
                )

                # --- predictive maintenance degradation injection ---
                # For the designated degrading equipment, apply a slow linear
                # drift in temperature baseline and increasing pressure noise
                # (vibration proxy) across the sequence of batches on that
                # line, simulating bearing/seal wear that worsens over time.
                degrade_frac = 0.0
                if equip == DEGRADING_EQUIPMENT:
                    degrade_frac = bi / max(N_BATCHES_PER_LINE - 1, 1)  # 0 -> 1 over batches
                temp_drift = degrade_frac * 8.0  # up to +8C baseline drift by last batch
                noise_amp = 1.0 + degrade_frac * 3.0  # noise grows up to 4x

                temps = rng.normal(temp_base + temp_drift, temp_sd * noise_amp, n_samples)
                press = rng.normal(pres_base, pres_sd * noise_amp, n_samples)
                flows = rng.normal(flow_base, flow_sd, n_samples)

                # --- SPC hard-violation injection ---
                # On the designated batch, inject a real temperature runaway:
                # a sustained shift of +4 sigma above the mean for a run of
                # samples, which Western Electric Rule 1 (single point beyond
                # 3-sigma) and Rule 2 (run of points on one side / trending)
                # should catch.
                if (
                    line == SPC_VIOLATION_LINE
                    and equip == f"{SPC_VIOLATION_LINE}-REACTOR"
                    and bi == SPC_VIOLATION_BATCH_INDEX
                ):
                    mid = n_samples // 2
                    runaway_len = min(10, n_samples - mid)
                    ramp = np.linspace(0, 6 * temp_sd, runaway_len)
                    temps[mid : mid + runaway_len] += ramp

                for ts, t, p, f in zip(timestamps, temps, press, flows):
                    rows.append(
                        {
                            "equipment_id": equip,
                            "line_id": line,
                            "batch_id": batch.batch_id,
                            "timestamp": ts,
                            "temperature_c": round(float(t), 3),
                            "pressure_bar": round(float(p), 4),
                            "flow_lpm": round(float(f), 3),
                        }
                    )
    return pd.DataFrame(rows)


def load_to_sqlite(mes_df: pd.DataFrame, historian_df: pd.DataFrame):
    if DB_PATH.exists():
        DB_PATH.unlink()
    conn = sqlite3.connect(DB_PATH)
    mes_df.to_sql("mes_batches", conn, index=False)
    historian_df.to_sql("historian_sensor_data", conn, index=False)
    conn.execute("CREATE INDEX idx_hist_equip_ts ON historian_sensor_data(equipment_id, timestamp)")
    conn.execute("CREATE INDEX idx_hist_batch ON historian_sensor_data(batch_id)")
    conn.execute("CREATE INDEX idx_mes_line ON mes_batches(line_id)")
    conn.commit()
    conn.close()


def main():
    print("Simulating MES batch-execution records...")
    mes_df = simulate_mes_batches()
    print(f"  {len(mes_df)} batch records across {mes_df['line_id'].nunique()} lines")

    print("Simulating historian sensor time-series...")
    historian_df = simulate_historian_sensor_data(mes_df)
    print(f"  {len(historian_df)} sensor samples across "
          f"{historian_df['equipment_id'].nunique()} equipment units")

    print(f"Loading into SQLite database at {DB_PATH} ...")
    load_to_sqlite(mes_df, historian_df)

    mes_df.to_csv(DATA_DIR / "mes_batches.csv", index=False)
    historian_df.to_csv(DATA_DIR / "historian_sensor_data.csv", index=False)
    print("Done.")
    return mes_df, historian_df


if __name__ == "__main__":
    main()

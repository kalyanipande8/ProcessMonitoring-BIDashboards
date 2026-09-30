# Process Monitoring & Real-Time BI Dashboards

> **Note on Spotfire/Tableau:** the resume bullet behind this project names
> **Spotfire** and **Tableau** as the dashboard tools. Both are licensed
> desktop applications with no headless/CLI runtime available in this
> environment, so **neither was installed, opened, or run here** -- there is
> no `.dxp` (Spotfire) file, no `.twbx` (Tableau) workbook, and no
> screenshot of a live dashboard in this repo. What *was* built, in full:
> the complete Python pipeline (simulate MES/historian data -> load into a
> real SQL database -> run SPC/alerting -> compute a predictive-maintenance
> signal) that a Spotfire or Tableau dashboard would sit on top of, four
> clean, documented, BI-tool-ready CSV extracts (`output/bi_extract_*.csv`),
> matplotlib figures standing in for what the dashboards' views would show,
> and a written, view-by-view description of what the actual Spotfire and
> Tableau dashboards would contain if built from those extracts (see "What
> the Spotfire/Tableau dashboard would show" in `output/report.md`). This
> mirrors how the sibling project `ProtocolDeviationAnalytics` handled an
> unavailable Tableau runtime -- state plainly what could and could not be
> executed rather than implying a tool ran when it didn't.

> **Disclaimer:** All data in this project is **synthetically simulated** by
> `simulate_mes_historian_data.py` using a fixed random seed (42). No real
> plant, equipment, batch, or sensor data from any manufacturer is used
> anywhere. Process setpoints, batch yields, and the injected degradation/
> violation patterns are plausible, engineering-informed synthetic values,
> not measurements from a real manufacturing line.

## What it does

Simulates MES batch-execution records and historian time-series sensor data
across multiple manufacturing lines, loads both into a real SQLite ("SQL")
database, runs real Statistical Process Control (SPC) logic against the
historian data to generate automated alerts, computes a real trend-based
predictive-maintenance early-warning signal, and produces the BI-tool-ready
extracts and written dashboard design that a real-time Spotfire/Tableau
process-monitoring dashboard would be built from.

1. **`simulate_mes_historian_data.py`** simulates **200 MES batch-execution
   records** (batch id, product, planned vs. actual duration, yield,
   operator, status) across **5 manufacturing lines** (`LINE-01`..`LINE-05`),
   each with 3 equipment units (Reactor, Pump, Dryer -- **15 equipment units**
   total). For each batch it simulates **historian sensor time-series**
   (temperature, pressure, flow) at 5-minute resolution for the batch's
   equipment. It injects two realistic anomalies for the downstream logic to
   detect: (a) a slow, worsening bearing/seal-wear drift (rising temperature
   baseline + growing pressure noise) on `LINE-03-PUMP` across its 40
   batches, standing in for a real predictive-maintenance case, and (b) a
   sustained temperature-runaway spike on one `LINE-05-REACTOR` batch,
   standing in for a hard SPC excursion. Both tables are loaded into a real
   SQLite database at `data/process_monitoring.db`.

2. **`spc_alerting.py`** establishes per-equipment, per-sensor control
   limits (mean +/- sigma from a baseline period) and evaluates each
   sensor stream against 4 Western Electric SPC rules (single point beyond
   3 sigma; 2-of-3 beyond 2 sigma; 4-of-5 beyond 1 sigma; 8 consecutive
   points on one side of the center line), writing every violation as a row
   in `output/spc_alerts.csv` -- the automated-alerts deliverable named in
   the resume bullet.

3. **`predictive_maintenance.py`** computes, per equipment unit, a rolling
   10-batch linear-regression trend (OLS slope + t-test) on mean
   temperature and pressure variability, flagging any equipment with a
   statistically significant (t > 2.0) rising trend as
   `maintenance_recommended` -- a real, simple statistical early-warning
   indicator, not a hardcoded flag.

4. **`build_dashboard_extracts.py`** builds four BI-tool-ready CSV extracts
   (hourly sensor trends, batch summary, SPC alerts, predictive-maintenance
   signals), 3 matplotlib figures standing in for the dashboards' views, and
   `output/report.md` with real computed numbers plus the written
   Spotfire/Tableau dashboard-view descriptions.

5. **`run_all.py`** orchestrates all of the above.

6. **`tests/test_pipeline.py`** (pytest, 7 tests) checks the MES/historian
   record counts, the SQLite database is real and queryable, the injected
   SPC violation batch is correctly detected by the rule engine (plus a
   direct unit-level test of the rule function on a clean vs. a 5-sigma-
   spiked synthetic signal), the injected degrading pump is correctly
   flagged by the predictive-maintenance trend test while a healthy peer is
   not, and every dashboard-extract deliverable file is produced.

## How to run

```bash
python3 -m venv .venv && source .venv/bin/activate   # or use your existing env
pip install -r requirements.txt
python run_all.py
pytest -q
```

(In this environment the pipeline was run against an existing Python
virtual environment that already had numpy/pandas/scipy/matplotlib/pytest
installed, since disk space on this machine was intermittently very low --
a fresh `pip install` into a new venv can fail with "No space left on
device" under that condition. `requirements.txt` is provided for a normal
machine with free disk space.)

## Results

_All numbers below are copied directly from an actual run of `python
run_all.py` -- nothing is hardcoded. Fixed random seed makes them
reproducible._

- **200 MES batch-execution records** across **5 manufacturing lines** and
  **3 products** (API-A, API-B, Intermediate-C).
- **57,156 historian sensor samples** (temperature, pressure, flow) across
  **15 equipment units**, at 5-minute resolution, loaded into a real SQLite
  database (`data/process_monitoring.db`, ~8.6 MB, 2 tables, indexed on
  equipment/timestamp and batch id).
- **9,532 automated SPC alerts** generated by Western Electric rule logic
  run against the real historian data:
  - By rule: WE Rule 1 (single point > 3 sigma) = 1,813; WE Rule 2 (2-of-3
    beyond 2 sigma) = 2,344; WE Rule 3 (4-of-5 beyond 1 sigma) = 2,818;
    WE Rule 4 (8-point run) = 2,557.
  - By severity: 1,813 Critical (Rule 1), 7,719 Warning.
  - Example real alert (from `output/spc_alerts.csv`): equipment
    `LINE-01-PUMP`, batch `B-00001`, sensor `pressure_bar`, value 3.4112 bar
    vs. a center line of 4.0095 bar (sigma 0.1989) -> z = -3.01 ->
    **WE Rule 1, Critical**, "Point 3.01 sigma from center line (>3 sigma)".
  - The injected temperature-runaway batch on `LINE-05-REACTOR` is
    correctly caught: its Rule-1 alert lands on the exact batch the
    anomaly was injected into (verified by
    `tests/test_pipeline.py::test_spc_detects_injected_violation`).
- **Predictive maintenance: 1 of 15 equipment units flagged.**
  `LINE-03-PUMP` (the equipment with the injected degradation pattern) shows
  a temperature trend slope of **+0.1603 C/batch** (t = 9.151,
  p = 2.0e-05) and a pressure-variability slope of **+0.02697 bar/batch**
  (t = 6.412, p = 2.1e-04) over its most recent 10 batches -- both far past
  the t > 2.0 significance threshold -- while all 14 other (non-injected)
  equipment units are correctly left unflagged.

All 7 pytest tests pass (`pytest -q` -> `7 passed`).

## SQL database schema (`data/process_monitoring.db`)

- **`mes_batches`**: one row per batch (`batch_id`, `line_id`, `product`,
  `batch_start`, `batch_end`, `planned_duration_min`,
  `actual_duration_min`, `yield_pct`, `operator_id`, `status`).
- **`historian_sensor_data`**: one row per (equipment, timestamp) sample
  (`equipment_id`, `line_id`, `batch_id`, `timestamp`, `temperature_c`,
  `pressure_bar`, `flow_lpm`).

## BI extract files (`output/bi_extract_*.csv`)

Identical schema usable by either Tableau or Spotfire:

| File | Grain | Purpose |
|---|---|---|
| `bi_extract_hourly_sensor_trends.csv` | equipment x hour | live sensor-trend view |
| `bi_extract_batch_summary.csv` | batch | MES + sensor rollup / yield analysis |
| `bi_extract_spc_alerts.csv` | alert | automated-alerts feed |
| `bi_extract_predictive_maintenance.csv` | equipment | predictive-maintenance tile |

## What the Spotfire/Tableau dashboards would show

See "What the Spotfire real-time process dashboard would show" and "What
the Tableau workbook would show" in `output/report.md` for the full
sheet/view-by-view description (Line Overview, Live Sensor Trend with
control-limit reference bands, Automated Alerts panel, Predictive
Maintenance tile, and Batch Genealogy/Yield view) of what would be built in
actual Spotfire or Tableau from the extracts above, if those tools were
available.

## Figures

- `output/figures/equipment_temperature_trend.png` -- daily mean
  temperature, degrading pump (`LINE-03-PUMP`) vs. a healthy peer
- `output/figures/spc_alerts_by_rule.png` -- SPC alert counts by rule and
  severity
- `output/figures/spc_control_chart_example.png` -- control chart for the
  injected temperature-runaway batch, with +-1/2/3 sigma reference lines

## Project structure

```
simulate_mes_historian_data.py   # MES batches + historian sensor data -> SQLite (data/process_monitoring.db)
spc_alerting.py                  # Western Electric SPC rules -> output/spc_alerts.csv
predictive_maintenance.py        # rolling-trend early-warning signal -> output/predictive_maintenance_signals.csv
build_dashboard_extracts.py      # BI extracts, figures, report.md -> output/
run_all.py                       # orchestrates the full pipeline
tests/test_pipeline.py           # pytest checks incl. injected-anomaly sanity checks
```

## Tech stack

- Python 3
- numpy / pandas for data simulation and wrangling
- SQLite (via `sqlite3`/pandas `to_sql`) for the "SQL" data layer named in
  the resume bullet
- scipy.stats for the predictive-maintenance trend test (OLS + t-test)
- matplotlib for figures (standing in for Spotfire/Tableau views)
- pytest
- Spotfire, Tableau (named in the source resume bullet; **not
  available/executed in this environment** -- see note at the top of this
  README)

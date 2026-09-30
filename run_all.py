"""Orchestrates the full pipeline: simulate -> load SQL -> SPC/alerts -> predictive maintenance -> dashboard extracts/report."""
import simulate_mes_historian_data
import spc_alerting
import predictive_maintenance
import build_dashboard_extracts


def main():
    print("=" * 70)
    print("STEP 1/4: Simulating MES + historian data, loading into SQLite")
    print("=" * 70)
    simulate_mes_historian_data.main()

    print("\n" + "=" * 70)
    print("STEP 2/4: Running SPC rules against historian data")
    print("=" * 70)
    spc_alerting.main()

    print("\n" + "=" * 70)
    print("STEP 3/4: Computing predictive maintenance early-warning signals")
    print("=" * 70)
    predictive_maintenance.main()

    print("\n" + "=" * 70)
    print("STEP 4/4: Building BI dashboard extracts, figures, and report")
    print("=" * 70)
    build_dashboard_extracts.main()

    print("\nPipeline complete. See output/report.md for full results.")


if __name__ == "__main__":
    main()

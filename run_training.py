"""CLI entry point for Phase 2 model training and export."""

import argparse
from pathlib import Path

from src.models.train import run_training
from src.models.evaluate import evaluate_model
from src.models.export import export_model, export_team_reports


def main() -> None:
    parser = argparse.ArgumentParser(description="Train and export models for lacrosse analytics")
    parser.add_argument("--no-export", action="store_true", help="Skip exporting artifacts")
    parser.add_argument("--no-reports", action="store_true", help="Skip generating team reports")
    args = parser.parse_args()

    model, X_test, y_test, df, feature_names = run_training()
    metrics = evaluate_model(model, X_test, y_test)

    if not args.no_export:
        paths = export_model(model, feature_names, metrics)
        print(f"\nExported model to {paths['model']}")
        print(f"Exported feature importance to {paths['importance']}")

    if not args.no_export and not args.no_reports:
        reports_dir = export_team_reports(df, model, feature_names)
        count = len(list(reports_dir.glob("*.txt")))
        print(f"Exported {count} team reports to {reports_dir}")


if __name__ == "__main__":
    main()

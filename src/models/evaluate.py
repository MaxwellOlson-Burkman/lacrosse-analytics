"""Model evaluation: MAE, R2, residual plots, predicted vs actual."""

from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
from sklearn.metrics import mean_absolute_error, r2_score
import matplotlib.pyplot as plt

def evaluate_model(
    model,
    X: pd.DataFrame,
    y: pd.Series,
    prefix: str = "",
) -> dict[str, float]:
    """Compute MAE and R2 for a model."""
    pred = model.predict(X)
    return {
        f"{prefix}mae": mean_absolute_error(y, pred),
        f"{prefix}r2": r2_score(y, pred),
    }


def plot_residuals(
    y_true: pd.Series,
    y_pred: np.ndarray,
    title: str = "Residual Plot",
    save_path: Optional[Path] = None,
) -> None:
    """Plot residuals (actual - predicted) vs predicted values."""

    residuals = y_true.values - y_pred
    plt.figure(figsize=(8, 6))
    plt.scatter(y_pred, residuals, alpha=0.5, s=20)
    plt.axhline(y=0, color="r", linestyle="--")
    plt.xlabel("Predicted winning_percentage")
    plt.ylabel("Residual (Actual - Predicted)")
    plt.title(title)
    plt.tight_layout()
    if save_path:
        plt.savefig(save_path, dpi=150)
    plt.show()


def plot_predicted_vs_actual(
    y_true: pd.Series,
    y_pred: np.ndarray,
    title: str = "Predicted vs Actual",
    save_path: Optional[Path] = None,
) -> None:
    """Scatter plot of predicted vs actual values."""

    plt.figure(figsize=(8, 8))
    plt.scatter(y_true, y_pred, alpha=0.5, s=30)
    min_val = min(y_true.min(), y_pred.min())
    max_val = max(y_true.max(), y_pred.max())
    plt.plot([min_val, max_val], [min_val, max_val], "r--", label="Perfect prediction")
    plt.xlabel("Actual winning_percentage")
    plt.ylabel("Predicted winning_percentage")
    plt.title(title)
    plt.legend()
    plt.tight_layout()
    if save_path:
        plt.savefig(save_path, dpi=150)
    plt.show()


def plot_feature_importance(
    model,
    feature_names: list[str],
    top_n: int = 15,
    save_path: Optional[Path] = None,
) -> None:
    """Bar chart of feature importances (for tree-based models)."""


    if not hasattr(model, "feature_importances_"):
        print("Model has no feature_importances_ attribute.")
        return

    imp = model.feature_importances_
    idx = np.argsort(imp)[::-1][:top_n]
    names = [feature_names[i] for i in idx]
    values = imp[idx]

    plt.figure(figsize=(10, 6))
    plt.barh(range(len(names)), values, align="center")
    plt.yticks(range(len(names)), names)
    plt.gca().invert_yaxis()
    plt.xlabel("Feature Importance")
    plt.title("Top Feature Importances")
    plt.tight_layout()
    if save_path:
        plt.savefig(save_path, dpi=150)
    plt.show()

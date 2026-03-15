"""Training pipeline: RF and GBR with hyperparameter tuning."""

from pathlib import Path
from typing import Any, Optional

import pandas as pd
import yaml
from sklearn.ensemble import GradientBoostingRegressor, RandomForestRegressor
from sklearn.model_selection import GridSearchCV, cross_val_score
from sklearn.linear_model import LinearRegression

from .feature_engineering import (
    TARGET_COL,
    get_feature_columns,
    load_and_prepare,
    prepare_features,
)


def load_model_config(config_path: Optional[Path] = None) -> dict[str, Any]:
    """Load model configuration from YAML."""
    if config_path is None:
        project_root = Path(__file__).resolve().parent.parent.parent
        config_path = project_root / "config" / "model_config.yaml"
    with open(config_path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def train_test_split_by_year(
    df: pd.DataFrame,
    test_year: int = 2024,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.Series, pd.Series]:
    """Split data by academic year. Hold out test_year as test set."""
    train_df = df[df["academic_year"] != test_year].copy()
    test_df = df[df["academic_year"] == test_year].copy()

    feature_cols = get_feature_columns(include_derived=True, include_clearing_margin=False)
    X_train, _ = prepare_features(train_df, feature_cols=feature_cols, include_division=True)
    X_test, _ = prepare_features(test_df, feature_cols=feature_cols, include_division=True)
    y_train = train_df[TARGET_COL]
    y_test = test_df[TARGET_COL]

    return X_train, X_test, y_train, y_test


def train_linear_baseline(
    X_train: pd.DataFrame,
    y_train: pd.Series,
) -> tuple[LinearRegression, float]:
    """Train a simple linear regression baseline."""
    model = LinearRegression()
    model.fit(X_train, y_train)
    r2 = model.score(X_train, y_train)
    return model, r2


def train_random_forest(
    X_train: pd.DataFrame,
    y_train: pd.Series,
    config: Optional[dict] = None,
    cv: int = 5,
) -> tuple[RandomForestRegressor, dict]:
    """Train Random Forest with GridSearchCV."""
    if config is None:
        config = load_model_config()
    rf_config = config.get("random_forest", {})

    param_grid = {
        "n_estimators": rf_config.get("n_estimators", [100, 200]),
        "max_depth": rf_config.get("max_depth", [8, 12, None]),
        "min_samples_leaf": rf_config.get("min_samples_leaf", [1, 2, 4]),
    }

    model = RandomForestRegressor(random_state=42)
    grid = GridSearchCV(
        model,
        param_grid,
        cv=cv,
        scoring="neg_mean_absolute_error",
        n_jobs=-1,
        verbose=1,
    )
    grid.fit(X_train, y_train)
    return grid.best_estimator_, {
        "best_params": grid.best_params_,
        "best_cv_mae": -grid.best_score_,
    }


def train_gradient_boosting(
    X_train: pd.DataFrame,
    y_train: pd.Series,
    config: Optional[dict] = None,
    cv: int = 5,
) -> tuple[GradientBoostingRegressor, dict]:
    """Train Gradient Boosting with GridSearchCV."""
    if config is None:
        config = load_model_config()
    gb_config = config.get("gradient_boosting", {})

    param_grid = {
        "n_estimators": gb_config.get("n_estimators", [100, 200]),
        "learning_rate": gb_config.get("learning_rate", [0.05, 0.1]),
        "max_depth": gb_config.get("max_depth", [4, 6]),
        "subsample": gb_config.get("subsample", [0.8, 1.0]),
    }

    model = GradientBoostingRegressor(random_state=42)
    grid = GridSearchCV(
        model,
        param_grid,
        cv=cv,
        scoring="neg_mean_absolute_error",
        n_jobs=-1,
        verbose=1,
    )
    grid.fit(X_train, y_train)
    return grid.best_estimator_, {
        "best_params": grid.best_params_,
        "best_cv_mae": -grid.best_score_,
    }


def run_training(
    data_path: Optional[Path] = None,
    config_path: Optional[Path] = None,
) -> tuple[Any, pd.DataFrame, pd.Series, pd.DataFrame, list[str]]:
    """
    Run full training pipeline. Returns (best_model, X_test, y_test, df, feature_names).
    """
    config = load_model_config(config_path)
    test_year = config.get("test_year", 2024)
    cv = config.get("cv_folds", 5)

    df = load_and_prepare(data_path)
    X_train, X_test, y_train, y_test = train_test_split_by_year(df, test_year=test_year)
    feature_names = list(X_train.columns)

    # Linear baseline
    lr_model, lr_r2 = train_linear_baseline(X_train, y_train)
    lr_mae = (lr_model.predict(X_test) - y_test).abs().mean()
    print(f"Linear Regression: train R2={lr_r2:.4f}, test MAE={lr_mae:.4f}")

    # Random Forest
    rf_model, rf_info = train_random_forest(X_train, y_train, config=config, cv=cv)
    rf_mae = (rf_model.predict(X_test) - y_test).abs().mean()
    print(f"Random Forest: CV MAE={rf_info['best_cv_mae']:.4f}, test MAE={rf_mae:.4f}")

    # Gradient Boosting
    gb_model, gb_info = train_gradient_boosting(X_train, y_train, config=config, cv=cv)
    gb_mae = (gb_model.predict(X_test) - y_test).abs().mean()
    print(f"Gradient Boosting: CV MAE={gb_info['best_cv_mae']:.4f}, test MAE={gb_mae:.4f}")

    # Pick best by test MAE
    candidates = [
        ("Linear", lr_model, lr_mae),
        ("RandomForest", rf_model, rf_mae),
        ("GradientBoosting", gb_model, gb_mae),
    ]
    best_name, best_model, best_mae = min(candidates, key=lambda x: x[2])
    print(f"\nBest model: {best_name} (test MAE={best_mae:.4f})")

    return best_model, X_test, y_test, df, feature_names

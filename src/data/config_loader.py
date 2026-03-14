"""Load and validate pipeline configuration from YAML."""

from pathlib import Path
from typing import Any

import yaml


def load_config(config_path: str | Path | None = None) -> dict[str, Any]:
    """Load pipeline configuration from YAML file.

    Args:
        config_path: Path to config file. Defaults to config/data_config.yaml
            relative to project root.

    Returns:
        Configuration dictionary.
    """
    if config_path is None:
        project_root = Path(__file__).resolve().parent.parent.parent
        config_path = project_root / "config" / "data_config.yaml"

    path = Path(config_path)
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")

    with open(path, encoding="utf-8") as f:
        config = yaml.safe_load(f)

    if not config:
        raise ValueError("Config file is empty")

    return config

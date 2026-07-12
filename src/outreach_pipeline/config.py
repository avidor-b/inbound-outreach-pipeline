"""Configuration loader with env var substitution and validation."""
import os
import re
import yaml
from pathlib import Path

class ConfigError(Exception):
    """Raised when configuration is invalid or missing."""
    pass

def _substitute_env_vars(value):
    """Replace ${VAR} with environment variable value."""
    if not isinstance(value, str):
        return value
    pattern = r'\$\{([^}]+)\}'
    def replacer(match):
        var_name = match.group(1)
        env_val = os.environ.get(var_name)
        if env_val is None:
            return match.group(0)  # Keep original if not set
        return env_val
    return re.sub(pattern, replacer, value)

def _process_config(obj):
    """Recursively substitute env vars in config."""
    if isinstance(obj, dict):
        return {k: _process_config(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [_process_config(item) for item in obj]
    else:
        return _substitute_env_vars(obj)

def load_config(config_path=None):
    """Load and validate config.yaml.

    Resolution order for the config file path:
      1. Explicit `config_path` argument (tests, one-off tooling)
      2. CONFIG_PATH env var (container deploys — we set
         CONFIG_PATH=/etc/outreach/config.yaml in the Dockerfile)
      3. 'config.yaml' relative to current working directory (local dev)
    """
    if config_path is None:
        config_path = os.environ.get("CONFIG_PATH", "config.yaml")
    path = Path(config_path)
    if not path.exists():
        raise ConfigError(f"Config file not found: {config_path}")
    
    with open(path) as f:
        raw = yaml.safe_load(f)
    
    config = _process_config(raw)
    
    # Validate required fields
    required = ["reports_path", "report_timezone"]
    for field in required:
        if field not in config:
            raise ConfigError(f"Missing required config field: {field}")
    
    # Set defaults
    config.setdefault("rate_limit_per_10s", 90)
    config.setdefault("safety_mode", True)
    config.setdefault("validation_tolerance", {"abs_diff": 1, "rel_diff_pct": 1})
    
    return config

def load_metrics(metrics_path="metrics.yaml"):
    """Load and validate metrics.yaml."""
    path = Path(metrics_path)
    if not path.exists():
        raise ConfigError(f"Metrics file not found: {metrics_path}")
    
    with open(path) as f:
        data = yaml.safe_load(f)
    
    if "metrics" not in data:
        raise ConfigError("metrics.yaml must contain 'metrics' list")
    
    # Validate each metric has required fields
    required_metric_fields = ["id", "sheet_name"]
    for i, metric in enumerate(data["metrics"]):
        for field in required_metric_fields:
            if field not in metric:
                raise ConfigError(f"Metric {i} missing required field: {field}")
        # Must have either row_key or row_number
        if "row_key" not in metric and "row_number" not in metric:
            raise ConfigError(f"Metric {i} must have either row_key or row_number")
    
    return data

def get_sheet_config(metrics_data, sheet_name):
    """Get sheet configuration by name."""
    for sheet in metrics_data.get("sheets", []):
        if sheet["name"] == sheet_name:
            return sheet
    return {"layout": "simple", "key_column": 1, "header_row": 1}

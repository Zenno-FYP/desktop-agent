"""Config loader for agent."""
import yaml
from pathlib import Path


class Config:
    """Load and access configuration from config.yaml."""

    def __init__(self, config_file: str = None):
        """Load config from YAML file.
        
        Args:
            config_file: path to config.yaml (defaults to ./config/config.yaml)
        """
        if config_file is None:
            config_file = Path(__file__).parent / "config.yaml"
        else:
            config_file = Path(config_file)

        self.config_path = config_file
        self._data = {}

        if config_file.exists():
            with open(config_file, "r", encoding="utf-8") as f:
                self._data = yaml.safe_load(f) or {}
        else:
            raise FileNotFoundError(f"Config file not found: {config_file}")

    def get(self, key: str, default=None):
        """Get config value by dot-separated key.
        
        Examples:
            config.get("sample_interval_sec")
            config.get("db.path")
        """
        keys = key.split(".")
        value = self._data

        for k in keys:
            if isinstance(value, dict):
                value = value.get(k)
                if value is None:
                    return default
            else:
                return default

        return value if value is not None else default

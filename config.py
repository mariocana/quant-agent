"""Configuration loader."""
import yaml
from pathlib import Path
from typing import Any


class Config:
    def __init__(self, config_path: str = "config.yaml"):
        self.path = Path(config_path)
        if not self.path.exists():
            raise FileNotFoundError(
                f"Config file not found: {config_path}. "
                f"Copy config.example.yaml to config.yaml and fill in your values."
            )
        with open(self.path) as f:
            self._data = yaml.safe_load(f)
    
    def get(self, key_path: str, default: Any = None) -> Any:
        """Get a config value using dot notation, e.g. 'claude.api_key'."""
        keys = key_path.split(".")
        value = self._data
        for key in keys:
            if isinstance(value, dict) and key in value:
                value = value[key]
            else:
                return default
        return value
    
    def __getitem__(self, key: str) -> Any:
        return self._data[key]


def load_profile(name: str) -> dict:
    """Carica un profilo da profiles/<name>.yaml"""
    path = Path("profiles") / f"{name}.yaml"
    if not path.exists():
        raise FileNotFoundError(f"Profile not found: {name}")
    with open(path) as f:
        return yaml.safe_load(f)

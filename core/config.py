"""
YAML configuration module for SENTRY.
Loads external configuration for thresholds, limits, and behavior.
"""

import copy
import os
from pathlib import Path
from typing import Any, Dict, Optional, Set

import yaml


DEFAULT_CONFIG = {
    "daemon": {
        "poll_interval_seconds": 3,
        "resume_seconds": 10,
        "cooldown_seconds": 15,
    },
    "thresholds": {
        "low": 0.35,
        "moderate": 0.50,
        "high": 0.70,
        "critical": 0.85,
    },
    "escalation": {
        "low": {
            "cpu_weight": 100,
            "memory_limit_percent": 100,
            "io_weight": 100,
        },
        "moderate": {
            "cpu_weight": 50,
            "memory_limit_percent": 90,
            "io_weight": 50,
        },
        "high": {
            "cpu_weight": 30,
            "memory_limit_percent": 75,
            "io_weight": 30,
        },
        "critical": {
            "cpu_weight": 10,
            "memory_limit_percent": 50,
            "io_weight": 10,
        },
    },
    "metrics": {
        "cpu_weight": 0.5,
        "memory_weight": 0.3,
        "io_weight": 0.2,
        "psi_weight": 0.0,
        "psi_cpu_weight": 0.3,
        "psi_memory_weight": 0.4,
        "psi_io_weight": 0.3,
    },
    "cgroup": {
        "path": "/sys/fs/cgroup/sentry_bg",
        "enabled": True,
    },
    "critical_processes": [
        "systemd",
        "gnome-shell",
        "Xorg",
        "pulseaudio",
        "pipewire",
        "python3",
        "ps",
    ],
    "logging": {
        "json_file": "sentry_audit.json",
        "text_file": "sentry_log.txt",
        "console": True,
        "console_level": "INFO",
    },
}


def resolve_config_path(config_file: Optional[str] = None) -> Path:
    if config_file:
        return Path(config_file)

    env_path = os.environ.get("SENTRY_CONFIG")
    if env_path:
        return Path(env_path)

    return Path(__file__).resolve().parent.parent / "sentry_config.yaml"


def load_config(config_file: Optional[str] = None) -> "ConfigManager":
    return ConfigManager(str(resolve_config_path(config_file)))


class ConfigManager:
    """
    Manages SENTRY configuration from YAML file.
    Falls back to defaults if config file not found.
    """
    
    def __init__(self, config_file: str = "sentry_config.yaml"):
        """
        Initialize config manager.
        
        Args:
            config_file (str): Path to YAML configuration file
        """
        self.config_file = Path(config_file)
        self.config = self._load_config()
    
    def _load_config(self) -> Dict[str, Any]:
        """
        Load configuration from YAML file or use defaults.
        
        Returns:
            dict: Merged configuration
        """
        config = copy.deepcopy(DEFAULT_CONFIG)

        if self.config_file.exists():
            try:
                with open(self.config_file, "r", encoding="utf-8") as handle:
                    user_config = yaml.safe_load(handle) or {}

                config = self._merge_dicts(config, user_config)
                print(f"[SENTRY] Loaded config from {self.config_file}")

            except Exception as exc:
                print(f"[SENTRY] Failed to load config: {exc}, using defaults")
        else:
            print(f"[SENTRY] Config file not found at {self.config_file}, using defaults")

        return config
    
    def _merge_dicts(self, base: Dict, override: Dict) -> Dict:
        """
        Recursively merge override dict into base dict.
        
        Args:
            base (dict): Base configuration
            override (dict): Override values
        
        Returns:
            dict: Merged configuration
        """
        result = base.copy()
        
        for key, value in override.items():
            if key in result and isinstance(result[key], dict) and isinstance(value, dict):
                result[key] = self._merge_dicts(result[key], value)
            else:
                result[key] = value
        
        return result
    
    def get(self, path: str, default: Any = None) -> Any:
        """
        Get configuration value by dot-notation path.
        
        Args:
            path (str): Configuration path (e.g., "daemon.poll_interval_seconds")
            default: Default value if path not found
        
        Returns:
            Configuration value or default
        """
        keys = path.split(".")
        value = self.config
        
        for key in keys:
            if isinstance(value, dict) and key in value:
                value = value[key]
            else:
                return default
        
        return value
    
    def get_dict(self, section: str) -> Dict:
        """
        Get entire configuration section as dict.
        
        Args:
            section (str): Section name (e.g., "daemon", "thresholds")
        
        Returns:
            dict: Configuration section
        """
        return self.config.get(section, {})
    
    # Daemon configuration
    def poll_interval(self) -> int:
        """Get daemon poll interval in seconds."""
        return self.get("daemon.poll_interval_seconds", 3)
    
    def resume_seconds(self) -> int:
        """Get auto-resume timeout in seconds."""
        return self.get("daemon.resume_seconds", 10)
    
    def cooldown_seconds(self) -> int:
        """Get cooldown period in seconds."""
        return self.get("daemon.cooldown_seconds", 15)
    
    # Threshold configuration
    def get_threshold(self, level: str) -> float:
        """
        Get stress threshold for level.
        
        Args:
            level (str): Level name ('low', 'moderate', 'high', 'critical')
        
        Returns:
            float: Threshold value [0, 1]
        """
        return self.get(f"thresholds.{level}", DEFAULT_CONFIG["thresholds"].get(level, 0.0))
    
    def all_thresholds(self) -> Dict[str, float]:
        """Get all stress thresholds."""
        return self.get_dict("thresholds")
    
    # Escalation configuration
    def get_escalation_actions(self, level: str) -> Dict[str, int]:
        """
        Get resource limits for stress level.

        Args:
            level (str): Level name ('low', 'moderate', 'high', 'critical' or uppercase)

        Returns:
            dict: Actions with cpu_weight, memory_limit_percent, io_weight
        """
        level_key = level.lower()
        escalation = self.get_dict("escalation")
        defaults = DEFAULT_CONFIG["escalation"].get(level_key, {})
        return escalation.get(level_key, defaults)
    
    # Metrics configuration
    def metric_weights(self) -> Dict[str, float]:
        """Get metric weights for stress calculation."""
        return self.get_dict("metrics")
    
    # cgroup configuration
    def cgroup_path(self) -> str:
        """Get cgroup path."""
        return self.get("cgroup.path", "/sys/fs/cgroup/sentry_bg")
    
    def cgroup_enabled(self) -> bool:
        """Check if cgroup control is enabled."""
        return self.get("cgroup.enabled", True)
    
    # Critical processes
    def critical_processes(self) -> list:
        """Get list of critical processes to never throttle."""
        return self.get("critical_processes", [])

    def critical_processes_set(self) -> Set[str]:
        """Get critical process names as a set for fast membership checks."""
        return set(self.critical_processes())
    
    # Logging configuration
    def json_log_file(self) -> str:
        """Get JSON log file path."""
        return self.get("logging.json_file", "sentry_audit.json")
    
    def text_log_file(self) -> str:
        """Get text log file path."""
        return self.get("logging.text_file", "sentry_log.txt")
    
    def console_logging_enabled(self) -> bool:
        """Check if console logging is enabled."""
        return self.get("logging.console", True)
    
    def console_log_level(self) -> str:
        """Get console logging level."""
        return self.get("logging.console_level", "INFO")
    
    def save_default_config(self):
        """Generate and save default configuration file."""
        try:
            with open(self.config_file, "w") as f:
                yaml.dump(DEFAULT_CONFIG, f, default_flow_style=False, sort_keys=False)
            print(f"[SENTRY] Default config saved to {self.config_file}")
        except Exception as e:
            print(f"[SENTRY] Failed to save config: {e}")

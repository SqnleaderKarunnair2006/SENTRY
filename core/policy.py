"""
Policy classification module for SENTRY.
Defines stress levels and escalation thresholds.
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.config import ConfigManager

# Stress level thresholds (customize based on workload)
THRESHOLDS = {
    "LOW": 0.35,
    "MODERATE": 0.50,
    "HIGH": 0.70,
    "CRITICAL": 0.85,
}

# Action escalation mapping: stress level → (cpu_weight, memory_limit_percent, io_weight)
ESCALATION_MATRIX = {
    "LOW": {
        "cpu_weight": 100,      # No limit
        "memory_limit_percent": 100,  # No limit
        "io_weight": 100,       # No limit
    },
    "MODERATE": {
        "cpu_weight": 50,       # 50% CPU throttle
        "memory_limit_percent": 90,   # 90% of process memory
        "io_weight": 50,        # 50% I/O throttle
    },
    "HIGH": {
        "cpu_weight": 30,       # 30% CPU throttle
        "memory_limit_percent": 75,   # 75% of process memory
        "io_weight": 30,        # 30% I/O throttle
    },
    "CRITICAL": {
        "cpu_weight": 10,       # 10% CPU throttle
        "memory_limit_percent": 50,   # 50% of process memory (hard limit)
        "io_weight": 10,        # 10% I/O throttle
    },
}


def configure_policy(config: "ConfigManager") -> None:
    """Apply thresholds and escalation matrix from sentry_config.yaml."""
    global THRESHOLDS, ESCALATION_MATRIX

    thresholds = config.all_thresholds()
    THRESHOLDS = {
        "LOW": thresholds.get("low", 0.35),
        "MODERATE": thresholds.get("moderate", 0.50),
        "HIGH": thresholds.get("high", 0.70),
        "CRITICAL": thresholds.get("critical", 0.85),
    }

    ESCALATION_MATRIX = {}
    for level in ["LOW", "MODERATE", "HIGH", "CRITICAL"]:
        ESCALATION_MATRIX[level] = config.get_escalation_actions(level)


def classify_basic(stress_score):
    """
    Classify system stress level based on computed stress score.
    
    When PSI is integrated into the stress score, this classification
    becomes more sensitive to actual kernel contention, not just utilization.
    
    Args:
        stress_score (float): Normalized stress score in [0, 1]
    
    Returns:
        str: One of ["LOW", "MODERATE", "HIGH", "CRITICAL"]
    """
    if stress_score >= THRESHOLDS["CRITICAL"]:
        return "CRITICAL"
    elif stress_score >= THRESHOLDS["HIGH"]:
        return "HIGH"
    elif stress_score >= THRESHOLDS["MODERATE"]:
        return "MODERATE"
    else:
        return "LOW"


def get_action_limits(stress_level):
    """
    Get resource limit actions for a given stress level.
    
    Args:
        stress_level (str): One of ["LOW", "MODERATE", "HIGH", "CRITICAL"]
    
    Returns:
        dict: Actions with cpu_weight, memory_limit, io_weight
    """
    return ESCALATION_MATRIX.get(stress_level, ESCALATION_MATRIX["LOW"])


def is_critical_level(stress_level):
    """Check if stress level requires immediate action."""
    return stress_level in ["HIGH", "CRITICAL"]

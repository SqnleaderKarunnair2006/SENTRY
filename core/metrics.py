import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional

from core.procfs import (
    PROC_ROOT,
    CpuStatSnapshot,
    cpu_usage_percent,
    io_wait_percent,
    read_memory_usage_percent,
    read_psi,
    read_system_stat,
)

if TYPE_CHECKING:
    from core.config import ConfigManager

DEFAULT_METRIC_WEIGHTS = {
    "cpu_weight": 0.5,
    "memory_weight": 0.3,
    "io_weight": 0.2,
    "psi_weight": 0.0,
    "psi_cpu_weight": 0.3,
    "psi_memory_weight": 0.4,
    "psi_io_weight": 0.3,
}

_metric_weights = DEFAULT_METRIC_WEIGHTS.copy()


def configure_metrics(config: "ConfigManager") -> None:
    global _metric_weights
    weights = config.metric_weights()
    _metric_weights = {
        "cpu_weight": weights.get("cpu_weight", DEFAULT_METRIC_WEIGHTS["cpu_weight"]),
        "memory_weight": weights.get("memory_weight", DEFAULT_METRIC_WEIGHTS["memory_weight"]),
        "io_weight": weights.get("io_weight", DEFAULT_METRIC_WEIGHTS["io_weight"]),
        "psi_weight": weights.get("psi_weight", DEFAULT_METRIC_WEIGHTS["psi_weight"]),
        "psi_cpu_weight": weights.get("psi_cpu_weight", DEFAULT_METRIC_WEIGHTS["psi_cpu_weight"]),
        "psi_memory_weight": weights.get("psi_memory_weight", DEFAULT_METRIC_WEIGHTS["psi_memory_weight"]),
        "psi_io_weight": weights.get("psi_io_weight", DEFAULT_METRIC_WEIGHTS["psi_io_weight"]),
    }


@dataclass(frozen=True)
class SystemMetrics:
    cpu_percent: float
    memory_percent: float
    io_wait_percent: float
    stress_score: float
    psi_cpu_some_avg10: Optional[float] = None
    psi_memory_some_avg10: Optional[float] = None
    psi_io_some_avg10: Optional[float] = None
    psi_score: Optional[float] = None


def normalize_psi_avg10(avg10: float) -> float:
    """
    Normalize PSI avg10 value (0-100) to [0, 1] range.
    PSI avg10 can exceed 100 under extreme contention.
    Clamp to [0, 1] for normalized scoring.
    
    Args:
        avg10 (float): PSI avg10 value (percentage)
    
    Returns:
        float: Normalized PSI value in [0, 1]
    """
    return min(1.0, max(0.0, avg10 / 100.0))


def compute_psi_score(
    psi_cpu: Optional[float],
    psi_memory: Optional[float],
    psi_io: Optional[float],
    weights: Optional[dict[str, float]] = None,
) -> Optional[float]:
    """
    Compute unified PSI score from individual resource stall signals.
    
    Args:
        psi_cpu (float): CPU PSI some_avg10
        psi_memory (float): Memory PSI some_avg10
        psi_io (float): I/O PSI some_avg10
        weights (dict): PSI sub-weights (psi_cpu_weight, psi_memory_weight, psi_io_weight)
    
    Returns:
        float: Unified PSI score in [0, 1], or None if no PSI data available
    """
    active = weights or _metric_weights
    
    # If all PSI readings are None, return None
    if psi_cpu is None and psi_memory is None and psi_io is None:
        return None
    
    # Use 0 for missing readings
    cpu_norm = normalize_psi_avg10(psi_cpu or 0.0)
    mem_norm = normalize_psi_avg10(psi_memory or 0.0)
    io_norm = normalize_psi_avg10(psi_io or 0.0)
    
    cpu_w = active.get("psi_cpu_weight", DEFAULT_METRIC_WEIGHTS["psi_cpu_weight"])
    mem_w = active.get("psi_memory_weight", DEFAULT_METRIC_WEIGHTS["psi_memory_weight"])
    io_w = active.get("psi_io_weight", DEFAULT_METRIC_WEIGHTS["psi_io_weight"])
    
    # Weighted average of normalized PSI readings
    psi_score = cpu_w * cpu_norm + mem_w * mem_norm + io_w * io_norm
    return round(min(1.0, psi_score), 2)


def compute_stress(
    cpu: float,
    memory: float,
    io: float,
    psi: Optional[float] = None,
    weights: Optional[dict[str, float]] = None,
) -> float:
    """
    Compute stress score from utilization and optionally PSI metrics.
    
    Args:
        cpu (float): CPU usage percentage [0, 100]
        memory (float): Memory usage percentage [0, 100]
        io (float): I/O wait percentage [0, 100]
        psi (float): PSI score [0, 1], optional
        weights (dict): Metric weights
    
    Returns:
        float: Normalized stress score [0, 1]
    """
    active = weights or _metric_weights
    cpu_w = active.get("cpu_weight", DEFAULT_METRIC_WEIGHTS["cpu_weight"])
    mem_w = active.get("memory_weight", DEFAULT_METRIC_WEIGHTS["memory_weight"])
    io_w = active.get("io_weight", DEFAULT_METRIC_WEIGHTS["io_weight"])
    psi_w = active.get("psi_weight", DEFAULT_METRIC_WEIGHTS["psi_weight"])
    
    # Base stress from utilization metrics
    util_stress = (cpu_w * cpu + mem_w * memory + io_w * io) / 100.0
    
    # If PSI is enabled and available, blend it in
    if psi_w > 0.0 and psi is not None:
        # Rebalance weights: util and psi weights should sum appropriately
        util_base_weight = 1.0 - psi_w
        if util_base_weight > 0:
            blended_stress = util_base_weight * util_stress + psi_w * psi
        else:
            blended_stress = psi
        return round(min(1.0, blended_stress), 2)
    
    return round(min(1.0, util_stress), 2)


class SystemMetricsSampler:
    """Delta-based system metrics from /proc/stat and /proc/meminfo."""

    def __init__(
        self,
        proc_root: str = PROC_ROOT,
        interval: float = 0.5,
        metric_weights: Optional[dict[str, float]] = None,
    ):
        self.proc_root = proc_root
        self.interval = interval
        self.metric_weights = metric_weights
        self._previous: Optional[CpuStatSnapshot] = None

    def warmup(self) -> SystemMetrics:
        self._previous = read_system_stat(self.proc_root)
        time.sleep(self.interval)
        return self.sample()

    def sample(self) -> SystemMetrics:
        current = read_system_stat(self.proc_root)
        memory = read_memory_usage_percent(self.proc_root)
        psi_fields = _read_psi_fields(self.proc_root)
        psi_score = compute_psi_score(
            psi_fields.get("psi_cpu_some_avg10"),
            psi_fields.get("psi_memory_some_avg10"),
            psi_fields.get("psi_io_some_avg10"),
            self.metric_weights,
        )

        if self._previous is None:
            self._previous = current
            return SystemMetrics(
                cpu_percent=0.0,
                memory_percent=memory,
                io_wait_percent=0.0,
                stress_score=compute_stress(0.0, memory, 0.0, psi_score, self.metric_weights),
                psi_score=psi_score,
                **psi_fields,
            )

        cpu = cpu_usage_percent(self._previous, current)
        io = io_wait_percent(self._previous, current)
        self._previous = current

        return SystemMetrics(
            cpu_percent=cpu,
            memory_percent=memory,
            io_wait_percent=io,
            stress_score=compute_stress(cpu, memory, io, psi_score, self.metric_weights),
            psi_score=psi_score,
            **psi_fields,
        )

    def sample_blocking(self) -> SystemMetrics:
        if self._previous is None:
            return self.warmup()

        time.sleep(self.interval)
        return self.sample()


def _read_psi_fields(proc_root: str) -> dict[str, Optional[float]]:
    cpu = read_psi("cpu", proc_root)
    memory = read_psi("memory", proc_root)
    io = read_psi("io", proc_root)

    return {
        "psi_cpu_some_avg10": cpu.some_avg10 if cpu else None,
        "psi_memory_some_avg10": memory.some_avg10 if memory else None,
        "psi_io_some_avg10": io.some_avg10 if io else None,
    }


_default_sampler = SystemMetricsSampler()


def calculate_cpu() -> float:
    return _default_sampler.sample_blocking().cpu_percent


def get_memory_usage() -> float:
    return read_memory_usage_percent()


def get_io_wait() -> float:
    return _default_sampler.sample_blocking().io_wait_percent


def sample_system_metrics(blocking: bool = True) -> SystemMetrics:
    if blocking:
        return _default_sampler.sample_blocking()
    return _default_sampler.sample()

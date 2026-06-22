import os
import tempfile
import unittest
from pathlib import Path

from core.classifier import classify_stress, decision_hint, trend_label, trend_rising
from core.metrics import (
    SystemMetricsSampler,
    compute_stress,
    compute_psi_score,
    normalize_psi_avg10,
)
from core.process import ProcessSampler
from core.procfs import (
    cpu_usage_percent,
    io_wait_percent,
    parse_process_stat,
    read_memory_usage_percent,
    read_psi,
    read_system_stat,
    CpuStatSnapshot,
)


FIXTURES = Path(__file__).resolve().parent / "fixtures" / "proc"


class ProcfsParsingTests(unittest.TestCase):
    def setUp(self):
        self.proc_root = str(FIXTURES)

    def test_cpu_usage_percent_from_delta(self):
        previous = read_system_stat(self.proc_root)
        current = CpuStatSnapshot(idle=previous.idle + 50, iowait=previous.iowait + 10, total=previous.total + 200)
        self.assertEqual(cpu_usage_percent(previous, current), 75.0)

    def test_io_wait_percent_from_delta(self):
        previous = read_system_stat(self.proc_root)
        current = CpuStatSnapshot(idle=previous.idle + 150, iowait=previous.iowait + 50, total=previous.total + 200)
        self.assertEqual(io_wait_percent(previous, current), 25.0)

    def test_memory_usage_percent(self):
        self.assertEqual(read_memory_usage_percent(self.proc_root), 62.5)

    def test_parse_process_stat(self):
        raw = "1234 (chrome) S 1 1 1 0 -1 4194560 100 0 0 0 500 250 0 0 0 17 0 0 0 0 0 0 0 0 0"
        comm, utime, stime = parse_process_stat(raw)
        self.assertEqual(comm, "chrome")
        self.assertEqual(utime, 500)
        self.assertEqual(stime, 250)

    def test_read_psi(self):
        psi = read_psi("cpu", self.proc_root)
        self.assertIsNotNone(psi)
        assert psi is not None
        self.assertEqual(psi.some_avg10, 12.34)
        self.assertEqual(psi.full_avg10, 4.56)


class PsiNormalizationTests(unittest.TestCase):
    def test_normalize_psi_avg10_within_range(self):
        # Normal PSI reading: 25% -> 0.25
        self.assertEqual(normalize_psi_avg10(25.0), 0.25)
    
    def test_normalize_psi_avg10_zero(self):
        # No stalls
        self.assertEqual(normalize_psi_avg10(0.0), 0.0)
    
    def test_normalize_psi_avg10_at_100(self):
        # Maximum normal: 100% -> 1.0
        self.assertEqual(normalize_psi_avg10(100.0), 1.0)
    
    def test_normalize_psi_avg10_exceeds_100(self):
        # Extreme contention: 150% -> clamped to 1.0
        self.assertEqual(normalize_psi_avg10(150.0), 1.0)
    
    def test_normalize_psi_avg10_negative(self):
        # Invalid negative: clamped to 0.0
        self.assertEqual(normalize_psi_avg10(-10.0), 0.0)


class PsiScoringTests(unittest.TestCase):
    def test_compute_psi_score_all_none(self):
        # No PSI data available
        score = compute_psi_score(None, None, None)
        self.assertIsNone(score)
    
    def test_compute_psi_score_balanced(self):
        # Equal stress across resources
        weights = {
            "psi_cpu_weight": 1/3,
            "psi_memory_weight": 1/3,
            "psi_io_weight": 1/3,
        }
        score = compute_psi_score(30.0, 30.0, 30.0, weights)
        # (0.3 * 1/3 + 0.3 * 1/3 + 0.3 * 1/3) = 0.3
        self.assertAlmostEqual(score, 0.3, places=2)
    
    def test_compute_psi_score_memory_heavy(self):
        # Memory dominates
        weights = {
            "psi_cpu_weight": 0.2,
            "psi_memory_weight": 0.6,
            "psi_io_weight": 0.2,
        }
        score = compute_psi_score(10.0, 50.0, 10.0, weights)
        # (0.1 * 0.2 + 0.5 * 0.6 + 0.1 * 0.2) = 0.32
        expected = 0.1 * 0.2 + 0.5 * 0.6 + 0.1 * 0.2
        self.assertAlmostEqual(score, expected, places=2)
    
    def test_compute_psi_score_partial_none(self):
        # Some PSI readings missing
        score = compute_psi_score(50.0, None, 20.0)
        self.assertIsNotNone(score)
        self.assertGreater(score, 0.0)
    
    def test_compute_psi_score_extreme_clamped(self):
        # Extreme values clamped to [0, 1]
        score = compute_psi_score(200.0, 200.0, 200.0)
        self.assertEqual(score, 1.0)


class StressComputationTests(unittest.TestCase):
    def test_compute_stress_utilization_only(self):
        # No PSI: utilization-based stress
        stress = compute_stress(cpu=50.0, memory=30.0, io=20.0)
        expected = (0.5 * 50 + 0.3 * 30 + 0.2 * 20) / 100
        self.assertAlmostEqual(stress, expected, places=2)
    
    def test_compute_stress_with_psi_disabled(self):
        # PSI present but weight=0: ignored
        weights = {
            "cpu_weight": 0.5,
            "memory_weight": 0.3,
            "io_weight": 0.2,
            "psi_weight": 0.0,
        }
        stress = compute_stress(cpu=50.0, memory=30.0, io=20.0, psi=0.8, weights=weights)
        expected = (0.5 * 50 + 0.3 * 30 + 0.2 * 20) / 100
        self.assertAlmostEqual(stress, expected, places=2)
    
    def test_compute_stress_with_psi_enabled(self):
        # PSI enabled with weight
        weights = {
            "cpu_weight": 0.4,
            "memory_weight": 0.2,
            "io_weight": 0.1,
            "psi_weight": 0.3,
        }
        util_stress = (0.4 * 50 + 0.2 * 30 + 0.1 * 20) / 100  # 0.26
        psi_stress = 0.8
        expected = 0.7 * util_stress + 0.3 * psi_stress
        stress = compute_stress(cpu=50.0, memory=30.0, io=20.0, psi=psi_stress, weights=weights)
        self.assertAlmostEqual(stress, expected, places=2)
    
    def test_compute_stress_psi_dominant(self):
        # PSI heavily weighted
        weights = {
            "cpu_weight": 0.05,
            "memory_weight": 0.05,
            "io_weight": 0.05,
            "psi_weight": 0.85,
        }
        stress = compute_stress(cpu=5.0, memory=5.0, io=5.0, psi=0.9, weights=weights)
        expected = 0.15 * (0.05 * 5 + 0.05 * 5 + 0.05 * 5) / 100 + 0.85 * 0.9
        self.assertAlmostEqual(stress, expected, places=2)


class MetricsSamplerTests(unittest.TestCase):
    def test_sampler_uses_stateful_delta(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            proc_root = Path(temp_dir)
            (proc_root / "meminfo").write_text(
                "MemTotal:       8000000 kB\nMemAvailable:   3000000 kB\n",
                encoding="utf-8",
            )
            (proc_root / "stat").write_text(
                "cpu  1000 0 500 5000 100 0 0 0 0 0\n",
                encoding="utf-8",
            )

            sampler = SystemMetricsSampler(proc_root=str(proc_root), interval=0)
            first = sampler.sample()
            self.assertEqual(first.cpu_percent, 0.0)

            (proc_root / "stat").write_text(
                "cpu  1100 0 550 5150 150 0 0 0 0 0\n",
                encoding="utf-8",
            )

            second = sampler.sample()
            self.assertGreater(second.cpu_percent, 0.0)
            self.assertGreater(second.io_wait_percent, 0.0)
            self.assertEqual(
                second.stress_score,
                compute_stress(second.cpu_percent, second.memory_percent, second.io_wait_percent, second.psi_score),
            )
    
    def test_sampler_includes_psi_fields(self):
        """PSI fields are always captured in SystemMetrics."""
        with tempfile.TemporaryDirectory() as temp_dir:
            proc_root = Path(temp_dir)
            (proc_root / "meminfo").write_text(
                "MemTotal:       8000000 kB\nMemAvailable:   3000000 kB\n",
                encoding="utf-8",
            )
            (proc_root / "stat").write_text(
                "cpu  1000 0 500 5000 100 0 0 0 0 0\n",
                encoding="utf-8",
            )
            # PSI not present, should be None
            
            sampler = SystemMetricsSampler(proc_root=str(proc_root), interval=0)
            metrics = sampler.sample()
            
            self.assertIsNone(metrics.psi_cpu_some_avg10)
            self.assertIsNone(metrics.psi_memory_some_avg10)
            self.assertIsNone(metrics.psi_io_some_avg10)
            self.assertIsNone(metrics.psi_score)


class ProcessSamplerTests(unittest.TestCase):
    def test_process_cpu_from_stat_delta(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            proc_root = Path(temp_dir)
            (proc_root / "meminfo").write_text(
                "MemTotal:       8000000 kB\nMemAvailable:   3000000 kB\n",
                encoding="utf-8",
            )
            (proc_root / "stat").write_text(
                "cpu  1000 0 500 5000 100 0 0 0 0 0\n",
                encoding="utf-8",
            )

            pid_dir = proc_root / "4242"
            pid_dir.mkdir()
            (pid_dir / "stat").write_text(
                "4242 (worker) R 1 1 1 0 -1 4194560 0 0 0 0 100 50 0 0 0 0 0 0",
                encoding="utf-8",
            )
            (pid_dir / "status").write_text("VmRSS:\t2000000 kB\n", encoding="utf-8")

            sampler = ProcessSampler(proc_root=str(proc_root))
            sampler.prime()

            (pid_dir / "stat").write_text(
                "4242 (worker) R 1 1 1 0 -1 4194560 0 0 0 0 300 150 0 0 0 0 0 0",
                encoding="utf-8",
            )
            (proc_root / "stat").write_text(
                "cpu  1100 0 550 5500 110 0 0 0 0 0\n",
                encoding="utf-8",
            )

            processes = sampler.sample(system_total_delta=600, total_memory_kb=8000000)
            self.assertEqual(len(processes), 1)
            self.assertEqual(processes[0].comm, "worker")
            self.assertEqual(processes[0].cpu_percent, 50.0)
            self.assertEqual(processes[0].memory_percent, 25.0)


class ClassifierTests(unittest.TestCase):
    def test_classify_stress_by_mode(self):
        self.assertEqual(classify_stress(0.10), "LOW")
        self.assertEqual(classify_stress(0.30, "Gaming"), "MODERATE")
        self.assertEqual(classify_stress(0.42, "Gaming"), "HIGH")
        self.assertEqual(classify_stress(0.70, "Balanced"), "CRITICAL")

    def test_trend_rising(self):
        self.assertFalse(trend_rising([0.5, 0.48, 0.52, 0.45, 0.44]))
        self.assertTrue(trend_rising([0.5, 0.48, 0.52, 0.7, 0.8]))

    def test_trend_label_and_decision(self):
        history = __import__("collections").deque([0.1, 0.2, 0.3, 0.4, 0.5])
        self.assertEqual(trend_label(history), "Rising")
        self.assertEqual(decision_hint("HIGH", "Rising"), "Mitigation advised")


if __name__ == "__main__":
    unittest.main()

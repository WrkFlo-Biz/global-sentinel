"""Tests for the QPanda QAE digital option pricer."""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from pathlib import Path

from src.research.backends import qpanda_qae_pricer as qqp


REPO_ROOT = Path(__file__).resolve().parents[2]


SAMPLE_REQUEST = {
    "underlying_price": 100.0,
    "strike": 105.0,
    "time_to_expiry_years": 0.25,
    "risk_free_rate": 0.04,
    "volatility": 0.28,
    "option_type": "call",
    "payout": 1.0,
    "config": {"epsilon": 0.02, "confidence_level": 0.95},
}


class _FakeCPUQVM:
    def init_qvm(self):
        return None

    def qAlloc_many(self, count):
        return list(range(count))

    def finalize(self):
        return None


class _FakeQCircuit:
    def __init__(self):
        self.ops = []

    def __lshift__(self, op):
        self.ops.append(op)
        return self


def _fake_ry(qubit, theta):
    return ("RY", qubit, theta)


def _fake_qae(circuit, qvec, epsilon, confidence_level):
    assert len(qvec) == 1
    assert epsilon == 0.02
    assert confidence_level == 0.95
    return 0.42


def _assert_common_schema(result: dict) -> None:
    assert {
        "backend",
        "algorithm",
        "status",
        "quantum_itm_probability",
        "classical_itm_probability",
        "quantum_price",
        "classical_price",
        "price_delta",
        "execution_metadata",
    }.issubset(result)
    assert result["backend"] == "qpanda_qae"
    assert result["algorithm"] == "iterative_amplitude_estimation"
    assert result["execution_metadata"]["not_for_direct_execution"] is True
    assert result["execution_metadata"]["quantum_direct_execution_forbidden"] is True
    assert result["execution_metadata"]["bounded_secondary_signal_only"] is True
    assert result["execution_metadata"]["backend"] == "qpanda_qae"


def test_qpanda_qae_pricer_success_with_fake_sdk(monkeypatch):
    monkeypatch.setattr(
        qqp,
        "_load_qae_sdk",
        lambda: {
            "package_name": "pyqpanda3",
            "CPUQVM": _FakeCPUQVM,
            "QCircuit": _FakeQCircuit,
            "RY": _fake_ry,
            "iterative_amplitude_estimation": _fake_qae,
        },
    )

    result = qqp.QPandaQAEOptionPricer().price(SAMPLE_REQUEST)

    _assert_common_schema(result)
    assert result["status"] == "success"
    assert result["quantum_itm_probability"] == 0.42
    assert result["execution_metadata"]["qae_available"] is True
    assert result["execution_metadata"]["sdk_package"] == "pyqpanda3"


def test_qpanda_qae_pricer_gracefully_degrades_when_sdk_missing(monkeypatch):
    monkeypatch.setattr(qqp, "_load_qae_sdk", lambda: (_ for _ in ()).throw(ImportError("missing")))

    result = qqp.QPandaQAEOptionPricer().price(SAMPLE_REQUEST)

    _assert_common_schema(result)
    assert result["status"] == "error"
    assert result["execution_metadata"]["qae_available"] is False
    assert "missing" in result["error"]


def test_qpanda_qae_module_main_gracefully_degrades_when_dependency_missing():
    code = textwrap.dedent(
        """
        import builtins
        import runpy

        real_import = builtins.__import__
        blocked = ("pyqpanda", "pyqpanda3")

        def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
            if any(name == prefix or name.startswith(prefix + ".") for prefix in blocked):
                raise ImportError("forced missing dependency")
            return real_import(name, globals, locals, fromlist, level)

        builtins.__import__ = fake_import
        runpy.run_module("src.research.backends.qpanda_qae_pricer", run_name="__main__")
        """
    )

    proc = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        check=False,
    )

    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    _assert_common_schema(payload)
    assert payload["status"] == "error"
    assert payload["execution_metadata"]["qae_available"] is False

#!/usr/bin/env python3
"""QPanda QAE digital option pricer.

Research-only backend that uses Origin's iterative amplitude estimation
primitive to estimate a digital option's exercise probability on a local
CPUQVM. The result is compared against a Black-Scholes digital-option
baseline and emitted as an artifact-only payload with the standard GS
guardrails.
"""
from __future__ import annotations

import hashlib
import json
import math
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _artifact_id(payload: Dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()[:16]


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default


def _safe_str(value: Any, default: str = "") -> str:
    if value is None:
        return default
    return str(value)


def _clamp_probability(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _norm_cdf(value: float) -> float:
    return 0.5 * (1.0 + math.erf(value / math.sqrt(2.0)))


def _load_qae_sdk() -> Dict[str, Any]:
    """Load the available Origin SDK surface for iterative amplitude estimation."""
    errors = []
    for package_name in ("pyqpanda3", "pyqpanda"):
        try:
            module = __import__(package_name)
            return {
                "package_name": package_name,
                "module": module,
                "CPUQVM": getattr(module, "CPUQVM"),
                "QCircuit": getattr(module, "QCircuit"),
                "RY": getattr(module, "RY"),
                "iterative_amplitude_estimation": getattr(
                    module, "iterative_amplitude_estimation"
                ),
            }
        except Exception as exc:  # pragma: no cover - exercised through fallback
            errors.append(f"{package_name}: {exc}")
    raise ImportError("; ".join(errors) or "No pyqpanda surface available")


class QPandaQAEOptionPricer:
    """Estimate digital option prices with iterative amplitude estimation."""

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = config or {}

    def run(self, request: Dict[str, Any]) -> Dict[str, Any]:
        return self.price(request)

    def price(self, request: Dict[str, Any]) -> Dict[str, Any]:
        start = time.monotonic()
        payload = request.get("option") if isinstance(request.get("option"), dict) else request

        option_type = _safe_str(payload.get("option_type"), "call").lower()
        if option_type not in {"call", "put"}:
            return self._error_result(
                request,
                reason=f"unsupported_option_type:{option_type}",
                start=start,
            )

        underlying_price = _safe_float(payload.get("underlying_price"), 0.0)
        strike = _safe_float(payload.get("strike"), 0.0)
        time_to_expiry_years = _safe_float(payload.get("time_to_expiry_years"), 0.0)
        risk_free_rate = _safe_float(payload.get("risk_free_rate"), 0.0)
        volatility = _safe_float(payload.get("volatility"), 0.0)
        payout = _safe_float(payload.get("payout"), 1.0)

        if underlying_price <= 0 or strike <= 0 or time_to_expiry_years <= 0 or volatility <= 0:
            return self._error_result(
                request,
                reason="invalid_option_parameters",
                start=start,
            )

        config = request.get("config") if isinstance(request.get("config"), dict) else {}
        epsilon = _safe_float(config.get("epsilon"), self.config.get("epsilon", 0.02))
        confidence_level = _safe_float(
            config.get("confidence_level"),
            self.config.get("confidence_level", 0.95),
        )

        classical_probability = self._digital_itm_probability(
            underlying_price=underlying_price,
            strike=strike,
            time_to_expiry_years=time_to_expiry_years,
            risk_free_rate=risk_free_rate,
            volatility=volatility,
            option_type=option_type,
        )
        classical_price = payout * math.exp(-risk_free_rate * time_to_expiry_years) * classical_probability

        try:
            sdk = _load_qae_sdk()
            quantum_probability = self._estimate_probability(
                sdk=sdk,
                target_probability=classical_probability,
                epsilon=epsilon,
                confidence_level=confidence_level,
            )
            status = "success"
            error = None
        except Exception as exc:
            sdk = None
            quantum_probability = None
            status = "error"
            error = str(exc)

        runtime_seconds = round(time.monotonic() - start, 4)
        quantum_price = None
        if quantum_probability is not None:
            quantum_probability = _clamp_probability(quantum_probability)
            quantum_price = payout * math.exp(-risk_free_rate * time_to_expiry_years) * quantum_probability

        artifact_seed = {
            "backend": "qpanda_qae",
            "timestamp_utc": _utc_now(),
            "option_type": option_type,
            "underlying_price": underlying_price,
            "strike": strike,
            "time_to_expiry_years": time_to_expiry_years,
            "risk_free_rate": risk_free_rate,
            "volatility": volatility,
            "quantum_probability": quantum_probability,
            "classical_probability": classical_probability,
            "status": status,
        }

        result = {
            "backend": "qpanda_qae",
            "algorithm": "iterative_amplitude_estimation",
            "status": status,
            "option_type": option_type,
            "underlying_price": underlying_price,
            "strike": strike,
            "time_to_expiry_years": time_to_expiry_years,
            "risk_free_rate": risk_free_rate,
            "volatility": volatility,
            "payout": payout,
            "quantum_itm_probability": quantum_probability,
            "classical_itm_probability": round(classical_probability, 8),
            "quantum_price": round(quantum_price, 8) if quantum_price is not None else None,
            "classical_price": round(classical_price, 8),
            "price_delta": round((quantum_price - classical_price), 8) if quantum_price is not None else None,
            "execution_metadata": {
                "not_for_direct_execution": True,
                "quantum_direct_execution_forbidden": True,
                "bounded_secondary_signal_only": True,
                "backend": "qpanda_qae",
                "algorithm": "iterative_amplitude_estimation",
                "qae_available": quantum_probability is not None,
                "sdk_package": (sdk or {}).get("package_name"),
                "epsilon": epsilon,
                "confidence_level": confidence_level,
                "classical_baseline": "black_scholes_digital",
                "artifact_only": True,
                "research_only": True,
                "runtime_seconds": runtime_seconds,
                "timestamp_utc": _utc_now(),
                "artifact_id": _artifact_id(artifact_seed),
            },
        }
        if error is not None:
            result["error"] = error
        return result

    @staticmethod
    def _digital_itm_probability(
        *,
        underlying_price: float,
        strike: float,
        time_to_expiry_years: float,
        risk_free_rate: float,
        volatility: float,
        option_type: str,
    ) -> float:
        sqrt_t = math.sqrt(time_to_expiry_years)
        d2 = (
            math.log(underlying_price / strike)
            + (risk_free_rate - 0.5 * volatility * volatility) * time_to_expiry_years
        ) / (volatility * sqrt_t)
        if option_type == "call":
            return _clamp_probability(_norm_cdf(d2))
        return _clamp_probability(_norm_cdf(-d2))

    @staticmethod
    def _estimate_probability(
        *,
        sdk: Dict[str, Any],
        target_probability: float,
        epsilon: float,
        confidence_level: float,
    ) -> float:
        theta = 2.0 * math.asin(math.sqrt(_clamp_probability(target_probability)))
        qvm = sdk["CPUQVM"]()
        qvm.init_qvm()
        q = qvm.qAlloc_many(1)
        circuit = sdk["QCircuit"]()
        circuit << sdk["RY"](q[0], theta)
        try:
            estimate = sdk["iterative_amplitude_estimation"](
                circuit, q, float(epsilon), float(confidence_level)
            )
            return float(estimate)
        finally:
            try:
                qvm.finalize()
            except Exception:
                pass

    def _error_result(self, request: Dict[str, Any], *, reason: str, start: float) -> Dict[str, Any]:
        runtime_seconds = round(time.monotonic() - start, 4)
        artifact_seed = {
            "backend": "qpanda_qae",
            "reason": reason,
            "request": request,
            "timestamp_utc": _utc_now(),
        }
        return {
            "backend": "qpanda_qae",
            "algorithm": "iterative_amplitude_estimation",
            "status": "error",
            "reason": reason,
            "quantum_itm_probability": None,
            "classical_itm_probability": None,
            "quantum_price": None,
            "classical_price": None,
            "price_delta": None,
            "execution_metadata": {
                "not_for_direct_execution": True,
                "quantum_direct_execution_forbidden": True,
                "bounded_secondary_signal_only": True,
                "backend": "qpanda_qae",
                "algorithm": "iterative_amplitude_estimation",
                "qae_available": False,
                "artifact_only": True,
                "research_only": True,
                "runtime_seconds": runtime_seconds,
                "timestamp_utc": _utc_now(),
                "artifact_id": _artifact_id(artifact_seed),
            },
        }


def _sample_request() -> Dict[str, Any]:
    return {
        "underlying_price": 100.0,
        "strike": 105.0,
        "time_to_expiry_years": 0.25,
        "risk_free_rate": 0.04,
        "volatility": 0.28,
        "option_type": "call",
        "payout": 1.0,
        "config": {
            "epsilon": 0.02,
            "confidence_level": 0.95,
        },
    }


if __name__ == "__main__":
    pricer = QPandaQAEOptionPricer()
    print(json.dumps(pricer.price(_sample_request()), indent=2))

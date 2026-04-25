from __future__ import annotations

import importlib.util
import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from urllib.error import HTTPError, URLError


REPO_ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = REPO_ROOT / "scripts" / "ops" / "broker_readiness.py"
SPEC = importlib.util.spec_from_file_location("broker_readiness_module", MODULE_PATH)
broker_readiness = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(broker_readiness)


class BrokerReadinessTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)

        self.env_patcher = mock.patch.dict(
            os.environ,
            {
                "GLOBAL_SENTINEL_REPO_ROOT": self.tmpdir.name,
                "IB_GATEWAY_HOST": "127.0.0.1",
                "IB_GATEWAY_PORT": "4001",
                "IB_CLIENT_ID": "1",
            },
            clear=False,
        )
        self.env_patcher.start()
        self.addCleanup(self.env_patcher.stop)

        self.checker = broker_readiness.BrokerReadinessChecker()

    def test_ibkr_401_means_not_authenticated(self) -> None:
        fake_result = type("Result", (), {"stdout": "active\n"})()

        def fake_urlopen(*args, **kwargs):
            raise HTTPError(
                url="https://localhost:5000/v1/api/iserver/auth/status",
                code=401,
                msg="Unauthorized",
                hdrs=None,
                fp=io.BytesIO(b""),
            )

        with mock.patch.object(broker_readiness.subprocess, "run", return_value=fake_result):
            with mock.patch.object(broker_readiness.urllib.request, "urlopen", side_effect=fake_urlopen):
                result = self.checker._check_ibkr()

        self.assertEqual(result["status"], "disconnected")
        self.assertTrue(result["service_active"])
        self.assertTrue(result["port_5000_ok"])
        self.assertFalse(result["authenticated"])
        self.assertIn("NOT_AUTHENTICATED", result["issues"])
        self.assertNotIn("PORT_5000_UNREACHABLE", result["issues"])

    def test_ibkr_network_error_means_port_unreachable(self) -> None:
        fake_result = type("Result", (), {"stdout": "active\n"})()

        with mock.patch.object(broker_readiness.subprocess, "run", return_value=fake_result):
            with mock.patch.object(
                broker_readiness.urllib.request,
                "urlopen",
                side_effect=URLError("connection refused"),
            ):
                result = self.checker._check_ibkr()

        self.assertEqual(result["status"], "disconnected")
        self.assertTrue(result["service_active"])
        self.assertFalse(result["port_5000_ok"])
        self.assertFalse(result["authenticated"])
        self.assertIn("PORT_5000_UNREACHABLE", result["issues"])
        self.assertNotIn("NOT_AUTHENTICATED", result["issues"])

    def test_ibkr_remote_gateway_uses_ib_async_health_path(self) -> None:
        fake_result = type("Result", (), {"stdout": "inactive\n"})()
        remote_settings = {"host": "100.78.207.22", "port": 4002, "client_id": 1}
        bridge_status = {
            "broker": "ibkr",
            "connected": True,
            "account": "U25016589",
            "net_liquidation": "10342.55",
            "buying_power": "8274.10",
        }

        with mock.patch.object(broker_readiness.subprocess, "run", return_value=fake_result):
            with mock.patch.object(
                broker_readiness.ibkr_bridge,
                "get_connection_settings",
                return_value=remote_settings,
            ):
                with mock.patch.object(
                    broker_readiness.ibkr_bridge,
                    "get_broker_status",
                    return_value=bridge_status,
                ):
                    with mock.patch.object(
                        broker_readiness.urllib.request,
                        "urlopen",
                        side_effect=AssertionError("client portal should not be probed for remote gateway"),
                    ):
                        result = self.checker._check_ibkr()

        self.assertEqual(result["status"], "connected")
        self.assertEqual(result["health_path"], "ib_async_tws")
        self.assertFalse(result["service_active"])
        self.assertTrue(result["authenticated"])
        self.assertEqual(result["gateway_host"], "100.78.207.22")
        self.assertEqual(result["gateway_port"], 4002)
        self.assertEqual(result["client_id"], 1)
        self.assertEqual(result["account"], "U25016589")
        self.assertEqual(result["equity"], 10342.55)
        self.assertEqual(result["buying_power"], 8274.10)
        self.assertEqual(result["issues"], [])

    def test_ibkr_remote_gateway_errors_do_not_require_local_client_portal(self) -> None:
        fake_result = type("Result", (), {"stdout": "inactive\n"})()
        remote_settings = {"host": "100.78.207.22", "port": 4002, "client_id": 1}
        bridge_status = {
            "broker": "ibkr",
            "connected": False,
            "error": "Cannot connect to IB Gateway at 100.78.207.22:4002: timeout",
        }

        with mock.patch.object(broker_readiness.subprocess, "run", return_value=fake_result):
            with mock.patch.object(
                broker_readiness.ibkr_bridge,
                "get_connection_settings",
                return_value=remote_settings,
            ):
                with mock.patch.object(
                    broker_readiness.ibkr_bridge,
                    "get_broker_status",
                    return_value=bridge_status,
                ):
                    with mock.patch.object(
                        broker_readiness.urllib.request,
                        "urlopen",
                        side_effect=AssertionError("client portal should not be probed for remote gateway"),
                    ):
                        result = self.checker._check_ibkr()

        self.assertEqual(result["status"], "disconnected")
        self.assertEqual(result["health_path"], "ib_async_tws")
        self.assertFalse(result["service_active"])
        self.assertFalse(result["authenticated"])
        self.assertEqual(result["gateway_host"], "100.78.207.22")
        self.assertEqual(result["gateway_port"], 4002)
        self.assertIn("TWS_CONNECT_FAILED", result["issues"])
        self.assertNotIn("SERVICE_DOWN", result["issues"])
        self.assertNotIn("PORT_5000_UNREACHABLE", result["issues"])


if __name__ == "__main__":
    unittest.main()

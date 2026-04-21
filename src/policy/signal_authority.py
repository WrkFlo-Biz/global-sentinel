"""Signal Authority — gate-keeps which signals can drive trade execution."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

_DEFAULT_CONFIG = "config/signal_authority.yaml"


class SignalAuthority:
    """Loads the signal-authority YAML and enforces per-signal permissions."""

    def __init__(
        self,
        config_path: str = _DEFAULT_CONFIG,
        repo_root: str | Path | None = None,
    ) -> None:
        if repo_root is None:
            repo_root = Path(__file__).resolve().parents[2]
        self._repo_root = Path(repo_root)
        self._config_path = self._repo_root / config_path

        self._authority_map: dict[str, str] = {}
        self._load()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _load(self) -> None:
        """Parse the YAML and build a signal -> level lookup."""
        logger.info("Loading signal-authority config from %s", self._config_path)
        with open(self._config_path, "r") as fh:
            raw: dict[str, Any] = yaml.safe_load(fh)

        for level, block in raw.get("authority_levels", {}).items():
            for signal in block.get("signals", []):
                self._authority_map[signal] = level

        logger.info(
            "Signal-authority loaded: %d signals across %s levels",
            len(self._authority_map),
            list(raw.get("authority_levels", {}).keys()),
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def check_authority(self, signal_name: str) -> str:
        """Return the authority level for *signal_name*.

        Returns ``"authoritative"``, ``"advisory"``, or ``"blocked"``.
        Unknown signals default to ``"blocked"``.
        """
        level = self._authority_map.get(signal_name, "blocked")
        logger.debug("check_authority(%s) -> %s", signal_name, level)
        return level

    def enforce(
        self,
        signal_name: str,
        action: str = "generate_trade_idea",
    ) -> dict[str, Any]:
        """Decide whether *signal_name* is permitted to perform *action*.

        Returns a dict::

            {
                "allowed": bool,
                "level": str,
                "reason": str,
            }
        """
        level = self.check_authority(signal_name)

        if level == "authoritative":
            allowed = True
            reason = (
                f"Signal '{signal_name}' is authoritative — "
                f"action '{action}' permitted."
            )
        elif level == "advisory":
            allowed = False
            reason = (
                f"Signal '{signal_name}' is advisory only — "
                f"action '{action}' denied. Tag on existing ideas instead."
            )
        else:  # blocked or unknown
            allowed = False
            reason = (
                f"Signal '{signal_name}' is blocked — "
                f"action '{action}' denied."
            )

        result = {"allowed": allowed, "level": level, "reason": reason}
        logger.info(
            "enforce(%s, %s) -> allowed=%s  level=%s",
            signal_name,
            action,
            allowed,
            level,
        )
        return result

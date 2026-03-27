#!/usr/bin/env python3
"""Global Sentinel V4 — Cryptographic Reasoning Packet Signer (Pack 8, Frontier R&D).

HMAC-based signing and verification for reasoning packets to prevent
post-hoc tampering of decision traces, audit logs, and research artifacts.

Each signed packet includes:
- HMAC-SHA256 signature over canonical payload
- Signer identity
- Timestamp
- Schema version
- Optional chain-of-custody linking (previous packet hash)

This module is RESEARCH-ONLY — not for direct execution.
It produces signed verification artifacts consumed by audit tools.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

DEFAULT_ALGORITHM = "hmac-sha256"


class ReasoningPacketSigner:
    """HMAC-based signer for reasoning packets and decision traces.

    Signs packets with HMAC-SHA256 using a configured secret key.
    Supports chain-of-custody by linking to previous packet hashes.
    """

    def __init__(
        self,
        secret_key: Optional[str] = None,
        signer_id: str = "global-sentinel-v4",
    ):
        self._secret = (secret_key or os.getenv("GS_PACKET_SIGNING_KEY", "")).encode("utf-8")
        self._signer_id = signer_id
        if not self._secret:
            logger.warning("No signing key configured; signatures will use empty key (insecure)")

    def sign(
        self,
        payload: Dict[str, Any],
        previous_hash: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Sign a reasoning packet.

        Args:
            payload: The packet data to sign (must be JSON-serializable).
            previous_hash: Optional hash of the previous packet for chain linking.

        Returns:
            Signed packet with _signature envelope.
        """
        canonical = self._canonicalize(payload)
        content_hash = hashlib.sha256(canonical).hexdigest()

        signing_input = canonical
        if previous_hash:
            signing_input = previous_hash.encode("utf-8") + b"|" + signing_input

        signature = hmac.new(self._secret, signing_input, hashlib.sha256).hexdigest()

        return {
            **payload,
            "_signature": {
                "algorithm": DEFAULT_ALGORITHM,
                "signature": signature,
                "content_hash": content_hash,
                "previous_hash": previous_hash,
                "signer_id": self._signer_id,
                "signed_at": datetime.now(timezone.utc).isoformat(),
                "schema_version": "packet_signature.v1",
            },
            "not_for_direct_execution": True,
            "research_only": True,
        }

    def verify(
        self,
        signed_packet: Dict[str, Any],
        previous_hash: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Verify a signed reasoning packet.

        Args:
            signed_packet: Packet with _signature envelope.
            previous_hash: Expected previous hash for chain verification.

        Returns:
            Verification result with valid (bool) and details.
        """
        sig_envelope = signed_packet.get("_signature")
        if not sig_envelope:
            return {"valid": False, "reason": "no_signature_envelope"}

        stored_sig = sig_envelope.get("signature", "")
        stored_content_hash = sig_envelope.get("content_hash", "")
        stored_prev_hash = sig_envelope.get("previous_hash")

        # Reconstruct payload without signature
        payload = {k: v for k, v in signed_packet.items()
                   if k not in ("_signature", "not_for_direct_execution", "research_only")}

        canonical = self._canonicalize(payload)
        computed_content_hash = hashlib.sha256(canonical).hexdigest()

        # Verify content hash
        if computed_content_hash != stored_content_hash:
            return {
                "valid": False,
                "reason": "content_hash_mismatch",
                "expected": computed_content_hash,
                "stored": stored_content_hash,
            }

        # Verify HMAC signature
        signing_input = canonical
        effective_prev = previous_hash or stored_prev_hash
        if effective_prev:
            signing_input = effective_prev.encode("utf-8") + b"|" + signing_input

        computed_sig = hmac.new(self._secret, signing_input, hashlib.sha256).hexdigest()

        if not hmac.compare_digest(computed_sig, stored_sig):
            return {"valid": False, "reason": "signature_mismatch"}

        # Verify chain continuity if previous_hash provided
        if previous_hash and stored_prev_hash and previous_hash != stored_prev_hash:
            return {
                "valid": False,
                "reason": "chain_continuity_broken",
                "expected_prev": previous_hash,
                "stored_prev": stored_prev_hash,
            }

        return {
            "valid": True,
            "reason": "signature_verified",
            "signer_id": sig_envelope.get("signer_id"),
            "signed_at": sig_envelope.get("signed_at"),
            "content_hash": stored_content_hash,
        }

    def sign_chain(self, packets: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Sign a chain of packets with linked hashes.

        Each packet's signature references the content hash of the previous.
        """
        signed_chain: List[Dict[str, Any]] = []
        prev_hash: Optional[str] = None

        for packet in packets:
            signed = self.sign(packet, previous_hash=prev_hash)
            prev_hash = signed["_signature"]["content_hash"]
            signed_chain.append(signed)

        return signed_chain

    def verify_chain(self, signed_packets: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Verify an entire chain of signed packets."""
        results: List[Dict[str, Any]] = []
        prev_hash: Optional[str] = None
        all_valid = True

        for i, packet in enumerate(signed_packets):
            result = self.verify(packet, previous_hash=prev_hash)
            result["chain_index"] = i
            results.append(result)
            if not result["valid"]:
                all_valid = False
            prev_hash = packet.get("_signature", {}).get("content_hash")

        return {
            "schema_version": "chain_verification.v1",
            "chain_length": len(signed_packets),
            "all_valid": all_valid,
            "results": results,
            "not_for_direct_execution": True,
            "research_only": True,
        }

    @staticmethod
    def _canonicalize(payload: Dict[str, Any]) -> bytes:
        """Canonical JSON serialization for deterministic hashing."""
        return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")

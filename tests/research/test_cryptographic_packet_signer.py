"""Tests for ReasoningPacketSigner."""
from __future__ import annotations

import pytest
from src.research.cryptographic_reasoning_packet_signer import ReasoningPacketSigner


@pytest.fixture
def signer():
    return ReasoningPacketSigner(secret_key="test-key-2026", signer_id="test-signer")


def _sample_packet(**overrides):
    base = {
        "decision": "hedge_spy",
        "confidence": 0.85,
        "regime": "elevated",
        "sources": ["fed", "ecb"],
    }
    base.update(overrides)
    return base


def test_sign_produces_signature(signer):
    signed = signer.sign(_sample_packet())
    assert "_signature" in signed
    sig = signed["_signature"]
    assert sig["algorithm"] == "hmac-sha256"
    assert sig["signature"]
    assert sig["content_hash"]
    assert sig["signer_id"] == "test-signer"
    assert sig["schema_version"] == "packet_signature.v1"
    assert signed["not_for_direct_execution"] is True
    assert signed["research_only"] is True


def test_verify_valid_signature(signer):
    signed = signer.sign(_sample_packet())
    result = signer.verify(signed)
    assert result["valid"] is True
    assert result["reason"] == "signature_verified"


def test_verify_tampered_payload(signer):
    signed = signer.sign(_sample_packet())
    signed["confidence"] = 0.99  # tamper
    result = signer.verify(signed)
    assert result["valid"] is False
    assert result["reason"] == "content_hash_mismatch"


def test_verify_wrong_key():
    signer1 = ReasoningPacketSigner(secret_key="key-A")
    signer2 = ReasoningPacketSigner(secret_key="key-B")
    signed = signer1.sign(_sample_packet())
    result = signer2.verify(signed)
    assert result["valid"] is False
    assert result["reason"] == "signature_mismatch"


def test_verify_no_signature_envelope(signer):
    result = signer.verify({"data": 123})
    assert result["valid"] is False
    assert result["reason"] == "no_signature_envelope"


def test_sign_with_chain_link(signer):
    prev_hash = "abc123def456"
    signed = signer.sign(_sample_packet(), previous_hash=prev_hash)
    assert signed["_signature"]["previous_hash"] == prev_hash


def test_sign_chain(signer):
    packets = [
        _sample_packet(decision="step_1"),
        _sample_packet(decision="step_2"),
        _sample_packet(decision="step_3"),
    ]
    chain = signer.sign_chain(packets)
    assert len(chain) == 3
    assert chain[0]["_signature"]["previous_hash"] is None
    assert chain[1]["_signature"]["previous_hash"] == chain[0]["_signature"]["content_hash"]
    assert chain[2]["_signature"]["previous_hash"] == chain[1]["_signature"]["content_hash"]


def test_verify_chain_valid(signer):
    packets = [_sample_packet(i=i) for i in range(4)]
    chain = signer.sign_chain(packets)
    result = signer.verify_chain(chain)
    assert result["all_valid"] is True
    assert result["chain_length"] == 4


def test_verify_chain_tampered(signer):
    packets = [_sample_packet(i=i) for i in range(3)]
    chain = signer.sign_chain(packets)
    chain[1]["decision"] = "tampered"  # tamper middle packet
    result = signer.verify_chain(chain)
    assert result["all_valid"] is False


def test_different_payloads_different_signatures(signer):
    s1 = signer.sign({"a": 1})
    s2 = signer.sign({"a": 2})
    assert s1["_signature"]["signature"] != s2["_signature"]["signature"]


def test_deterministic_signature(signer):
    packet = _sample_packet()
    s1 = signer.sign(packet)
    s2 = signer.sign(packet)
    assert s1["_signature"]["signature"] == s2["_signature"]["signature"]
    assert s1["_signature"]["content_hash"] == s2["_signature"]["content_hash"]


def test_empty_key_warning():
    signer = ReasoningPacketSigner(secret_key="")
    signed = signer.sign(_sample_packet())
    # Should still produce a signature (just insecure)
    assert signed["_signature"]["signature"]


def test_payload_preserved(signer):
    packet = _sample_packet(extra_field="preserved")
    signed = signer.sign(packet)
    assert signed["extra_field"] == "preserved"
    assert signed["decision"] == "hedge_spy"

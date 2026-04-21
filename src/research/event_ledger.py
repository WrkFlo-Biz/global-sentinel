from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple


LEDGER_VERSION = "event_ledger.v1"

_EVENT_TYPE_CANDIDATES = (
    "event_type",
    "event_category",
    "topic",
    "category",
)
_SUBJECT_TEXT_CANDIDATES = (
    "headline",
    "title",
    "summary",
    "topic",
    "event_category",
    "category",
    "page_title",
    "page_name",
    "series_name",
    "symbol",
    "region",
)
_SUBJECT_KEY_CANDIDATES = (
    "series_id",
    "series_key",
    "symbol",
    "page_name",
    "page_title",
    "event_category",
    "category",
    "region",
    "packet_id",
    "event_id",
)
_SOURCE_DOMAIN_CANDIDATES = ("source_domain", "source")
_SOURCE_URL_CANDIDATES = (
    "source_url",
    "source_feed_url",
    "provenance.raw_source_url",
    "provenance.data_url",
)
_SOURCE_TIER_CANDIDATES = ("source_tier",)
_SOURCE_TYPE_CANDIDATES = ("source_type",)
_SOURCE_CREDIBILITY_CANDIDATES = (
    "source_credibility",
    "source_confidence",
    "confidence",
    "trust_weight",
)
_ASSET_TAG_CANDIDATES = (
    "asset_tags",
    "related_assets",
    "asset_channels",
)
_GEOGRAPHY_TAG_CANDIDATES = (
    "geography_tags",
    "region",
    "country",
    "country_name",
)
_TIME_HORIZON_CANDIDATES = (
    "time_horizon",
    "holding_period",
)
_EVENT_TIME_CANDIDATES = (
    "published_time_utc",
    "published_date",
    "published",
    "latest_observation_date",
    "latest_row.date",
    "latest_row.period",
    "parsing_meta.row_date",
    "parsing_meta.period",
    "parsing_meta.latest_observation_date",
    "parsing_meta.latest_row.date",
    "parsing_meta.latest_row.period",
    "timestamp_utc",
)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _get_nested(packet: Mapping[str, Any], path: str) -> Any:
    cur: Any = packet
    for part in path.split("."):
        if not isinstance(cur, Mapping):
            return None
        cur = cur.get(part)
        if cur is None:
            return None
    return cur


def _first_value_with_path(
    packet: Mapping[str, Any],
    candidates: Sequence[str],
) -> Tuple[Any, Optional[str]]:
    for path in candidates:
        value = _get_nested(packet, path)
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        if isinstance(value, (list, tuple, set, dict)) and len(value) == 0:  # type: ignore[arg-type]
            continue
        return value, path
    return None, None


def _stringify(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (list, tuple, set)):
        parts = [_stringify(v) for v in value]
        return " | ".join([p for p in parts if p])
    if isinstance(value, dict):
        try:
            return json.dumps(value, sort_keys=True, ensure_ascii=False)
        except Exception:
            return str(value)
    return str(value).strip()


def normalize_event_text(value: Any) -> str:
    text = _stringify(value).lower().strip()
    out: List[str] = []
    for ch in text:
        if ch.isalnum():
            out.append(ch)
        elif ch.isspace():
            out.append(" ")
        else:
            out.append(" ")
    return " ".join("".join(out).split())


def _schema_family(packet: Mapping[str, Any]) -> str:
    schema_version = _stringify(packet.get("schema_version"))
    if not schema_version:
        return ""
    family = schema_version.split(".", 1)[0]
    return family.strip()


def _source_family(source_tier: str, source_type: str, official_source: bool) -> str:
    tier = source_tier.lower()
    stype = source_type.lower()

    if official_source or "official" in tier:
        return "official"
    if "research" in tier or "tier_3" in tier:
        return "research"
    if "institutional" in tier or "tier_2" in tier or "tier_b" in tier:
        return "institutional"
    if stype in {"search_result", "headline_item", "page", "page_link"} or "osint" in tier or "alt" in tier:
        return "alt"
    return "unknown"


def _canonical_event_type(packet: Mapping[str, Any], event_type_hint: Optional[str]) -> Tuple[str, Optional[str]]:
    value, path = _first_value_with_path(packet, _EVENT_TYPE_CANDIDATES)
    if value is not None:
        return _stringify(value), path
    if event_type_hint:
        return _stringify(event_type_hint), "__hint__"
    if packet.get("error") is not None:
        return "bridge_error", "error"
    schema_family = _schema_family(packet)
    if schema_family:
        return schema_family, "schema_version"
    return "unknown", None


def _canonical_subject(
    packet: Mapping[str, Any],
) -> Tuple[str, str, Optional[str]]:
    text_value, text_path = _first_value_with_path(packet, _SUBJECT_TEXT_CANDIDATES)
    key_value, key_path = _first_value_with_path(packet, _SUBJECT_KEY_CANDIDATES)

    # Prefer semantic subject keys over raw source IDs whenever text context exists.
    if key_path in {"packet_id", "event_id"} and text_value is not None:
        key_value = text_value
        key_path = text_path

    if text_value is None:
        text_value = key_value
        text_path = key_path

    if text_value is None:
        text_value = packet.get("event_id") or packet.get("packet_id") or packet.get("source_url") or packet.get("source")
        text_path = "event_id" if packet.get("event_id") else ("packet_id" if packet.get("packet_id") else ("source_url" if packet.get("source_url") else "source"))

    subject_text = _stringify(text_value)
    if len(subject_text) > 180:
        subject_text = subject_text[:177] + "..."

    subject_key = _stringify(key_value) if key_value is not None else subject_text
    return subject_text, normalize_event_text(subject_key), key_path or text_path


def _normalize_tag_list(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        items = value
    else:
        items = [value]
    out: List[str] = []
    for item in items:
        text = _stringify(item)
        if not text:
            continue
        out.append(text)
    # Preserve first-seen order while dropping duplicates
    seen = set()
    deduped: List[str] = []
    for item in out:
        key = normalize_event_text(item)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def _infer_asset_tags(packet: Mapping[str, Any], subject_text: str) -> Tuple[List[str], Optional[str]]:
    value, path = _first_value_with_path(packet, _ASSET_TAG_CANDIDATES)
    tags = _normalize_tag_list(value)
    if not tags and subject_text:
        norm = normalize_event_text(subject_text)
        if any(token in norm for token in ("oil", "energy", "crude", "gasoline", "petroleum")):
            tags.append("energy")
        if any(token in norm for token in ("fed", "rates", "yield", "bond", "treasury", "cpi", "inflation")):
            tags.append("rates")
        if any(token in norm for token in ("airline", "aviation", "travel", "airport")):
            tags.append("transport")
    return tags, path


def _infer_geography_tags(packet: Mapping[str, Any], subject_text: str) -> Tuple[List[str], Optional[str]]:
    value, path = _first_value_with_path(packet, _GEOGRAPHY_TAG_CANDIDATES)
    tags = _normalize_tag_list(value)
    if not tags and subject_text:
        norm = normalize_event_text(subject_text)
        for candidate in ("global", "us", "usa", "europe", "asia", "china", "japan", "iran", "russia", "ukraine", "middle east"):
            if candidate in norm:
                tags.append(candidate.replace(" ", "_"))
    return tags, path


def _infer_time_horizon(
    packet: Mapping[str, Any],
    family: str,
    source_type: str,
    event_type: str,
) -> Tuple[str, Optional[str]]:
    value, path = _first_value_with_path(packet, _TIME_HORIZON_CANDIDATES)
    if value is not None:
        return normalize_event_text(value), path
    if _get_nested(packet, "release_window"):
        return "event_window", "release_window"
    if family in {"macro_policy_event", "macro_policy_event_error"}:
        return "event_window", "schema_version"
    if family in {"geopolitical_event", "physical_flow_event"}:
        return "short_term", "packet_type"
    if source_type in {"api", "rss", "page_link", "page", "search_result", "csv"}:
        return "intraday", "source_type"
    if event_type in {"bridge_error"}:
        return "operational", "event_type"
    return "short_term", None


def _infer_source_credibility(packet: Mapping[str, Any], source_family: str) -> Tuple[float, Optional[str]]:
    value, path = _first_value_with_path(packet, _SOURCE_CREDIBILITY_CANDIDATES)
    if value is not None:
        try:
            return max(0.0, min(1.0, float(value))), path
        except Exception:
            pass

    default_by_family = {
        "official": 0.92,
        "institutional": 0.74,
        "research": 0.58,
        "alt": 0.46,
        "unknown": 0.50,
    }
    return default_by_family.get(source_family, 0.50), None


def _event_time_hint(packet: Mapping[str, Any]) -> Tuple[str, Optional[str]]:
    value, path = _first_value_with_path(packet, _EVENT_TIME_CANDIDATES)
    if value is None:
        return "", None
    return _stringify(value), path


def build_event_dedupe_key(
    packet: Mapping[str, Any],
    *,
    bridge_name: Optional[str] = None,
    event_type_hint: Optional[str] = None,
) -> Dict[str, Any]:
    event_type, event_type_field = _canonical_event_type(packet, event_type_hint)
    family = _schema_family(packet) or _stringify(packet.get("packet_type")) or "unknown"
    subject_text, subject_key, subject_field = _canonical_subject(packet)
    event_time_hint, event_time_field = _event_time_hint(packet)
    source_domain, source_domain_field = _first_value_with_path(packet, _SOURCE_DOMAIN_CANDIDATES)
    source_url, source_url_field = _first_value_with_path(packet, _SOURCE_URL_CANDIDATES)
    source_tier, source_tier_field = _first_value_with_path(packet, _SOURCE_TIER_CANDIDATES)
    source_type, source_type_field = _first_value_with_path(packet, _SOURCE_TYPE_CANDIDATES)
    source_type = source_type if source_type is not None else None
    official_source = bool(packet.get("official_source"))
    official_source_confirmed = bool(packet.get("official_source_confirmed"))
    source_family = _source_family(
        _stringify(source_tier),
        _stringify(source_type),
        official_source,
    )
    asset_tags, asset_tags_field = _infer_asset_tags(packet, subject_text)
    geography_tags, geography_tags_field = _infer_geography_tags(packet, subject_text)
    time_horizon, time_horizon_field = _infer_time_horizon(
        packet,
        family=family,
        source_type=_stringify(source_type),
        event_type=event_type,
    )
    source_credibility, source_credibility_field = _infer_source_credibility(packet, source_family)

    dedupe_components = {
        "family": normalize_event_text(family),
        "event_type": normalize_event_text(event_type),
        "subject_key": subject_key,
        "event_time_hint": normalize_event_text(event_time_hint),
        "time_horizon": normalize_event_text(time_horizon),
    }
    raw = "|".join(f"{k}={v}" for k, v in dedupe_components.items())
    canonical_event_key = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]

    source_key_raw = json.dumps(
        {
            "bridge_name": bridge_name or _stringify(_get_nested(packet, "provenance.bridge") or packet.get("bridge")),
            "event_id": _stringify(packet.get("event_id") or packet.get("packet_id") or packet.get("canonical_event_id")),
            "event_time_hint": event_time_hint,
            "source_domain": _stringify(source_domain),
            "source_url": _stringify(source_url),
            "source_feed_url": _stringify(packet.get("source_feed_url")),
            "subject_text": subject_text,
            "subject_key": subject_key,
        },
        sort_keys=True,
        ensure_ascii=False,
    )
    source_event_key = hashlib.sha256(source_key_raw.encode("utf-8")).hexdigest()[:20]

    canonical_event_id = f"evt_{canonical_event_key}"
    source_event_id = _stringify(
        packet.get("event_id")
        or packet.get("packet_id")
        or packet.get("source_event_id")
    )

    return {
        "ledger_version": LEDGER_VERSION,
        "bridge_name": bridge_name or _stringify(_get_nested(packet, "provenance.bridge") or packet.get("bridge")),
        "canonical_event_id": canonical_event_id,
        "canonical_event_key": canonical_event_key,
        "canonical_dedupe_key": canonical_event_key,
        "event_id": canonical_event_id,
        "dedupe_key": canonical_event_key,
        "source_event_key": source_event_key,
        "source_event_id": source_event_id or None,
        "canonical_event_family": family,
        "canonical_event_type": event_type,
        "canonical_subject_text": subject_text,
        "canonical_subject_normalized": normalize_event_text(subject_text),
        "canonical_subject_key": subject_key,
        "canonical_event_time_hint": event_time_hint or None,
        "asset_tags": asset_tags,
        "geography_tags": geography_tags,
        "time_horizon": time_horizon,
        "source_credibility": source_credibility,
        "canonical_source": {
            "source_domain": _stringify(source_domain) or None,
            "source_url": _stringify(source_url) or None,
            "source_feed_url": _stringify(packet.get("source_feed_url")) or None,
            "source_tier": _stringify(source_tier) or None,
            "source_type": _stringify(source_type) or None,
            "source_family": source_family,
            "official_source": official_source,
            "official_source_confirmed": official_source_confirmed,
            "source_credibility": source_credibility,
        },
        "novelty_score": None,  # filled by build_event_ledger
        "dedupe_score": None,  # filled by build_event_ledger
        "novelty_factors": [],
        "dedupe_factors": [],
        "field_map": {
            "event_type": event_type_field,
            "subject_text": subject_field,
            "source_domain": source_domain_field,
            "source_url": source_url_field,
            "source_tier": source_tier_field,
            "source_type": source_type_field,
            "event_time_hint": event_time_field,
            "asset_tags": asset_tags_field,
            "geography_tags": geography_tags_field,
            "time_horizon": time_horizon_field,
            "source_credibility": source_credibility_field,
        },
        "dedupe_components": dedupe_components,
        "source_context": {
            "bridge_name": bridge_name or _stringify(_get_nested(packet, "provenance.bridge") or packet.get("bridge")) or None,
            "event_id": _stringify(packet.get("event_id") or packet.get("packet_id") or packet.get("canonical_event_id")) or None,
            "packet_type": _stringify(packet.get("packet_type")) or None,
        },
    }


def score_event_novelty(
    packet: Mapping[str, Any],
    *,
    ledger: Optional[Mapping[str, Any]] = None,
    source_type_hint: Optional[str] = None,
    event_type_hint: Optional[str] = None,
) -> Tuple[float, List[str], float]:
    ledger = ledger or build_event_dedupe_key(
        packet,
        event_type_hint=event_type_hint,
    )
    source = ledger["canonical_source"]
    source_family = str(source.get("source_family") or "unknown")
    source_type = str(source.get("source_type") or source_type_hint or "").lower()
    event_type = str(ledger.get("canonical_event_type") or "").lower()
    subject_text = str(ledger.get("canonical_subject_text") or "")
    subject_norm = str(ledger.get("canonical_subject_normalized") or "")
    event_time_hint = str(ledger.get("canonical_event_time_hint") or "")
    tags = [
        str(tag).lower()
        for tag in (packet.get("tags") or [])
        if str(tag).strip()
    ]

    score = 0.0
    factors: List[str] = []

    base_by_family = {
        "official": 0.58,
        "institutional": 0.46,
        "research": 0.40,
        "alt": 0.34,
        "unknown": 0.38,
    }
    score += base_by_family.get(source_family, 0.38)
    factors.append(f"source_family:{source_family}:{base_by_family.get(source_family, 0.38):+.2f}")

    source_type_bonus = {
        "api": 0.10,
        "rss": 0.08,
        "csv": 0.08,
        "page_link": 0.06,
        "page": -0.10,
        "search_result": 0.05,
        "error": -0.20,
    }.get(source_type, 0.0)
    score += source_type_bonus
    factors.append(f"source_type:{source_type or 'unknown'}:{source_type_bonus:+.2f}")

    source_credibility = 0.5
    try:
        source_credibility = float(ledger.get("source_credibility", source.get("source_credibility", 0.5)))
    except Exception:
        source_credibility = 0.5
    source_credibility = max(0.0, min(1.0, source_credibility))
    credibility_adj = (source_credibility - 0.5) * 0.20
    score += credibility_adj
    factors.append(f"source_credibility:{source_credibility:.2f}:{credibility_adj:+.2f}")

    if packet.get("official_source") is True:
        score += 0.04
        factors.append("official_source:+0.04")
    if packet.get("official_source_confirmed") is True:
        score += 0.05
        factors.append("official_source_confirmed:+0.05")
    if packet.get("rate_regime_shock_candidate") is True:
        score += 0.08
        factors.append("rate_regime_shock_candidate:+0.08")
    if packet.get("requires_rate_cross_asset_check") is True:
        score += 0.04
        factors.append("requires_rate_cross_asset_check:+0.04")
    if event_time_hint:
        score += 0.04
        factors.append("event_time_hint:+0.04")
    if subject_norm and subject_norm not in {event_type, "macro calendar update", "unknown"}:
        score += 0.03
        factors.append("subject_specificity:+0.03")
    if len(subject_text) > 80:
        score += 0.02
        factors.append("subject_richness:+0.02")
    if ledger.get("asset_tags"):
        score += 0.02
        factors.append("asset_tags:+0.02")
    if ledger.get("geography_tags"):
        score += 0.02
        factors.append("geography_tags:+0.02")
    if ledger.get("time_horizon") in {"event_window", "short_term"}:
        score += 0.02
        factors.append(f"time_horizon:{ledger.get('time_horizon')}:+0.02")
    if any(tag in {"headline_item", "inventory_draw_signal", "refinery_utilization_drop_signal", "item"} for tag in tags):
        score += 0.05
        factors.append("signal_specificity:+0.05")
    if any(tag in {"page_heartbeat", "cannot_confirm_policy_event"} for tag in tags):
        score -= 0.10
        factors.append("heartbeat_penalty:-0.10")
    if source_type == "page" and event_type in {"macro_calendar_update", "bridge_error"}:
        score -= 0.06
        factors.append("page_generic_penalty:-0.06")
    if "error" in event_type or packet.get("error") is not None:
        score -= 0.12
        factors.append("error_penalty:-0.12")

    score = max(0.0, min(1.0, score))
    dedupe_score = max(0.0, min(1.0, 1.0 - score))
    return round(score, 4), factors, round(dedupe_score, 4)


def build_event_ledger(
    packet: Mapping[str, Any],
    *,
    bridge_name: Optional[str] = None,
    source_type_hint: Optional[str] = None,
    event_type_hint: Optional[str] = None,
) -> Dict[str, Any]:
    ledger = build_event_dedupe_key(
        packet,
        bridge_name=bridge_name,
        event_type_hint=event_type_hint,
    )

    source = dict(ledger["canonical_source"])
    if not source.get("source_type") and source_type_hint:
        source["source_type"] = source_type_hint
        source["source_family"] = _source_family(
            str(source.get("source_tier") or ""),
            source_type_hint,
            bool(source.get("official_source")),
        )
    ledger["canonical_source"] = source

    novelty_score, novelty_factors, dedupe_score = score_event_novelty(
        packet,
        ledger=ledger,
        source_type_hint=source_type_hint,
        event_type_hint=event_type_hint,
    )
    ledger["novelty_score"] = novelty_score
    ledger["dedupe_score"] = dedupe_score
    ledger["novelty_factors"] = novelty_factors
    ledger["dedupe_factors"] = [f"dedupe_key:{ledger['canonical_event_key']}"]

    return ledger


def attach_event_ledger(
    packet: Mapping[str, Any],
    *,
    bridge_name: Optional[str] = None,
    source_type_hint: Optional[str] = None,
    event_type_hint: Optional[str] = None,
) -> Dict[str, Any]:
    out = dict(packet)
    ledger = build_event_ledger(
        out,
        bridge_name=bridge_name,
        source_type_hint=source_type_hint,
        event_type_hint=event_type_hint,
    )
    existing = dict(out.get("event_ledger") or {})
    existing.update(ledger)
    out["event_ledger"] = existing
    out["event_id"] = existing["canonical_event_id"]
    out["canonical_event_id"] = existing["canonical_event_id"]
    out["canonical_event_key"] = existing["canonical_event_key"]
    out["canonical_dedupe_key"] = existing["dedupe_key"]
    out["dedupe_key"] = existing["dedupe_key"]
    out["canonical_event_family"] = existing["canonical_event_family"]
    out["canonical_event_type"] = existing["canonical_event_type"]
    out["canonical_subject"] = existing["canonical_subject_text"]
    out["canonical_subject_key"] = existing["canonical_subject_key"]
    out["asset_tags"] = existing.get("asset_tags", [])
    out["geography_tags"] = existing.get("geography_tags", [])
    out["time_horizon"] = existing.get("time_horizon")
    out["source_credibility"] = existing.get("source_credibility")
    out["canonical_novelty_score"] = existing["novelty_score"]
    out["canonical_dedupe_score"] = existing["dedupe_score"]
    out["canonical_source_family"] = existing["canonical_source"]["source_family"]
    out["canonical_source_type"] = existing["canonical_source"]["source_type"]
    return out


__all__ = [
    "LEDGER_VERSION",
    "attach_event_ledger",
    "build_event_dedupe_key",
    "build_event_ledger",
    "normalize_event_text",
    "score_event_novelty",
    "utc_now_iso",
]

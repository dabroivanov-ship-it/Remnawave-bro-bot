"""Pure helpers for Remnawave IP control / sharing detection."""

from __future__ import annotations

import ipaddress
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

# Neighbor addresses in one operator pool count as one source, not a crowd.
# /24 covers a typical CGNAT/NAT block without merging half a city.
IPV4_SOURCE_PREFIX = 24
# An IPv6 subscriber is usually given a whole /64.
IPV6_SOURCE_PREFIX = 64


def extract_ip(payload: dict[str, Any] | None) -> str | None:
    if not isinstance(payload, dict):
        return None
    for key in ('requestIp', 'request_ip', 'ip', 'ipAddress', 'ip_address'):
        value = payload.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return None


def extract_panel_user_id(payload: dict[str, Any] | None) -> int | None:
    if not isinstance(payload, dict):
        return None
    for key in ('userId', 'user_id'):
        value = payload.get(key)
        if value is None:
            continue
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    return None


def parse_request_time(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        text = str(value).strip()
        if not text:
            return None
        if text.endswith('Z'):
            text = text[:-1] + '+00:00'
        try:
            dt = datetime.fromisoformat(text)
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def unique_ips_from_history(
    records: list[dict[str, Any]],
    *,
    cutoff: datetime | None = None,
) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for record in records:
        ip = extract_ip(record)
        if not ip or ip in seen:
            continue
        if cutoff is not None:
            request_at = parse_request_time(record.get('requestAt') or record.get('request_at'))
            if request_at is not None and request_at < cutoff:
                continue
        seen.add(ip)
        ordered.append(ip)
    return ordered


def source_key(ip: str) -> str:
    """Collapse an address to its sharing source: IPv4 /24 or IPv6 /64."""
    text = str(ip).strip()
    if not text:
        return text
    try:
        address = ipaddress.ip_address(text)
    except ValueError:
        return text
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        address = address.ipv4_mapped
    prefix = IPV6_SOURCE_PREFIX if address.version == 6 else IPV4_SOURCE_PREFIX
    return str(ipaddress.ip_network(f'{address}/{prefix}', strict=False))


def count_sources(ips: Iterable[str]) -> int:
    return len({source_key(ip) for ip in ips if ip})


def is_sharing_threshold(unique_ip_count: int, threshold: int) -> bool:
    return unique_ip_count >= max(2, threshold)


def normalize_chat_id(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def normalize_topic_id(value: Any) -> int | None:
    if value is None or value == '':
        return None
    try:
        topic = int(value)
    except (TypeError, ValueError):
        return None
    return topic or None


def resolve_alert_destination(
    chat_id: Any,
    topic_id: Any,
    *,
    default_chat_id: Any = None,
    default_topic_id: Any = None,
    fallback_topic_id: Any = None,
) -> tuple[str | None, int | None]:
    chat = normalize_chat_id(chat_id) or normalize_chat_id(default_chat_id)
    topic = normalize_topic_id(topic_id)
    if topic is None:
        topic = normalize_topic_id(default_topic_id) or normalize_topic_id(fallback_topic_id)
    return chat, topic

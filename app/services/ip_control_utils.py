"""Pure helpers for Remnawave IP control / sharing detection."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any


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


def is_sharing_threshold(unique_ip_count: int, threshold: int) -> bool:
    return unique_ip_count >= max(2, threshold)

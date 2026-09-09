from datetime import UTC, datetime, timedelta

from app.services.ip_control_utils import (
    extract_ip,
    extract_panel_user_id,
    is_sharing_threshold,
    parse_request_time,
    unique_ips_from_history,
)


def test_extract_ip_prefers_request_ip():
    assert extract_ip({'requestIp': '1.1.1.1', 'ip': '8.8.8.8'}) == '1.1.1.1'
    assert extract_ip({'ipAddress': ' 2.2.2.2 '}) == '2.2.2.2'
    assert extract_ip({'ip': ''}) is None
    assert extract_ip(None) is None


def test_extract_panel_user_id():
    assert extract_panel_user_id({'userId': 42}) == 42
    assert extract_panel_user_id({'user_id': '7'}) == 7
    assert extract_panel_user_id({'id': 'x'}) is None
    assert extract_panel_user_id({'id': 99}) is None


def test_unique_ips_from_history_filters_window_and_duplicates():
    now = datetime.now(UTC)
    records = [
        {'requestIp': '10.0.0.1', 'requestAt': now.isoformat()},
        {'requestIp': '10.0.0.1', 'requestAt': now.isoformat()},
        {'requestIp': '10.0.0.2', 'requestAt': (now - timedelta(hours=2)).isoformat()},
        {'requestIp': '10.0.0.3', 'requestAt': (now - timedelta(days=3)).isoformat()},
        {'ip': '10.0.0.4'},
    ]
    cutoff = now - timedelta(hours=24)
    ips = unique_ips_from_history(records, cutoff=cutoff)
    assert ips == ['10.0.0.1', '10.0.0.2', '10.0.0.4']


def test_sharing_threshold():
    assert is_sharing_threshold(5, 5) is True
    assert is_sharing_threshold(4, 5) is False
    assert is_sharing_threshold(2, 1) is True


def test_parse_request_time_zulu():
    parsed = parse_request_time('2026-09-09T12:00:00Z')
    assert parsed is not None
    assert parsed.tzinfo is not None
    assert parsed.hour == 12

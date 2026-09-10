from datetime import UTC, datetime, timedelta

from app.services.ip_control_utils import (
    count_sources,
    extract_ip,
    extract_panel_user_id,
    is_sharing_threshold,
    parse_request_time,
    resolve_alert_destination,
    source_key,
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


def test_resolve_alert_destination_uses_ip_control_override():
    chat, topic = resolve_alert_destination(
        '-100111',
        21,
        default_chat_id='-100000',
        default_topic_id=14,
        fallback_topic_id=1,
    )
    assert chat == '-100111'
    assert topic == 21


def test_resolve_alert_destination_falls_back_when_empty():
    chat, topic = resolve_alert_destination(
        '  ',
        0,
        default_chat_id='-100000',
        default_topic_id=14,
        fallback_topic_id=1,
    )
    assert chat == '-100000'
    assert topic == 14


def test_resolve_alert_destination_uses_general_topic_if_suspicious_missing():
    chat, topic = resolve_alert_destination(
        None,
        None,
        default_chat_id='-100000',
        default_topic_id=None,
        fallback_topic_id=3,
    )
    assert chat == '-100000'
    assert topic == 3


def test_sharing_threshold():
    assert is_sharing_threshold(5, 5) is True
    assert is_sharing_threshold(4, 5) is False
    assert is_sharing_threshold(2, 1) is True


def test_parse_request_time_zulu():
    parsed = parse_request_time('2026-09-09T12:00:00Z')
    assert parsed is not None
    assert parsed.tzinfo is not None
    assert parsed.hour == 12


def test_source_key_collapses_ipv4_slash_24():
    assert source_key('10.20.30.1') == '10.20.30.0/24'
    assert source_key('10.20.30.1') == source_key('10.20.30.254')
    assert source_key('10.20.31.1') != source_key('10.20.30.1')


def test_source_key_collapses_ipv6_slash_64():
    assert source_key('2001:db8:1:2::1') == source_key('2001:db8:1:2::ffff')
    assert source_key('2001:db8:1:3::1') != source_key('2001:db8:1:2::1')


def test_source_key_maps_ipv4_mapped_ipv6():
    assert source_key('::ffff:10.20.30.4') == source_key('10.20.30.99')


def test_count_sources_treats_cgnat_pool_as_one():
    ips = ['188.170.87.10', '188.170.87.11', '188.170.87.200', '8.8.8.8']
    assert count_sources(ips) == 2
    assert is_sharing_threshold(count_sources(ips), 5) is False
    assert is_sharing_threshold(len(ips), 5) is False
    many_in_one_net = [f'10.0.0.{n}' for n in range(1, 8)]
    assert len(many_in_one_net) == 7
    assert count_sources(many_in_one_net) == 1
    mixed = many_in_one_net + ['10.0.1.1', '10.0.2.1', '10.0.3.1', '10.0.4.1']
    assert count_sources(mixed) == 5
    assert is_sharing_threshold(count_sources(mixed), 5) is True

"""Unit tests for Remnawave API 3.x client contract."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.external.remnawave_api import (
    RemnaWaveAPI,
    RemnaWaveUser,
    UserStatus,
    apply_panel_identity,
    resolve_panel_user,
)


def _user_payload(**overrides) -> dict:
    base = {
        'id': 42,
        'shortUuid': 'abc123',
        'username': 'user_100',
        'status': 'ACTIVE',
        'trafficLimitBytes': 0,
        'trafficLimitStrategy': 'NO_RESET',
        'expireAt': '2030-01-01T00:00:00.000Z',
        'telegramId': 100,
        'email': None,
        'hwidDeviceLimit': 1,
        'description': None,
        'tag': None,
        'subscriptionUrl': 'https://example/sub',
        'activeInternalSquads': [],
        'createdAt': '2026-01-01T00:00:00.000Z',
        'updatedAt': '2026-01-02T00:00:00.000Z',
    }
    base.update(overrides)
    return base


def test_parse_user_requires_id_not_uuid():
    api = RemnaWaveAPI(base_url='https://panel.example', api_key='x')
    user = api._parse_user(_user_payload())
    assert user.id == 42
    assert user.short_uuid == 'abc123'
    assert user.uuid == '42'  # transitional alias
    assert user.status == UserStatus.ACTIVE


def test_parse_user_fails_without_id():
    api = RemnaWaveAPI(base_url='https://panel.example', api_key='x')
    payload = _user_payload()
    del payload['id']
    with pytest.raises(KeyError):
        api._parse_user(payload)


@pytest.mark.asyncio
async def test_delete_user_treats_empty_204_as_success():
    api = RemnaWaveAPI(base_url='https://panel.example', api_key='x')
    api._make_request = AsyncMock(return_value={})
    assert await api.delete_user(42) is True
    api._make_request.assert_awaited_once_with('DELETE', '/api/users/42')


@pytest.mark.asyncio
async def test_remove_device_sends_user_id():
    api = RemnaWaveAPI(base_url='https://panel.example', api_key='x')
    api._make_request = AsyncMock(return_value={})
    assert await api.remove_device(42, 'hwid-1') is True
    api._make_request.assert_awaited_once_with(
        'POST',
        '/api/hwid/devices/delete',
        data={'userId': 42, 'hwid': 'hwid-1'},
    )


@pytest.mark.asyncio
async def test_resolve_panel_user_order():
    api = MagicMock()
    api.get_user_by_id = AsyncMock(return_value=None)
    api.get_user_by_short_uuid = AsyncMock(
        return_value=SimpleNamespace(id=7, short_uuid='short', username='u')
    )
    api.get_user_by_username = AsyncMock(return_value=None)

    user = await resolve_panel_user(api, remnawave_id=7, short_uuid='short', username='u')
    assert user.id == 7
    api.get_user_by_id.assert_awaited_once()
    api.get_user_by_short_uuid.assert_awaited_once_with('short')
    api.get_user_by_username.assert_not_awaited()


def test_apply_panel_identity():
    panel_user = RemnaWaveUser(
        id=99,
        short_uuid='s99',
        username='user_99',
        status=UserStatus.ACTIVE,
        traffic_limit_bytes=0,
        traffic_limit_strategy=__import__(
            'app.external.remnawave_api', fromlist=['TrafficLimitStrategy']
        ).TrafficLimitStrategy.NO_RESET,
        expire_at=datetime(2030, 1, 1, tzinfo=UTC),
        telegram_id=1,
        email=None,
        hwid_device_limit=1,
        description=None,
        tag=None,
        subscription_url='',
        active_internal_squads=[],
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        updated_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    user = SimpleNamespace(remnawave_id=None, remnawave_uuid=None)
    sub = SimpleNamespace(remnawave_id=None, remnawave_uuid=None, remnawave_short_uuid=None)
    apply_panel_identity(user=user, subscription=sub, panel_user=panel_user)
    assert user.remnawave_id == 99
    assert user.remnawave_uuid is None  # legacy uuid column left untouched
    assert sub.remnawave_id == 99
    assert sub.remnawave_short_uuid == 's99'
    assert sub.remnawave_uuid is None


def test_parse_system_stats_maps_online_users_and_traffic_bytes():
    """3.x stats: onlineNow is users online; totalBytesLifetime is user traffic.

    nodes.totalOnline is connections on nodes, and users.totalTrafficBytes is absent.
    """
    api = RemnaWaveAPI(base_url='https://panel.example', api_key='x')
    parsed = api.parse_system_stats(
        {
            'onlineStats': {
                'onlineNow': 4,
                'lastDay': 10,
                'lastWeek': 20,
                'neverOnline': 3,
            },
            'users': {
                'totalUsers': 100,
                'statusCounts': {'ACTIVE': 80},
            },
            'nodes': {
                'totalOnline': 21,
                'totalBytesLifetime': '4096',
            },
            'cpu': {'cores': 2},
            'memory': {'total': 1000, 'used': 400, 'free': 600},
            'uptime': 50,
        }
    )
    assert parsed['users_online'] == 4
    assert parsed['users_online'] != 21
    assert parsed['total_user_traffic'] == 4096
    assert parsed['total_users'] == 100
    assert parsed['users_last_day'] == 10
    assert parsed['users_by_status'] == {'ACTIVE': 80}


def test_count_connected_nodes_skips_offline_and_disabled():
    api = RemnaWaveAPI(base_url='https://panel.example', api_key='x')
    nodes = [
        SimpleNamespace(is_connected=True, is_disabled=False),
        SimpleNamespace(is_connected=True, is_disabled=True),
        SimpleNamespace(is_connected=False, is_disabled=False),
        {'isConnected': True, 'isDisabled': False},
    ]
    assert api.count_connected_nodes(nodes) == 2
    assert api.count_connected_nodes(nodes) != 21

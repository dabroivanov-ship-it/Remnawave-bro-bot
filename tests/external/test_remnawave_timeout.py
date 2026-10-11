"""Panel connect timeouts retry, then skip the operator error report."""

from __future__ import annotations

import sys
import types
from unittest.mock import AsyncMock, MagicMock

import aiohttp
import pytest
from structlog.testing import capture_logs

from app.external.remnawave_api import (
    RemnaWaveAPI,
    RemnaWaveAPIError,
    RemnaWaveTimeoutError,
)


# tests/conftest.py stubs redis as a non-package. Give it an exceptions module
# so the IP-control service can be imported without the real client.
if 'redis.exceptions' not in sys.modules:
    _redis_mod = sys.modules.get('redis')
    if _redis_mod is not None and not hasattr(_redis_mod, '__path__'):
        _redis_mod.__path__ = []  # type: ignore[attr-defined]
    _redis_exc = types.ModuleType('redis.exceptions')
    _redis_exc.NoScriptError = type('NoScriptError', (Exception,), {})
    sys.modules['redis.exceptions'] = _redis_exc

from app.services.ip_control_service import IpControlScheduler, IpControlService


class _BodyResponse:
    def __init__(self, status: int, body: str):
        self.status = status
        self.headers: dict[str, str] = {}
        self._body = body

    async def text(self) -> str:
        return self._body

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _TimeoutResponse:
    async def __aenter__(self):
        raise aiohttp.ConnectionTimeoutError('Connection timeout to host https://panel.example/api/users')

    async def __aexit__(self, exc_type, exc, tb):
        return False


def _api_with(request) -> RemnaWaveAPI:
    api = RemnaWaveAPI('https://panel.example', 'test-key')
    api.session = MagicMock()
    api.session.request = request
    return api


@pytest.mark.asyncio
async def test_connect_timeout_retries_then_raises_timeout_error(monkeypatch):
    sleep = AsyncMock()
    monkeypatch.setattr('app.external.remnawave_api.asyncio.sleep', sleep)
    request = MagicMock(side_effect=lambda *args, **kwargs: _TimeoutResponse())
    api = _api_with(request)

    with pytest.raises(RemnaWaveTimeoutError, match='Connection timeout') as caught:
        await api.get_system_stats()

    assert caught.value.status_code is None
    assert request.call_count == 4
    assert sleep.await_count == 3
    delays = [call.args[0] for call in sleep.await_args_list]
    assert delays == [1.0, 2.0, 4.0]


@pytest.mark.asyncio
async def test_http_error_is_not_a_timeout(monkeypatch):
    sleep = AsyncMock()
    monkeypatch.setattr('app.external.remnawave_api.asyncio.sleep', sleep)
    request = MagicMock(return_value=_BodyResponse(401, '{"message":"Unauthorized"}'))
    api = _api_with(request)

    with pytest.raises(RemnaWaveAPIError) as caught:
        await api._make_request('GET', '/api/system/stats')

    assert not isinstance(caught.value, RemnaWaveTimeoutError)
    assert caught.value.status_code == 401
    assert request.call_count == 1
    sleep.assert_not_awaited()


@pytest.mark.asyncio
async def test_server_error_stays_api_error_after_retries(monkeypatch):
    monkeypatch.setattr('app.external.remnawave_api.asyncio.sleep', AsyncMock())
    request = MagicMock(return_value=_BodyResponse(503, '{"message":"unavailable"}'))
    api = _api_with(request)

    with pytest.raises(RemnaWaveAPIError) as caught:
        await api.get_system_stats()

    assert not isinstance(caught.value, RemnaWaveTimeoutError)
    assert caught.value.status_code == 503
    assert request.call_count == 4


@pytest.mark.asyncio
async def test_successful_stats_response_is_unchanged():
    request = MagicMock(return_value=_BodyResponse(200, '{"response":{"users":{"totalUsers":3}}}'))
    api = _api_with(request)

    stats = await api.get_system_stats()

    assert stats == {'users': {'totalUsers': 3}}
    assert request.call_count == 1


@pytest.mark.asyncio
async def test_ip_control_loop_skips_cycle_on_panel_timeout(monkeypatch):
    service = IpControlService()
    scheduler = IpControlScheduler(service)
    scheduler._is_running = True
    scans = {'count': 0}

    async def fail_scan(*args, **kwargs):
        scans['count'] += 1
        scheduler._is_running = False
        raise RemnaWaveTimeoutError('Request failed: Connection timeout to host')

    monkeypatch.setattr(service, 'is_enabled', lambda: True)
    monkeypatch.setattr(service, 'run_scan', fail_scan)
    monkeypatch.setattr('app.services.ip_control_service.asyncio.sleep', AsyncMock())

    with capture_logs() as logs:
        await scheduler._loop()

    assert scans['count'] == 1
    assert any(
        entry['log_level'] == 'warning' and entry['event'] == 'IP control scan skipped: Remnawave panel timed out'
        for entry in logs
    )
    assert not any(entry['log_level'] == 'error' for entry in logs)


@pytest.mark.asyncio
async def test_ip_control_loop_still_reports_api_errors(monkeypatch):
    service = IpControlService()
    scheduler = IpControlScheduler(service)
    scheduler._is_running = True

    async def fail_scan(*args, **kwargs):
        scheduler._is_running = False
        raise RemnaWaveAPIError('Unauthorized', 401)

    monkeypatch.setattr(service, 'is_enabled', lambda: True)
    monkeypatch.setattr(service, 'run_scan', fail_scan)
    monkeypatch.setattr('app.services.ip_control_service.asyncio.sleep', AsyncMock())

    with capture_logs() as logs:
        await scheduler._loop()

    assert any(entry['log_level'] == 'error' and entry['event'] == 'IP control scan failed' for entry in logs)

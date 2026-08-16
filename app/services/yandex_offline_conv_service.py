"""Yandex.Metrika offline conversions service.

Sends events (registration, trial-add, purchase) to mc.yandex.ru/collect
using the Measurement Protocol. Config is stored in cabinet SystemSetting
(with env fallback for bootstrap).
"""

from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass

import httpx
import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database.crud.system_setting import get_setting_value, upsert_system_setting
from app.database.crud.yandex_client_id import (
    get_cid,
    mark_registration_sent,
    mark_trial_sent,
    upsert_cid,
)
from app.database.database import AsyncSessionLocal


logger = structlog.get_logger(__name__)

COLLECT_URL = 'https://mc.yandex.ru/collect'
TIMEOUT = 10.0
MAX_RETRIES = 3
RETRY_DELAY = 1.0
CONFIG_CACHE_TTL = 30.0

# Cabinet-managed keys in system_settings
KEY_ENABLED = 'CABINET_YANDEX_OFFLINE_CONV_ENABLED'
KEY_COUNTER_ID = 'CABINET_YANDEX_OFFLINE_CONV_COUNTER_ID'
KEY_SECRET = 'CABINET_YANDEX_OFFLINE_CONV_MEASUREMENT_SECRET'
KEY_DL = 'CABINET_YANDEX_OFFLINE_CONV_DL'
KEY_DT = 'CABINET_YANDEX_OFFLINE_CONV_DT'
KEY_CURRENCY = 'CABINET_YANDEX_OFFLINE_CONV_CURRENCY'
KEY_START_PREFIX = 'CABINET_YANDEX_OFFLINE_CONV_START_PREFIX'

_CID_RE = re.compile(r'^[A-Za-z0-9._:-]{4,128}$')
_http_client: httpx.AsyncClient | None = None
_config_cache: OfflineConvConfig | None = None
_config_loaded_at = 0.0


@dataclass(frozen=True)
class OfflineConvConfig:
    enabled: bool = False
    counter_id: str = ''
    measurement_secret: str = ''
    dl: str = ''
    dt: str = ''
    currency: str = 'RUB'
    start_prefix: str = 'utm_ya_'

    @property
    def is_ready(self) -> bool:
        return bool(self.enabled and self.counter_id and self.measurement_secret)

    @property
    def secret_masked(self) -> str:
        secret = self.measurement_secret
        if len(secret) > 4:
            return '*' * 8 + secret[-4:]
        return '***' if secret else ''


def _env_config() -> OfflineConvConfig:
    return OfflineConvConfig(
        enabled=bool(getattr(settings, 'YANDEX_OFFLINE_CONV_ENABLED', False)),
        counter_id=str(getattr(settings, 'YANDEX_OFFLINE_CONV_COUNTER_ID', '') or ''),
        measurement_secret=str(getattr(settings, 'YANDEX_OFFLINE_CONV_MEASUREMENT_SECRET', '') or ''),
        dl=str(getattr(settings, 'YANDEX_OFFLINE_CONV_DL', '') or ''),
        dt=str(getattr(settings, 'YANDEX_OFFLINE_CONV_DT', '') or ''),
        currency=str(getattr(settings, 'YANDEX_OFFLINE_CONV_CURRENCY', '') or 'RUB'),
        start_prefix=str(getattr(settings, 'YANDEX_OFFLINE_CONV_START_PREFIX', '') or 'utm_ya_'),
    )


def invalidate_config_cache() -> None:
    global _config_cache, _config_loaded_at
    _config_cache = None
    _config_loaded_at = 0.0


def get_cached_config() -> OfflineConvConfig:
    """Sync accessor for already-loaded config (falls back to env)."""
    if _config_cache is not None:
        return _config_cache
    return _env_config()


async def get_offline_conv_config(
    db: AsyncSession | None = None,
    *,
    force_refresh: bool = False,
) -> OfflineConvConfig:
    """Load offline-conversion settings from cabinet DB (env fallback)."""
    global _config_cache, _config_loaded_at

    now = time.time()
    if (
        not force_refresh
        and _config_cache is not None
        and (now - _config_loaded_at) < CONFIG_CACHE_TTL
    ):
        return _config_cache

    async def _load(session: AsyncSession) -> OfflineConvConfig:
        env = _env_config()
        enabled_raw = await get_setting_value(session, KEY_ENABLED)
        counter = await get_setting_value(session, KEY_COUNTER_ID)
        secret = await get_setting_value(session, KEY_SECRET)
        dl = await get_setting_value(session, KEY_DL)
        dt = await get_setting_value(session, KEY_DT)
        currency = await get_setting_value(session, KEY_CURRENCY)
        prefix = await get_setting_value(session, KEY_START_PREFIX)

        # If nothing stored in cabinet yet, seed from env once so UI shows current values.
        if enabled_raw is None and counter is None and secret is None and env.is_ready:
            await upsert_system_setting(session, KEY_ENABLED, 'true' if env.enabled else 'false')
            await upsert_system_setting(session, KEY_COUNTER_ID, env.counter_id)
            await upsert_system_setting(session, KEY_SECRET, env.measurement_secret)
            await upsert_system_setting(session, KEY_DL, env.dl)
            await upsert_system_setting(session, KEY_DT, env.dt)
            await upsert_system_setting(session, KEY_CURRENCY, env.currency)
            await upsert_system_setting(session, KEY_START_PREFIX, env.start_prefix)
            await session.commit()
            return env

        enabled = (
            enabled_raw.lower() == 'true'
            if enabled_raw is not None
            else env.enabled
        )
        return OfflineConvConfig(
            enabled=enabled,
            counter_id=(counter if counter is not None else env.counter_id) or '',
            measurement_secret=(secret if secret is not None else env.measurement_secret) or '',
            dl=(dl if dl is not None else env.dl) or '',
            dt=(dt if dt is not None else env.dt) or '',
            currency=(currency if currency is not None else env.currency) or 'RUB',
            start_prefix=(prefix if prefix is not None else env.start_prefix) or 'utm_ya_',
        )

    try:
        if db is not None:
            config = await _load(db)
        else:
            async with AsyncSessionLocal() as session:
                config = await _load(session)
    except Exception as exc:
        logger.warning('Failed to load offline conv config from DB, using env', error=str(exc))
        config = _env_config()

    _config_cache = config
    _config_loaded_at = now
    return config


async def save_offline_conv_config(
    db: AsyncSession,
    *,
    enabled: bool | None = None,
    counter_id: str | None = None,
    measurement_secret: str | None = None,
    dl: str | None = None,
    dt: str | None = None,
    currency: str | None = None,
    start_prefix: str | None = None,
) -> OfflineConvConfig:
    """Persist offline-conversion settings from cabinet admin."""
    current = await get_offline_conv_config(db, force_refresh=True)

    if enabled is not None:
        await upsert_system_setting(db, KEY_ENABLED, 'true' if enabled else 'false')
    if counter_id is not None:
        await upsert_system_setting(db, KEY_COUNTER_ID, counter_id.strip())
    if measurement_secret is not None:
        # Empty string means "keep existing secret"
        secret = measurement_secret.strip()
        if secret:
            await upsert_system_setting(db, KEY_SECRET, secret)
    if dl is not None:
        await upsert_system_setting(db, KEY_DL, dl.strip())
    if dt is not None:
        await upsert_system_setting(db, KEY_DT, dt.strip())
    if currency is not None:
        await upsert_system_setting(db, KEY_CURRENCY, (currency.strip() or 'RUB'))
    if start_prefix is not None:
        await upsert_system_setting(db, KEY_START_PREFIX, start_prefix.strip())

    await db.commit()
    invalidate_config_cache()
    return await get_offline_conv_config(db, force_refresh=True)


def _get_client() -> httpx.AsyncClient:
    global _http_client
    if _http_client is None or _http_client.is_closed:
        _http_client = httpx.AsyncClient(timeout=TIMEOUT)
    return _http_client


async def _is_enabled(db: AsyncSession | None = None) -> bool:
    cfg = await get_offline_conv_config(db)
    return cfg.is_ready


def _normalize_cid(cid: str | None) -> str | None:
    if not isinstance(cid, str):
        return None
    cid = cid.strip()
    if not cid or not _CID_RE.match(cid):
        return None
    return cid


def _mask_cid(cid: str) -> str:
    if len(cid) <= 4:
        return '****'
    return '*' * (len(cid) - 4) + cid[-4:]


def _base_payload(cfg: OfflineConvConfig, cid: str) -> dict[str, str]:
    return {
        'tid': cfg.counter_id,
        'cid': cid,
        'ms': cfg.measurement_secret,
    }


def _pageview_payload(cfg: OfflineConvConfig, cid: str) -> dict[str, str]:
    payload = _base_payload(cfg, cid)
    payload.update(
        {
            't': 'pageview',
            'dl': cfg.dl or 'https://example.com',
            'dt': cfg.dt or 'VPN BRO',
        }
    )
    return payload


def _event_payload(cfg: OfflineConvConfig, cid: str, event_action: str) -> dict[str, str]:
    payload = _base_payload(cfg, cid)
    payload.update(
        {
            't': 'event',
            'ea': event_action,
        }
    )
    return payload


def _ecommerce_purchase_payload(
    cfg: OfflineConvConfig,
    cid: str,
    amount_rubles: float,
    order_id: str = '',
    product_name: str = '',
    product_category: str = '',
) -> dict[str, str]:
    """Build ecommerce:purchase payload for Metrika Measurement Protocol."""
    service_name = cfg.dt or getattr(settings, 'PAYMENT_SERVICE_NAME', '') or 'Subscription'
    currency = cfg.currency or 'RUB'
    payload = _base_payload(cfg, cid)
    payload.update(
        {
            't': 'event',
            'ea': 'purchase',
            'pa': 'purchase',
            'ti': order_id or str(int(time.time())),
            'tr': str(amount_rubles),
            'cu': currency,
            'ev': str(amount_rubles),
            'pr1id': 'subscription',
            'pr1nm': product_name or service_name,
            'pr1ca': product_category or 'subscription',
            'pr1pr': str(amount_rubles),
            'pr1qt': '1',
        }
    )
    return payload


async def _post_collect(payload: dict[str, str], kind: str, cid: str) -> bool:
    """POST to mc.yandex.ru/collect with retries. Returns True on success."""
    masked = _mask_cid(cid)
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            client = _get_client()
            resp = await client.post(COLLECT_URL, data=payload)

            if 200 <= resp.status_code < 300:
                logger.info('collect sent', kind=kind, cid=masked, status=resp.status_code)
                return True

            if 500 <= resp.status_code < 600 and attempt < MAX_RETRIES:
                logger.warning(
                    'collect server error',
                    kind=kind,
                    attempt=attempt,
                    max=MAX_RETRIES,
                    cid=masked,
                    status=resp.status_code,
                )
                await asyncio.sleep(RETRY_DELAY)
                continue

            logger.error(
                'collect failed',
                kind=kind,
                cid=masked,
                status=resp.status_code,
                body=resp.text[:200],
            )
            return False
        except Exception as exc:
            if attempt < MAX_RETRIES:
                logger.warning(
                    'collect request error',
                    kind=kind,
                    attempt=attempt,
                    cid=masked,
                    error=str(exc),
                )
                await asyncio.sleep(RETRY_DELAY)
                continue
            logger.error('collect request failed', kind=kind, cid=masked, error=str(exc))
            return False
    return False


async def _send_event(cfg: OfflineConvConfig, cid: str, event_action: str) -> bool:
    return await _post_collect(_event_payload(cfg, cid, event_action), event_action, cid)


# --- Background task helpers ---

_background_tasks: set[asyncio.Task] = set()


def _task_done(task):
    """Log errors from background conversion tasks."""
    _background_tasks.discard(task)
    if task.cancelled():
        return
    exc = task.exception()
    if exc:
        logger.error('YandexOfflineConv background task failed', error=str(exc))


def spawn_bg(coro) -> None:
    """Spawn a background Yandex conversion task with proper reference tracking."""

    async def _runner():
        cfg = await get_offline_conv_config()
        if not cfg.is_ready:
            coro.close()
            return
        await coro

    task = asyncio.create_task(_runner())
    _background_tasks.add(task)
    task.add_done_callback(_task_done)


async def _fire_bg(event_name: str, event_fn, user_id: int, **kwargs) -> None:
    """Generic background wrapper: opens a session, calls event_fn, logs errors."""
    try:
        async with AsyncSessionLocal() as db:
            await event_fn(db, user_id, **kwargs)
    except Exception as exc:
        logger.warning('YandexOfflineConv background event failed', event=event_name, user_id=user_id, error=str(exc))


async def fire_registration_bg(user_id: int) -> None:
    """Fire registration event in background with its own DB session."""
    await _fire_bg('registration', on_registration, user_id)


async def fire_trial_bg(user_id: int) -> None:
    """Fire trial event in background with its own DB session."""
    await _fire_bg('trial', on_trial, user_id)


async def fire_purchase_bg(user_id: int, amount_kopeks: int) -> None:
    """Fire purchase event in background with its own DB session."""
    await _fire_bg('purchase', on_purchase, user_id, amount_kopeks=amount_kopeks)


# --- Public API ---
async def store_cid(
    db: AsyncSession,
    user_id: int,
    cid: str | None,
    source: str = 'web',
) -> bool:
    """Store Yandex ClientID for a user. Returns True if stored."""
    normalized = _normalize_cid(cid)
    if not normalized:
        return False

    try:
        cfg = await get_offline_conv_config(db)
        await upsert_cid(db, user_id, normalized, source=source, counter_id=cfg.counter_id or None)
        logger.info('stored CID', user_id=user_id, source=source)
        return True
    except Exception as exc:
        logger.error('failed to store CID', user_id=user_id, error=str(exc))
        return False


async def store_cid_and_fire_registration(
    user_id: int,
    cid: str | None,
    *,
    source: str = 'web',
) -> None:
    """Store Yandex CID and fire registration conversion in background (best-effort)."""
    if not cid:
        return
    try:
        async with AsyncSessionLocal() as db:
            stored = await store_cid(db, user_id, cid, source=source)
            if stored:
                await db.commit()
                spawn_bg(fire_registration_bg(user_id))
    except Exception as exc:
        logger.warning('Failed to store CID and fire registration', user_id=user_id, error=str(exc))


async def store_cid_and_fire_purchase(
    user_id: int,
    cid: str | None,
    amount_kopeks: int,
    *,
    source: str = 'cabinet',
) -> None:
    """Persist a freshly-provided CID, then fire a purchase event in background."""
    if not await _is_enabled():
        return
    try:
        async with AsyncSessionLocal() as db:
            if cid:
                stored = await store_cid(db, user_id, cid, source=source)
                if stored:
                    await db.commit()
        spawn_bg(fire_purchase_bg(user_id, amount_kopeks))
    except Exception as exc:
        logger.warning('Failed to store CID and fire purchase', user_id=user_id, error=str(exc))


async def store_cid_and_fire_trial(
    user_id: int,
    cid: str | None,
    *,
    source: str = 'cabinet',
) -> None:
    """Persist CID and fire trial-add offline conversion in background."""
    if not await _is_enabled():
        return
    try:
        async with AsyncSessionLocal() as db:
            if cid:
                stored = await store_cid(db, user_id, cid, source=source)
                if stored:
                    await db.commit()
        spawn_bg(fire_trial_bg(user_id))
    except Exception as exc:
        logger.warning('Failed to store CID and fire trial', user_id=user_id, error=str(exc))


async def on_registration(db: AsyncSession, user_id: int) -> None:
    """Fire registration event (once per user)."""
    cfg = await get_offline_conv_config(db)
    if not cfg.is_ready:
        return

    try:
        row = await get_cid(db, user_id)
        if not row or row.registration_sent:
            return
        if not row.yandex_cid or row.yandex_cid.startswith('_'):
            return

        success = await _send_event(cfg, row.yandex_cid, 'registration')
        if success:
            await mark_registration_sent(db, user_id)
            await db.commit()
            logger.info('registration event sent', user_id=user_id)
    except Exception as exc:
        logger.error('registration event failed', user_id=user_id, error=str(exc))


async def on_trial(db: AsyncSession, user_id: int) -> None:
    """Fire trial-add event (once per user)."""
    cfg = await get_offline_conv_config(db)
    if not cfg.is_ready:
        return

    try:
        row = await get_cid(db, user_id)
        if not row or row.trial_sent:
            return
        if not row.yandex_cid or row.yandex_cid.startswith('_'):
            return

        success = await _send_event(cfg, row.yandex_cid, 'trial-add')
        if success:
            await mark_trial_sent(db, user_id)
            await db.commit()
            logger.info('trial-add event sent', user_id=user_id)
    except Exception as exc:
        logger.error('trial-add event failed', user_id=user_id, error=str(exc))


async def on_purchase(db: AsyncSession, user_id: int, amount_kopeks: int) -> None:
    """Fire ecommerce purchase event (every payment)."""
    cfg = await get_offline_conv_config(db)
    if not cfg.is_ready:
        return

    try:
        row = await get_cid(db, user_id)
        if not row:
            return
        if not row.yandex_cid or row.yandex_cid.startswith('_'):
            return

        amount_rubles = amount_kopeks / 100
        payload = _ecommerce_purchase_payload(cfg, row.yandex_cid, amount_rubles)
        success = await _post_collect(payload, 'purchase', row.yandex_cid)
        if success:
            logger.info('purchase event sent', user_id=user_id, amount=amount_rubles)
    except Exception as exc:
        logger.error('purchase event failed', user_id=user_id, error=str(exc))


def parse_cid_from_start_param(param: str) -> tuple[str | None, str]:
    """Extract Yandex CID from bot start parameter."""
    prefix = get_cached_config().start_prefix or settings.YANDEX_OFFLINE_CONV_START_PREFIX
    if not prefix or not param.startswith(prefix):
        return None, param

    cid = param[len(prefix) :]
    normalized = _normalize_cid(cid)
    return normalized, param

"""Shared utility for generating campaign deep links and web links."""

from app.config import settings


def get_campaign_deep_link(start_parameter: str) -> str:
    """Generate a Telegram deep link for a campaign."""
    bot_username = settings.get_bot_username()
    if bot_username:
        return f'https://t.me/{bot_username}?start={start_parameter}'
    return f'?start={start_parameter}'


def get_campaign_web_link(start_parameter: str) -> str | None:
    """Generate a web cabinet link for a campaign.

    Points to /login so users land on the auth screen; the cabinet SPA
    captures ?campaign= into localStorage and applies it on register/login.
    Prefers CABINET_URL, falls back to MINIAPP_CUSTOM_URL.
    """
    cabinet_url = settings._normalized_cabinet_url()
    if cabinet_url:
        return f'{cabinet_url}/login?campaign={start_parameter}'

    base_url = (settings.MINIAPP_CUSTOM_URL or '').rstrip('/')
    if base_url:
        return f'{base_url}/login?campaign={start_parameter}'
    return None

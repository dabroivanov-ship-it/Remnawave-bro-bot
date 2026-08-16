"""Dynamic SEO HTML for guide pages (served without frontend rebuild)."""

import structlog
from fastapi import APIRouter, Depends, HTTPException, Path, status
from fastapi.responses import HTMLResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.crud.guides import build_public_hub, get_guide_page_by_slug

from ..dependencies import get_cabinet_db
from ..services.guide_seo_html import (
    bust_template_cache,
    render_guide_page_html,
    render_guides_hub_html,
)


logger = structlog.get_logger(__name__)

router = APIRouter(prefix='/seo', tags=['Cabinet SEO'])

_HTML_HEADERS = {
    'Cache-Control': 'public, max-age=60, stale-while-revalidate=300',
    'Vary': 'Accept',
}


@router.get('/guides', response_class=HTMLResponse)
async def render_guides_hub_seo(
    db: AsyncSession = Depends(get_cabinet_db),
) -> Response:
    try:
        hub, categories, pages = await build_public_hub(db)
        if not categories:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Guides hub is not configured')
        html = render_guides_hub_html(hub, categories, pages)
        return HTMLResponse(content=html, headers=_HTML_HEADERS)
    except FileNotFoundError as error:
        logger.error('Cabinet static shell missing for SEO render', error=str(error))
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail='Cabinet static files are not available. Ensure /srv/cabinet is mounted in the bot container.',
        ) from error
    except HTTPException:
        raise
    except Exception:
        logger.exception('Failed to render guides hub SEO HTML')
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail='Failed to render guides hub',
        )


@router.get('/page/{slug}', response_class=HTMLResponse)
async def render_guide_page_seo(
    slug: str = Path(..., max_length=200, pattern=r'^[a-z0-9\-]+$'),
    db: AsyncSession = Depends(get_cabinet_db),
) -> Response:
    page = await get_guide_page_by_slug(db, slug)
    if not page or not page.is_active:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Guide page not found')

    content = (page.content or {}).get('ru') or next(iter((page.content or {}).values()), '')
    if not str(content).strip():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Guide page has no content')

    try:
        html = render_guide_page_html(page)
        return HTMLResponse(content=html, headers=_HTML_HEADERS)
    except FileNotFoundError as error:
        logger.error('Cabinet static shell missing for SEO render', slug=slug, error=str(error))
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail='Cabinet static files are not available. Ensure /srv/cabinet is mounted in the bot container.',
        ) from error
    except Exception:
        logger.exception('Failed to render guide page SEO HTML', slug=slug)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail='Failed to render guide page',
        )


@router.post('/reload-template', include_in_schema=False)
async def reload_seo_template() -> dict[str, str]:
    bust_template_cache()
    return {'status': 'ok'}

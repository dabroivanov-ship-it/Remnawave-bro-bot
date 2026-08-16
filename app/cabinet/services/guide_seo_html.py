"""Build crawlable HTML for marketing guide pages from DB (no frontend rebuild)."""

from __future__ import annotations

import html
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.config import settings
from app.database.models import GuideCategory, GuideHubSettings, GuidePage

_META_ATTR_RE = re.compile(
    r'<meta\s+(name|property)=["\']([^"\']+)["\']\s+content=["\'][^"\']*["\']\s*/?>',
    re.IGNORECASE,
)
_CANONICAL_RE = re.compile(r'<link\s+rel=["\']canonical["\'][^>]*>', re.IGNORECASE)
_TITLE_RE = re.compile(r'<title>[^<]*</title>', re.IGNORECASE)
_ROOT_BLOCK_RE = re.compile(
    r'<div id="root">\s*<div id="prerender"[\s\S]*?</div>\s*</div>',
    re.IGNORECASE,
)
_SEO_STATIC_RE = re.compile(r'<div id="seo-static"[\s\S]*?</div>', re.IGNORECASE)


def _escape(value: str) -> str:
    return html.escape(str(value), quote=True)


def resolve_locale_text(data: dict[str, str] | None, locale: str = 'ru', fallback: str = '') -> str:
    if not data:
        return fallback
    return data.get(locale) or data.get('ru') or data.get('en') or next(iter(data.values()), fallback)


def get_site_origin() -> str:
    for candidate in (settings.CABINET_URL, settings.WEBHOOK_URL):
        raw = (candidate or '').strip()
        if not raw or 'example.com' in raw:
            continue
        if raw.startswith('http://') or raw.startswith('https://'):
            from urllib.parse import urlparse

            parsed = urlparse(raw)
            if parsed.scheme and parsed.netloc:
                return f'{parsed.scheme}://{parsed.netloc}'
    return 'https://example.com'


def canonical_url(path: str = '/') -> str:
    origin = get_site_origin()
    normalized = path if path.startswith('/') else f'/{path}'
    if normalized == '/':
        return f'{origin}/'
    return f'{origin}{normalized.rstrip("/")}'


@lru_cache(maxsize=4)
def _cached_index_template(path_str: str, mtime_ns: int) -> str:
    del mtime_ns  # bust cache when file changes
    return Path(path_str).read_text(encoding='utf-8')


def _index_template_candidates() -> list[Path]:
    configured = settings.get_cabinet_static_path() / 'index.html'
    seen: set[str] = set()
    candidates: list[Path] = []
    for path in (configured, Path('/srv/cabinet/index.html')):
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        candidates.append(path)
    return candidates


def load_spa_shell_template() -> str:
    errors: list[str] = []
    for index_path in _index_template_candidates():
        if not index_path.is_file():
            errors.append(f'missing: {index_path}')
            continue
        try:
            stat = index_path.stat()
            return _cached_index_template(str(index_path), stat.st_mtime_ns)
        except OSError as error:
            errors.append(f'unreadable {index_path}: {error}')

    bust_template_cache()
    raise FileNotFoundError(
        'Cabinet index.html not found for SEO render. '
        f'Checked: {"; ".join(errors)}. '
        'Mount /srv/cabinet into the bot container (see docker-compose.yml).'
    )


def _replace_meta(html_doc: str, attr: str, key: str, content: str) -> str:
    tag = f'<meta {attr}="{key}" content="{_escape(content)}" />'
    pattern = re.compile(
        rf'<meta\s+{attr}=["\']{re.escape(key)}["\']\s+content=["\'][^"\']*["\']\s*/?>',
        re.IGNORECASE,
    )
    if pattern.search(html_doc):
        return pattern.sub(tag, html_doc, count=1)
    return html_doc.replace('</head>', f'    {tag}\n  </head>', 1)


def apply_seo_head(html_doc: str, *, title: str, description: str, path: str) -> str:
    url = canonical_url(path)
    out = _TITLE_RE.sub(f'<title>{_escape(title)}</title>', html_doc, count=1)
    out = _replace_meta(out, 'name', 'description', description)
    out = _replace_meta(out, 'name', 'robots', 'index, follow')
    out = _replace_meta(out, 'property', 'og:title', title)
    out = _replace_meta(out, 'property', 'og:description', description)
    out = _replace_meta(out, 'property', 'og:url', url)
    out = _replace_meta(out, 'name', 'twitter:title', title)
    out = _replace_meta(out, 'name', 'twitter:description', description)
    if _CANONICAL_RE.search(out):
        out = _CANONICAL_RE.sub(f'<link rel="canonical" href="{_escape(url)}" />', out, count=1)
    else:
        out = out.replace('</head>', f'    <link rel="canonical" href="{_escape(url)}" />\n  </head>', 1)
    return out


def inject_seo_body(html_doc: str, inner_html: str) -> str:
    cleaned = _ROOT_BLOCK_RE.sub('<div id="root"></div>', html_doc)
    seo_block = f'<div id="seo-static" class="seo-static-fallback" aria-hidden="true">{inner_html}</div>'
    if 'id="seo-static"' in cleaned:
        return _SEO_STATIC_RE.sub(seo_block, cleaned, count=1)
    return cleaned.replace('<div id="root"></div>', f'<div id="root"></div>\n    {seo_block}', 1)


def render_seo_document(*, title: str, description: str, path: str, body_html: str) -> str:
    shell = load_spa_shell_template()
    with_head = apply_seo_head(shell, title=title, description=description, path=path)
    return inject_seo_body(with_head, body_html)


def render_guide_page_html(page: GuidePage, locale: str = 'ru') -> str:
    title = resolve_locale_text(page.meta_title, locale) or resolve_locale_text(page.title, locale, page.slug)
    description = resolve_locale_text(page.meta_description, locale)
    content = resolve_locale_text(page.content, locale)
    body = (
        f'<main class="mh-prose" style="max-width:56rem;margin:0 auto;padding:1.5rem;color:#e5e7eb">'
        f'{content}</main>'
    )
    return render_seo_document(title=title, description=description, path=f'/{page.slug}', body_html=body)


def _hub_category_card(
    category: GuideCategory,
    pages: list[GuidePage],
    locale: str,
) -> str:
    cat_title = _escape(resolve_locale_text(category.title, locale))
    cat_desc = _escape(resolve_locale_text(category.description, locale))
    featured = next((p for p in pages if p.is_featured), None)
    regular = [p for p in pages if not p.is_featured]

    parts = [
        '<div class="glass-card p-10 rounded-[2rem]">',
        f'<h3 class="text-2xl font-black mb-4">{cat_title}</h3>',
        f'<p class="text-gray-400 text-sm mb-4">{cat_desc}</p>',
    ]
    if featured:
        label = _escape(resolve_locale_text(featured.link_label, locale) or resolve_locale_text(featured.title, locale))
        parts.append(
            f'<a class="block text-sky-400 hover:underline font-bold mb-6" href="/{featured.slug}">{label}</a>'
        )
    parts.append('<div class="space-y-3 text-sm">')
    for page in regular:
        label = _escape(resolve_locale_text(page.link_label, locale) or resolve_locale_text(page.title, locale))
        parts.append(f'<a class="block text-sky-400 hover:underline" href="/{page.slug}">{label}</a>')
    parts.append('</div>')
    footer = resolve_locale_text(category.footer_html, locale)
    if footer:
        parts.append(f'<p class="text-gray-500 text-xs leading-relaxed mt-6 mb-0 pt-4 border-t border-white/10">{footer}</p>')
    parts.append('</div>')
    return '\n'.join(parts)


def render_guides_hub_html(
    hub: GuideHubSettings,
    categories: list[GuideCategory],
    pages: list[GuidePage],
    locale: str = 'ru',
) -> str:
    title = resolve_locale_text(hub.meta_title, locale) or 'Инструкции | VPN BRO'
    description = resolve_locale_text(hub.meta_description, locale)
    kicker = _escape(resolve_locale_text(hub.kicker, locale))
    hub_title = _escape(resolve_locale_text(hub.title, locale))
    hub_highlight = _escape(resolve_locale_text(hub.title_highlight, locale))
    hub_desc = _escape(resolve_locale_text(hub.description, locale))

    pages_by_category: dict[int, list[GuidePage]] = {}
    for page in pages:
        if page.category_id is None:
            continue
        pages_by_category.setdefault(page.category_id, []).append(page)

    cards = ''.join(
        _hub_category_card(category, pages_by_category.get(category.id, []), locale)
        for category in categories
    )

    body = f'''<main class="mh-prose" style="max-width:56rem;margin:0 auto;padding:1.5rem;color:#e5e7eb">
<section class="hero hero--center" style="padding-top:56px; padding-bottom:34px;">
  <div class="container hero__grid">
    <div>
      <div class="hero__kicker"><span aria-hidden="true">📚</span><span>{kicker}</span></div>
      <h1 class="hero__title">{hub_title} <em>{hub_highlight}</em></h1>
      <p class="hero__lead">{hub_desc}</p>
    </div>
  </div>
</section>
<section class="max-w-7xl mx-auto px-6 pb-32">
<div class="grid grid-cols-1 md:grid-cols-3 gap-8">{cards}</div>
</section>
</main>'''

    return render_seo_document(title=title, description=description, path='/guides', body_html=body)


def bust_template_cache() -> None:
    _cached_index_template.cache_clear()

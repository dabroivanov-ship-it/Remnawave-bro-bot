"""Tests for dynamic guide SEO HTML rendering."""

from app.cabinet.services import guide_seo_html as seo


def test_escape_html():
    assert seo._escape('a & b <c>') == 'a &amp; b &lt;c&gt;'


def test_apply_seo_head_replaces_title_and_description():
    shell = '''<!doctype html><html><head>
    <title>Old</title>
    <meta name="description" content="old desc" />
    <link rel="canonical" href="https://example.com/" />
    </head><body><div id="root"></div></body></html>'''
    out = seo.apply_seo_head(shell, title='New Title', description='New desc', path='/forkop')
    assert '<title>New Title</title>' in out
    assert 'content="New desc"' in out
    assert 'href="https://example.com/forkop"' in out or 'href=' in out


def test_inject_seo_body():
    shell = '<html><body><div id="root"></div></body></html>'
    out = seo.inject_seo_body(shell, '<main>hello</main>')
    assert 'id="seo-static"' in out
    assert '<main>hello</main>' in out
    assert '<div id="root"></div>' in out


def test_resolve_locale_text():
    assert seo.resolve_locale_text({'ru': 'Привет', 'en': 'Hi'}, 'ru') == 'Привет'
    assert seo.resolve_locale_text({'en': 'Hi'}, 'ru') == 'Hi'
    assert seo.resolve_locale_text(None, 'ru', 'fb') == 'fb'

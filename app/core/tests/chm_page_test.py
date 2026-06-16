from pathlib import Path

from app.api.chm import _rewrite_html


def test_rewrite_html_inserts_chinese_query_highlight_without_escape_error(tmp_path):
    page = tmp_path / 'page.htm'
    html = '<html><head></head><body><p>火球术</p></body></html>'

    rewritten = _rewrite_html(html, page, '火球术')

    assert r'\u706b\u7403\u672f' in rewritten
    assert 'chm-search-highlight' in rewritten
    assert rewritten.endswith('</body></html>')


def test_rewrite_html_keeps_backslashes_in_query(tmp_path):
    rewritten = _rewrite_html('<html><body>test</body></html>', Path(tmp_path / 'page.htm'), r'\user')

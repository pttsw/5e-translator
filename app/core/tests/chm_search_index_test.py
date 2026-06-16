import os
from pathlib import Path

from app.core.chm_search_index import ChmSearchIndex


def _write_page(path: Path, content: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f'<html><body><h1>{path.stem}</h1><p>{content}</p></body></html>',
        encoding='utf-8'
    )


def test_searches_chinese_and_english_content(tmp_path):
    root = tmp_path / 'chm'
    index_path = tmp_path / 'index.sqlite3'
    _write_page(root / '法术' / '火球术.htm', '造成火焰伤害。 Fire damage.')
    _write_page(root / '怪物' / 'dragon.htm', '一条强大的巨龙。')

    index = ChmSearchIndex(root, index_path)
    result = index.sync(force=True)
    assert result['updated'] == 2

    title_matches, content_matches = index.search('火球术')
    assert title_matches[0]['path'] == '法术/火球术.htm'
    assert content_matches[0]['path'] == '法术/火球术.htm'

    _, english_matches = index.search('fire damage')
    assert english_matches[0]['path'] == '法术/火球术.htm'


def test_incrementally_updates_and_deletes_pages(tmp_path):
    root = tmp_path / 'chm'
    index_path = tmp_path / 'index.sqlite3'
    page = root / '旧页面.htm'
    _write_page(page, '旧内容')

    index = ChmSearchIndex(root, index_path)
    index.sync(force=True)
    assert index.search('旧内容')[1]

    _write_page(page, '新的内容')
    stat = page.stat()
    os.utime(page, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1))
    result = index.sync(force=True)
    assert result['updated'] == 1
    assert not index.search('旧内容')[1]
    assert index.search('新的内容')[1]

    page.unlink()
    result = index.sync(force=True)

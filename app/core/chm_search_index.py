import fcntl
import os
import re
import sqlite3
import threading
import time
from pathlib import Path

from bs4 import BeautifulSoup


HTML_SUFFIXES = {'.htm', '.html'}
DEFAULT_INDEX_PATH = Path(__file__).resolve().parents[2] / 'data' / 'chm_search.sqlite3'
SYNC_INTERVAL_SECONDS = 300
_INDEX_LOCK = threading.Lock()
_LAST_SYNC_AT = {}


def _decode_text(file_path: Path) -> str:
    raw = file_path.read_bytes()
    for encoding in ('gb18030', 'utf-8', 'gbk'):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode('utf-8', errors='ignore')


def _html_to_text(html: str) -> str:
    soup = BeautifulSoup(html, 'lxml')
    for node in soup(['script', 'style', 'noscript']):
        node.decompose()
    return re.sub(r'\s+', ' ', soup.get_text(' ', strip=True)).strip()


def _search_tokens(text: str) -> str:
    normalized = re.sub(r'\s+', ' ', (text or '').casefold()).strip()
    if not normalized:
        return ''

    chars = [f'u{ord(char):x}' for char in normalized]
    pairs = [
        f'b{ord(normalized[index]):x}x{ord(normalized[index + 1]):x}'
        for index in range(len(normalized) - 1)
    ]
    return ' '.join(dict.fromkeys(chars + pairs))


def _match_expression(query: str) -> str:
    normalized = re.sub(r'\s+', ' ', (query or '').casefold()).strip()
    if not normalized:
        return ''
    if len(normalized) == 1:
        return f'u{ord(normalized):x}'
    return ' AND '.join(
        dict.fromkeys(
            f'b{ord(normalized[index]):x}x{ord(normalized[index + 1]):x}'
            for index in range(len(normalized) - 1)
        )
    )


def _like_pattern(query: str) -> str:
    escaped = query.replace('!', '!!').replace('%', '!%').replace('_', '!_')
    return f'%{escaped}%'


def _snippet(content: str, query: str, before: int = 50, after: int = 130) -> str:
    index = content.casefold().find(query.casefold())
    if index < 0:
        return content[:before + after]
    start = max(0, index - before)
    end = min(len(content), index + len(query) + after)
    prefix = '...' if start else ''
    suffix = '...' if end < len(content) else ''
    return f'{prefix}{content[start:end]}{suffix}'


class ChmSearchIndex:
    def __init__(self, root: Path, index_path: Path = None):
        self.root = root.resolve()
        configured_path = os.getenv('CHM_SEARCH_INDEX_PATH')
        self.index_path = Path(configured_path) if configured_path else (index_path or DEFAULT_INDEX_PATH)
        self.index_path = self.index_path.resolve()
        self.lock_path = Path(f'{self.index_path}.lock')

    def _connect(self):
        self.index_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(str(self.index_path), timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute('PRAGMA journal_mode=WAL')
        connection.execute('PRAGMA synchronous=NORMAL')
        connection.execute('PRAGMA temp_store=MEMORY')
        return connection

    @staticmethod
    def _ensure_schema(connection):
        connection.execute(
            '''
            CREATE TABLE IF NOT EXISTS chm_index_files (
                path TEXT PRIMARY KEY,
                document_rowid INTEGER NOT NULL UNIQUE,
                mtime_ns INTEGER NOT NULL,
                size INTEGER NOT NULL
            )
            '''
        )
        connection.execute(
            '''
            CREATE VIRTUAL TABLE IF NOT EXISTS chm_documents USING fts5(
                path UNINDEXED,
                title UNINDEXED,
                content UNINDEXED,
                title_terms,
                content_terms
            )
            '''
        )
        connection.execute(
            '''
            CREATE TABLE IF NOT EXISTS chm_index_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            '''
        )

    def _iter_files(self):
        for file_path in self.root.rglob('*'):
            if file_path.is_file() and file_path.suffix.lower() in HTML_SUFFIXES:
                yield file_path

    def sync(self, force: bool = False, rebuild: bool = False):
        cache_key = str(self.index_path)
        now = time.monotonic()
        if (
            not force
            and not rebuild
            and self.index_path.exists()
            and now - _LAST_SYNC_AT.get(cache_key, 0) < SYNC_INTERVAL_SECONDS
        ):
            return {'updated': 0, 'deleted': 0, 'skipped': True}

        with _INDEX_LOCK:
            now = time.monotonic()
            if (
                not force
                and not rebuild
                and self.index_path.exists()
                and now - _LAST_SYNC_AT.get(cache_key, 0) < SYNC_INTERVAL_SECONDS
            ):
                return {'updated': 0, 'deleted': 0, 'skipped': True}

            self.index_path.parent.mkdir(parents=True, exist_ok=True)
            with self.lock_path.open('a+') as lock_file:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
                try:
                    result = self._sync_locked(rebuild)
                    _LAST_SYNC_AT[cache_key] = time.monotonic()
                    return result
                finally:
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)

    def _sync_locked(self, rebuild: bool):
        updated = 0
        with self._connect() as connection:
            self._ensure_schema(connection)
            existing = {
                row['path']: row
                for row in connection.execute(
                    'SELECT path, document_rowid, mtime_ns, size FROM chm_index_files'
                )
            }
            seen_paths = set()

            if rebuild:
                connection.execute('DELETE FROM chm_documents')
                connection.execute('DELETE FROM chm_index_files')
                existing = {}

            next_rowid = connection.execute(
                'SELECT COALESCE(MAX(document_rowid), 0) + 1 FROM chm_index_files'
            ).fetchone()[0]

            for file_path in self._iter_files():
                rel_path = file_path.relative_to(self.root).as_posix()
                seen_paths.add(rel_path)
                stat = file_path.stat()
                previous = existing.get(rel_path)
                if (
                    previous
                    and previous['mtime_ns'] == stat.st_mtime_ns
                    and previous['size'] == stat.st_size
                ):
                    continue

                document_rowid = previous['document_rowid'] if previous else next_rowid
                if not previous:
                    next_rowid += 1
                else:
                    connection.execute(
                        'DELETE FROM chm_documents WHERE rowid = ?',
                        (document_rowid,)
                    )

                title = file_path.stem
                content = _html_to_text(_decode_text(file_path))
                connection.execute(
                    '''
                    INSERT INTO chm_documents(
                        rowid, path, title, content, title_terms, content_terms
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    ''',
                    (
                        document_rowid,
                        rel_path,
                        title,
                        content,
                        _search_tokens(f'{title} {rel_path}'),
                        _search_tokens(content)
                    )
                )
                connection.execute(
                    '''
                    INSERT INTO chm_index_files(path, document_rowid, mtime_ns, size)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(path) DO UPDATE SET
                        document_rowid = excluded.document_rowid,
                        mtime_ns = excluded.mtime_ns,
                        size = excluded.size
                    ''',
                    (rel_path, document_rowid, stat.st_mtime_ns, stat.st_size)
                )
                updated += 1
                if updated % 100 == 0:
                    connection.commit()

            deleted_paths = set(existing) - seen_paths
            for rel_path in deleted_paths:
                document_rowid = existing[rel_path]['document_rowid']
                connection.execute(
                    'DELETE FROM chm_documents WHERE rowid = ?',
                    (document_rowid,)
                )
                connection.execute(
                    'DELETE FROM chm_index_files WHERE path = ?',
                    (rel_path,)
                )

            connection.execute(
                '''
                INSERT INTO chm_index_meta(key, value) VALUES ('last_sync_at', ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                ''',
                (str(int(time.time())),)
            )
            connection.commit()
        return {'updated': updated, 'deleted': len(deleted_paths), 'skipped': False}

    def search(self, query: str, title_limit: int = 30, content_limit: int = 60):
        self.sync()
        expression = _match_expression(query)
        if not expression:
            return [], []

        like_query = _like_pattern(query)
        with self._connect() as connection:
            title_rows = connection.execute(
                '''
                SELECT path, title
                FROM chm_documents
                WHERE title_terms MATCH ?
                  AND (
                    title LIKE ? COLLATE NOCASE ESCAPE '!'
                    OR path LIKE ? COLLATE NOCASE ESCAPE '!'
                  )
                LIMIT ?
                ''',
                (expression, like_query, like_query, title_limit)
            ).fetchall()
            content_rows = connection.execute(
                '''
                SELECT path, title, content
                FROM chm_documents
                WHERE content_terms MATCH ?
                  AND content LIKE ? COLLATE NOCASE ESCAPE '!'
                LIMIT ?
                ''',
                (expression, like_query, content_limit)
            ).fetchall()

        title_matches = [
            {'path': row['path'], 'title': row['title']}
            for row in title_rows
        ]
        content_matches = [
            {
                'path': row['path'],
                'title': row['title'],
                'snippet': _snippet(row['content'], query)
            }
            for row in content_rows
        ]
        return title_matches, content_matches

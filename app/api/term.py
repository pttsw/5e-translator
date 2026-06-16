import hashlib
import json
import os
import tempfile
from pathlib import Path

from flask import send_file
from flask_login import login_required
from flask_restful import Api, Resource, request
from sqlalchemy import inspect, text

from app.model import db
from .restful_utils import success

api = Api()

TERM_EXPORT_DIR = Path(tempfile.gettempdir()) / '5e-translator'
TERM_EXPORT_PATH = TERM_EXPORT_DIR / 'terms.json'
TERM_EXPORT_META_PATH = TERM_EXPORT_DIR / 'terms.meta.json'


def _get_term_export_columns():
    available_columns = {
        column['name'] for column in inspect(db.engine).get_columns('term')
    }
    optional_columns = [
        column for column in (
            'source', 'note', 'modified_at', 'modified_by',
            'modified_reson', 'to_be_discussed'
        )
        if column in available_columns
    ]
    return ['en', 'cn', 'category', *optional_columns]


def _serialize_term_rows(rows, columns):
    items = []
    for row in rows:
        item = {column: row.get(column) for column in columns}
        for column in (
            'source', 'note', 'modified_at', 'modified_by',
            'modified_reson', 'to_be_discussed'
        ):
            item.setdefault(column, None)
        if item['modified_at'] is not None:
            item['modified_at'] = str(item['modified_at'])
        items.append(item)
    return items


def _get_database_term_state():
    row = db.session.execute(
        text(
            "SELECT COUNT(*) AS `count`, "
            "MAX(`modified_at`) AS `max_modified_at` "
            "FROM `term`"
        )
    ).mappings().one()
    max_modified_at = row['max_modified_at']
    return {
        'count': int(row['count'] or 0),
        'max_modified_at': (
            str(max_modified_at) if max_modified_at is not None else None
        ),
    }


def _read_term_export_metadata():
    try:
        metadata = json.loads(
            TERM_EXPORT_META_PATH.read_text(encoding='utf-8')
        )
    except (OSError, ValueError, TypeError):
        return None
    required_keys = {'version', 'count', 'max_modified_at'}
    return metadata if required_keys.issubset(metadata) else None


def _write_term_export(items, database_state):
    terms_json = json.dumps(
        items,
        ensure_ascii=False,
        separators=(',', ':'),
    ).encode('utf-8')
    version = hashlib.sha256(terms_json).hexdigest()
    payload = json.dumps({
        'version': version,
        'count': len(items),
        'items': items,
    }, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    TERM_EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode='wb',
        dir=TERM_EXPORT_DIR,
        prefix='terms-',
        suffix='.tmp',
        delete=False,
    ) as temporary_file:
        temporary_file.write(payload)
        temporary_path = temporary_file.name
    os.replace(temporary_path, TERM_EXPORT_PATH)
    metadata = {
        'version': version,
        'count': len(items),
        'max_modified_at': database_state['max_modified_at'],
    }
    with tempfile.NamedTemporaryFile(
        mode='w',
        encoding='utf-8',
        dir=TERM_EXPORT_DIR,
        prefix='terms-meta-',
        suffix='.tmp',
        delete=False,
    ) as temporary_file:
        json.dump(metadata, temporary_file, separators=(',', ':'))
        temporary_meta_path = temporary_file.name
    os.replace(temporary_meta_path, TERM_EXPORT_META_PATH)
    return metadata


def _build_term_export(database_state):
    select_columns = _get_term_export_columns()
    rows = db.session.execute(
        text(
            f"SELECT {', '.join(f'`{column}`' for column in select_columns)} "
            "FROM term "
            "ORDER BY CHAR_LENGTH(en) DESC, en ASC, category ASC"
        )
    ).mappings().all()
    items = _serialize_term_rows(rows, select_columns)
    return _write_term_export(items, database_state)


def _ensure_term_export():
    database_state = _get_database_term_state()
    metadata = None
    if TERM_EXPORT_PATH.is_file() and TERM_EXPORT_META_PATH.is_file():
        metadata = _read_term_export_metadata()
    if (
        metadata is not None and
        metadata['count'] == database_state['count'] and
        metadata['max_modified_at'] == database_state['max_modified_at']
    ):
        return metadata, False
    return _build_term_export(database_state), True


@api.resource('/terms')
class TermApi(Resource):
    @login_required
    def get(self):
        page = max(request.args.get('page', 1, int), 1)
        limit = min(max(request.args.get('limit', 100, int), 1), 500)
        en = request.args.get('en', '').strip()
        cn = request.args.get('cn', '').strip()
        category = request.args.get('category', '').strip()
        source = request.args.get('source', '').strip()
        to_be_discussed = request.args.get('to_be_discussed', None, int)

        select_columns = _get_term_export_columns()
        available_columns = set(select_columns)
        where_clauses = []
        params = {}

        for field, value in (
            ('en', en),
            ('cn', cn),
            ('category', category),
            ('source', source),
        ):
            if value and field in available_columns:
                where_clauses.append(f"`{field}` LIKE :{field}")
                params[field] = f'%{value}%'
        if to_be_discussed is not None and 'to_be_discussed' in available_columns:
            where_clauses.append('`to_be_discussed` = :to_be_discussed')
            params['to_be_discussed'] = to_be_discussed

        where_sql = f" WHERE {' AND '.join(where_clauses)}" if where_clauses else ''
        params.update(limit=limit, offset=(page - 1) * limit)

        count = db.session.execute(
            text(f'SELECT COUNT(*) FROM term{where_sql}'),
            params
        ).scalar()
        rows = db.session.execute(
            text(
                f"SELECT {', '.join(f'`{column}`' for column in select_columns)} "
                f"FROM term{where_sql} "
                "ORDER BY CHAR_LENGTH(en) ASC, en ASC, category ASC "
                "LIMIT :limit OFFSET :offset"
            ),
            params
        ).mappings().all()

        return success(data={
            'count': int(count or 0),
            'items': _serialize_term_rows(rows, select_columns),
        })


@api.resource('/terms/export')
class TermExportApi(Resource):
    @login_required
    def get(self):
        metadata, rebuilt = _ensure_term_export()
        response = send_file(
            str(TERM_EXPORT_PATH),
            mimetype='application/octet-stream',
            as_attachment=True,
            download_name='terms.json',
            conditional=False,
        )
        response.headers['X-Term-Version'] = metadata['version']
        response.headers['X-Term-Count'] = str(metadata['count'])
        response.headers['X-Term-Rebuilt'] = '1' if rebuilt else '0'
        response.headers['Cache-Control'] = 'no-store'
        return response


@api.resource('/terms/export/meta')
class TermExportMetaApi(Resource):
    @login_required
    def get(self):
        metadata, rebuilt = _ensure_term_export()
        return success(data={
            **metadata,
            'rebuilt': rebuilt,
        })

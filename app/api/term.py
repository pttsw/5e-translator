import csv
import hashlib
import io
import json
import os
import re
import tempfile
from pathlib import Path

from flask import send_file
from flask_login import current_user, login_required
from flask_restful import Api, Resource, request
from sqlalchemy import bindparam, inspect, text

from app.model import UserModel, db
from .restful_utils import error, success

api = Api()

TERM_EXPORT_DIR = Path(tempfile.gettempdir()) / '5e-translator'
TERM_EXPORT_PATH = TERM_EXPORT_DIR / 'terms.json'
TERM_EXPORT_META_PATH = TERM_EXPORT_DIR / 'terms.meta.json'
SOURCE_SPLIT_PATTERN = re.compile(r'[,，\n]+')
TERM_TABLE_EXPORT_COLUMNS = [
    ('en', '英文术语'),
    ('cn', '中文译名'),
    ('category', '分类编码'),
    ('category_label', '分类'),
    ('source', '来源缩写'),
    ('source_label', '来源'),
    ('note', '备注'),
    ('to_be_discussed_label', '状态'),
    ('modified_reson', '修改说明'),
    ('modified_at', '修改时间'),
    ('modified_by', '最后编辑人ID'),
]


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


def _serialize_term_rows(
        rows,
        columns,
        include_category_label=False,
        include_source_label=False,
        book_map=None):
    items = []
    for row in rows:
        item = {column: row.get(column) for column in columns}
        for column in (
            'source', 'note', 'modified_at', 'modified_by',
            'modified_reson', 'to_be_discussed'
        ):
            item.setdefault(column, None)
        if include_category_label:
            item['category_label'] = (
                row.get('category_label')
                or row.get('category')
                or ''
            )
        if include_source_label:
            item['source_label'] = _source_label(item.get('source'), book_map or {})
        item['modified_by_nickname'] = row.get('modified_by_nickname') or row.get('modified_by_username')
        if item['modified_at'] is not None:
            item['modified_at'] = str(item['modified_at'])
        items.append(item)
    return items


def _is_admin_user():
    user_id = _current_user_id()
    if user_id is None:
        return False
    user = UserModel.query.get(user_id)
    return bool(user and user.roles == 'admin')


def _current_user_id():
    try:
        return int(current_user.get_id())
    except (TypeError, ValueError):
        return None


def _term_identity(term):
    book_map = _get_book_map()
    return {
        'en': term.get('en'),
        'cn': term.get('cn'),
        'category': term.get('category'),
        'category_label': term.get('category_label') or term.get('category'),
        'source': term.get('source'),
        'source_label': _source_label(term.get('source'), book_map),
        'note': term.get('note'),
        'modified_reson': term.get('modified_reson'),
        'to_be_discussed': term.get('to_be_discussed'),
        'modified_by': term.get('modified_by'),
        'modified_by_nickname': (
            term.get('modified_by_nickname')
            or term.get('modified_by_username')
            or str(term.get('modified_by') or '')
        ),
    }


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


def _clean_text(value):
    return str(value or '').strip()


def _clean_source_codes(value):
    if isinstance(value, list):
        raw_values = value
    else:
        raw_values = SOURCE_SPLIT_PATTERN.split(str(value or ''))
    codes = []
    seen = set()
    for raw_value in raw_values:
        code = _clean_text(raw_value)
        if not code or code in seen:
            continue
        seen.add(code)
        codes.append(code)
    return codes


def _source_storage_value(value):
    return ','.join(_clean_source_codes(value))


def _get_book_map():
    try:
        rows = db.session.execute(
            text("SELECT abbr, cn FROM book ORDER BY cn ASC, abbr ASC")
        ).mappings().all()
    except Exception:
        return {}
    return {row['abbr']: row['cn'] for row in rows}


def _get_category_map():
    try:
        rows = db.session.execute(
            text("SELECT code, name FROM term_category")
        ).mappings().all()
    except Exception:
        return {}
    return {row['code']: row['name'] for row in rows}


def _source_label(source, book_map):
    labels = []
    for code in _clean_source_codes(source):
        cn = book_map.get(code)
        labels.append(f"{cn}（{code}）" if cn else code)
    return '、'.join(labels)


def _resolve_source_storage_value(value):
    book_map = _get_book_map()
    cn_to_abbr = {cn: abbr for abbr, cn in book_map.items()}
    codes = []
    seen = set()
    for code in _clean_source_codes(value):
        resolved = code if code in book_map else cn_to_abbr.get(code, code)
        if not resolved or resolved in seen:
            continue
        seen.add(resolved)
        codes.append(resolved)
    return ','.join(codes)


def _source_codes_exist(codes):
    if not codes:
        return True
    rows = db.session.execute(
        text("SELECT abbr FROM book WHERE abbr IN :codes").bindparams(
            bindparam('codes', expanding=True)
        ),
        {'codes': tuple(codes)}
    ).scalars().all()
    return len(set(rows)) == len(set(codes))


def _read_cached_term_export_items():
    payload = json.loads(TERM_EXPORT_PATH.read_text(encoding='utf-8'))
    return payload.get('items') or []


def _serialize_table_export_item(item, category_map, book_map):
    category = item.get('category') or ''
    to_be_discussed = item.get('to_be_discussed')
    return {
        **item,
        'category_label': category_map.get(category) or category,
        'source_label': _source_label(item.get('source'), book_map),
        'to_be_discussed_label': '待讨论' if to_be_discussed else '已确定',
    }


def _validate_required_text(data, fields):
    labels = {
        'en': '英文术语',
        'cn': '中文译名',
        'category': '分类',
        'source': '来源',
        'modified_reson': '修改说明',
        'code': '分类编码',
        'name': '分类名称',
        'abbr': '来源缩写',
    }
    cleaned = {field: _clean_text(data.get(field)) for field in fields}
    missing = [labels.get(field, field) for field, value in cleaned.items() if not value]
    if missing:
        return None, f"{'、'.join(missing)}不能为空"
    return cleaned, None


def _load_term(en, category):
    return db.session.execute(
        text(
            "SELECT term.en, term.cn, term.category, term.source, term.note, "
            "term.modified_reson, term.modified_by, term.to_be_discussed, "
            "term_category.name AS category_label, "
            "user.nickname AS modified_by_nickname, "
            "user.username AS modified_by_username "
            "FROM term LEFT JOIN user ON user.id = term.modified_by "
            "LEFT JOIN term_category "
            "ON BINARY term_category.code = BINARY term.category "
            "WHERE BINARY term.en = BINARY :en "
            "AND BINARY term.category = BINARY :category"
        ),
        {'en': en, 'category': category}
    ).mappings().first()


def _term_exists(en, category):
    return bool(db.session.execute(
        text(
            "SELECT 1 FROM term "
            "WHERE BINARY en = BINARY :en "
            "AND BINARY category = BINARY :category LIMIT 1"
        ),
        {'en': en, 'category': category}
    ).scalar())


def _category_exists(code):
    return bool(db.session.execute(
        text(
            "SELECT 1 FROM term_category "
            "WHERE BINARY code = BINARY :code LIMIT 1"
        ),
        {'code': code}
    ).scalar())


def _category_usage_count(code):
    return int(db.session.execute(
        text(
            "SELECT COUNT(*) FROM term "
            "WHERE BINARY category = BINARY :code"
        ),
        {'code': code}
    ).scalar() or 0)


def _book_exists(abbr):
    return bool(db.session.execute(
        text(
            "SELECT 1 FROM book "
            "WHERE BINARY abbr = BINARY :abbr LIMIT 1"
        ),
        {'abbr': abbr}
    ).scalar())


def _book_usage_count(abbr):
    return int(db.session.execute(
        text(
            "SELECT COUNT(*) FROM term "
            "WHERE FIND_IN_SET("
            ":abbr, "
            "REPLACE(REPLACE(REPLACE(source, '，', ','), ', ', ','), ' ,', ',')"
            ") > 0"
        ),
        {'abbr': abbr}
    ).scalar() or 0)


def _serialize_import_failure(row_number, incoming, reason, db_term=None):
    book_map = _get_book_map()
    item = {
        'row_number': row_number,
        'reason': reason,
        'import_en': incoming.get('en') or '',
        'import_cn': incoming.get('cn') or '',
        'import_category': incoming.get('category') or '',
        'import_source': incoming.get('source') or '',
        'import_source_label': _source_label(incoming.get('source'), book_map),
        'import_note': incoming.get('note') or '',
        'db_en': '',
        'db_cn': '',
        'db_category': '',
        'db_source': '',
        'db_source_label': '',
        'db_note': '',
    }
    if db_term is not None:
        item.update({
            'db_en': db_term.get('en') or '',
            'db_cn': db_term.get('cn') or '',
            'db_category': db_term.get('category') or '',
            'db_source': db_term.get('source') or '',
            'db_source_label': _source_label(db_term.get('source'), book_map),
            'db_note': db_term.get('note') or '',
        })
    return item


@api.resource('/terms')
class TermApi(Resource):
    @login_required
    def get(self):
        page = max(request.args.get('page', 1, int), 1)
        limit = min(max(request.args.get('limit', 100, int), 1), 500)
        en = request.args.get('en', '').strip()
        cn = request.args.get('cn', '').strip()
        eq_en = request.args.get('eq_en', '').strip()
        eq_cn = request.args.get('eq_cn', '').strip()
        category = request.args.get('category', '').strip()
        source = request.args.get('source', '').strip()
        to_be_discussed = request.args.get('to_be_discussed', None, int)
        sort = request.args.get('sort', '+en')

        select_columns = _get_term_export_columns()
        available_columns = set(select_columns)
        where_clauses = []
        params = {}

        for field, value in (
            ('en', en),
            ('cn', cn),
        ):
            if value and field in available_columns:
                where_clauses.append(f"LOWER(`{field}`) LIKE :{field}")
                params[field] = f'%{value.lower()}%'
        for field, value in (
            ('category', category),
        ):
            if value and field in available_columns:
                where_clauses.append(f"`{field}` LIKE :{field}")
                params[field] = f'%{value}%'
        source_codes = _clean_source_codes(source)
        if source_codes and 'source' in available_columns:
            source_filters = []
            for index, source_code in enumerate(source_codes):
                param_key = f'source_{index}'
                source_filters.append(
                    "FIND_IN_SET("
                    f":{param_key}, "
                    "REPLACE(REPLACE(REPLACE(`source`, '，', ','), ', ', ','), ' ,', ',')"
                    ") > 0"
                )
                params[param_key] = source_code
            where_clauses.append(f"({' OR '.join(source_filters)})")
        for field, value in (
            ('en', eq_en),
            ('cn', eq_cn),
        ):
            if value and field in available_columns:
                param_key = f'eq_{field}'
                where_clauses.append(f"`{field}` = :{param_key}")
                params[param_key] = value
        if to_be_discussed is not None and 'to_be_discussed' in available_columns:
            where_clauses.append('`to_be_discussed` = :to_be_discussed')
            params['to_be_discussed'] = to_be_discussed

        where_sql = f" WHERE {' AND '.join(where_clauses)}" if where_clauses else ''
        sort_columns = {
            'en': 'term.en',
            'cn': 'term.cn',
            'category': 'term.category',
            'category_label': 'term_category.name',
            'source': 'term.source',
            'note': 'term.note',
            'to_be_discussed': 'term.to_be_discussed',
            'modified_reson': 'term.modified_reson',
            'modified_at': 'term.modified_at',
            'modified_by': 'term.modified_by',
            'modified_by_nickname': 'user.nickname',
        }
        sort_direction = 'ASC'
        sort_key = 'en'
        if sort:
            sort_direction = 'DESC' if sort.startswith('-') else 'ASC'
            sort_key = sort[1:] if sort[0] in ('+', '-') else sort
        sort_expression = sort_columns.get(sort_key, sort_columns['en'])
        if sort_key == 'en':
            order_sql = (
                f"ORDER BY CHAR_LENGTH(term.en) {sort_direction}, "
                f"term.en {sort_direction}, term.category ASC"
            )
        else:
            order_sql = (
                f"ORDER BY {sort_expression} {sort_direction}, "
                "term.en ASC, term.category ASC"
            )
        params.update(limit=limit, offset=(page - 1) * limit)

        count = db.session.execute(
            text(f'SELECT COUNT(*) FROM term{where_sql}'),
            params
        ).scalar()
        rows = db.session.execute(
            text(
                f"SELECT {', '.join(f'term.`{column}`' for column in select_columns)}, "
                "term_category.name AS category_label, "
                "user.nickname AS modified_by_nickname, "
                "user.username AS modified_by_username "
                "FROM term LEFT JOIN user ON user.id = term.modified_by "
                "LEFT JOIN term_category "
                "ON BINARY term_category.code = BINARY term.category"
                f"{where_sql} "
                f"{order_sql} "
                "LIMIT :limit OFFSET :offset"
            ),
            params
        ).mappings().all()
        book_map = _get_book_map()

        return success(data={
            'count': int(count or 0),
            'items': _serialize_term_rows(
                rows,
                select_columns,
                include_category_label=True,
                include_source_label=True,
                book_map=book_map
            ),
        })

    @login_required
    def post(self):
        if not request.is_json:
            return error("请求体必须是JSON")
        params = request.get_json() or {}
        values, validation_error = _validate_required_text(
            params,
            ['en', 'cn', 'category']
        )
        if validation_error:
            return error(validation_error)
        values['source'] = _resolve_source_storage_value(params.get('source'))
        if not _source_codes_exist(_clean_source_codes(values['source'])):
            return error("新增失败：来源不存在")
        if not _category_exists(values['category']):
            return error("新增失败：分类不存在")
        if _term_exists(values['en'], values['category']):
            return error("新增失败：术语已存在")

        note = _clean_text(params.get('note'))
        modified_reson = _clean_text(params.get('modified_reson')) or '手动新增'
        to_be_discussed = 1 if params.get('to_be_discussed') else 0
        try:
            db.session.execute(
                text(
                    "INSERT INTO term "
                    "(en, cn, category, source, note, modified_reson, "
                    "modified_by, to_be_discussed) "
                    "VALUES (:en, :cn, :category, :source, :note, "
                    ":modified_reson, :modified_by, :to_be_discussed)"
                ),
                {
                    **values,
                    'note': note,
                    'modified_reson': modified_reson,
                    'modified_by': _current_user_id(),
                    'to_be_discussed': to_be_discussed,
                }
            )
            db.session.commit()
        except Exception as exc:
            db.session.rollback()
            return error(f"新增失败: {exc}")

        term = _load_term(values['en'], values['category'])
        return success(message="新增成功", data=_term_identity(term))

    @login_required
    def put(self):
        if not request.is_json:
            return error("请求体必须是JSON")
        params = request.get_json() or {}
        original_en = _clean_text(params.get('original_en') or params.get('en'))
        if 'original_category' not in params:
            return error("编辑失败：缺少原始术语定位信息")
        original_category = str(params.get('original_category') or '')
        if not original_en:
            return error("编辑失败：缺少原始术语定位信息")

        values, validation_error = _validate_required_text(
            params,
            ['cn', 'category', 'modified_reson']
        )
        if validation_error:
            return error(validation_error)
        values['source'] = _resolve_source_storage_value(params.get('source'))
        if not _source_codes_exist(_clean_source_codes(values['source'])):
            return error("编辑失败：来源不存在")
        if not _category_exists(values['category']):
            return error("编辑失败：分类不存在")

        current_user_id = _current_user_id()
        is_admin = _is_admin_user()
        term = _load_term(original_en, original_category)
        if term is None:
            return error("编辑失败：术语不存在")
        owner_id = term.get('modified_by')
        is_owner = owner_id is not None and int(owner_id) == current_user_id
        if not is_admin and not is_owner:
            return error("普通用户仅可修改最后编辑人为自己的术语")

        target_category = values['category']
        if target_category != original_category and _term_exists(original_en, target_category):
            return error("编辑失败：术语已存在")

        try:
            db.session.execute(
                text(
                    "UPDATE term SET "
                    "cn = :cn, category = :target_category, source = :source, "
                    "note = :note, modified_reson = :modified_reson, "
                    "modified_by = :modified_by, to_be_discussed = :to_be_discussed "
                    "WHERE BINARY en = BINARY :original_en "
                    "AND BINARY category = BINARY :original_category"
                ),
                {
                    'cn': values['cn'],
                    'target_category': target_category,
                    'source': values['source'],
                    'note': _clean_text(params.get('note')),
                    'modified_reson': values['modified_reson'],
                    'modified_by': current_user_id,
                    'to_be_discussed': 1 if params.get('to_be_discussed') else 0,
                    'original_en': original_en,
                    'original_category': original_category,
                }
            )
            db.session.commit()
        except Exception as exc:
            db.session.rollback()
            return error(f"编辑失败: {exc}")

        updated_term = _load_term(original_en, target_category)
        return success(message="编辑成功", data=_term_identity(updated_term))

    @login_required
    def delete(self):
        if not request.is_json:
            return error("请求体必须是JSON")
        params = request.get_json() or {}
        items = params.get('items') or []
        confirm_non_owner = bool(params.get('confirm_non_owner'))
        if not isinstance(items, list) or not items:
            return error("请选择要删除的术语")

        current_user_id = _current_user_id()
        is_admin = _is_admin_user()
        keys = []
        seen = set()
        for item in items:
            en = (item.get('en') or '').strip() if isinstance(item, dict) else ''
            category = (item.get('category') or '').strip() if isinstance(item, dict) else ''
            key = (en, category)
            if not en or key in seen:
                continue
            seen.add(key)
            keys.append(key)
        if not keys:
            return error("请选择有效的术语")

        deleted = []
        failed = []
        try:
            for en, category in keys:
                term = db.session.execute(
                    text(
                        "SELECT term.en, term.cn, term.category, term.modified_by, "
                        "user.nickname AS modified_by_nickname, "
                        "user.username AS modified_by_username "
                        "FROM term LEFT JOIN user ON user.id = term.modified_by "
                        "WHERE BINARY term.en = BINARY :en "
                        "AND BINARY term.category = BINARY :category"
                    ),
                    {'en': en, 'category': category}
                ).mappings().first()
                if term is None:
                    failed.append({
                        'en': en,
                        'category': category,
                        'reason': '术语不存在',
                    })
                    continue

                owner_id = term.get('modified_by')
                is_owner = owner_id is not None and int(owner_id) == current_user_id
                if not is_admin and not is_owner:
                    failed.append({
                        **_term_identity(term),
                        'reason': '只能删除最后编辑人为自己的术语',
                    })
                    continue
                if is_admin and not is_owner and not confirm_non_owner:
                    failed.append({
                        **_term_identity(term),
                        'reason': '管理员删除他人编辑的术语需要二次确认',
                        'requires_confirmation': True,
                    })
                    continue

                db.session.execute(
                    text(
                        "DELETE FROM term "
                        "WHERE BINARY en = BINARY :en "
                        "AND BINARY category = BINARY :category"
                    ),
                    {'en': en, 'category': category}
                )
                deleted.append(_term_identity(term))
            db.session.commit()
        except Exception as exc:
            db.session.rollback()
            return error(f"删除失败: {exc}")

        return success(data={
            'deleted': deleted,
            'failed': failed,
            'deleted_count': len(deleted),
            'failed_count': len(failed),
        })


@api.resource('/terms/import')
class TermImportApi(Resource):
    @login_required
    def post(self):
        if not request.is_json:
            return error("请求体必须是JSON")
        params = request.get_json() or {}
        items = params.get('items') or []
        if not isinstance(items, list) or not items:
            return error("导入失败：没有可导入的数据")

        current_user_id = _current_user_id()
        success_count = 0
        existing_count = 0
        failed = []

        try:
            for index, raw_item in enumerate(items, start=1):
                item = raw_item if isinstance(raw_item, dict) else {}
                incoming = {
                    'en': _clean_text(item.get('en')),
                    'cn': _clean_text(item.get('cn')),
                    'category': _clean_text(item.get('category')),
                    'source': _resolve_source_storage_value(item.get('source')),
                    'note': _clean_text(item.get('note')),
                    'to_be_discussed': 1 if item.get('to_be_discussed') else 0,
                }
                row_number = item.get('_row_number') or index + 1
                if not incoming['en'] or not incoming['cn']:
                    failed.append(_serialize_import_failure(
                        row_number,
                        incoming,
                        '英文术语或中文译名为空'
                    ))
                    continue
                if not incoming['category']:
                    failed.append(_serialize_import_failure(
                        row_number,
                        incoming,
                        '分类为空'
                    ))
                    continue
                if not _category_exists(incoming['category']):
                    failed.append(_serialize_import_failure(
                        row_number,
                        incoming,
                        '分类不存在'
                    ))
                    continue
                if incoming['source'] and not _source_codes_exist(_clean_source_codes(incoming['source'])):
                    failed.append(_serialize_import_failure(
                        row_number,
                        incoming,
                        '来源不存在'
                    ))
                    continue

                existing = _load_term(incoming['en'], incoming['category'])
                if existing is not None:
                    if (existing.get('cn') or '') == incoming['cn']:
                        existing_count += 1
                    else:
                        failed.append(_serialize_import_failure(
                            row_number,
                            incoming,
                            '译名与数据库中的不一致',
                            existing
                        ))
                    continue

                db.session.execute(
                    text(
                        "INSERT INTO term "
                        "(en, cn, category, source, note, modified_reson, "
                        "modified_by, to_be_discussed) "
                        "VALUES (:en, :cn, :category, :source, :note, "
                        ":modified_reson, :modified_by, :to_be_discussed)"
                    ),
                    {
                        **incoming,
                        'modified_reson': '批量导入术语',
                        'modified_by': current_user_id,
                    }
                )
                success_count += 1
            db.session.commit()
        except Exception as exc:
            db.session.rollback()
            return error(f"导入失败: {exc}")

        return success(data={
            'total': len(items),
            'success_count': success_count,
            'existing_count': existing_count,
            'failed_count': len(failed),
            'failed': failed,
        })


@api.resource('/term-categories')
class TermCategoryApi(Resource):
    @login_required
    def get(self):
        rows = db.session.execute(
            text(
                "SELECT term_category.code, term_category.name, "
                "term_category.sort_order, term_category.created_at, "
                "term_category.updated_at, term_category.created_by, "
                "term_category.modified_by, "
                "COUNT(term.en) AS usage_count "
                "FROM term_category "
                "LEFT JOIN term "
                "ON BINARY term.category = BINARY term_category.code "
                "GROUP BY term_category.code, term_category.name, "
                "term_category.sort_order, term_category.created_at, "
                "term_category.updated_at, term_category.created_by, "
                "term_category.modified_by "
                "ORDER BY term_category.sort_order ASC, "
                "term_category.name ASC, term_category.code ASC"
            )
        ).mappings().all()
        items = []
        for row in rows:
            item = dict(row)
            item['usage_count'] = int(item.get('usage_count') or 0)
            if item.get('created_at') is not None:
                item['created_at'] = str(item['created_at'])
            if item.get('updated_at') is not None:
                item['updated_at'] = str(item['updated_at'])
            items.append(item)
        return success(data={'items': items})

    @login_required
    def post(self):
        if not _is_admin_user():
            return error("仅管理员可新增分类")
        if not request.is_json:
            return error("请求体必须是JSON")
        params = request.get_json() or {}
        values, validation_error = _validate_required_text(
            params,
            ['code', 'name']
        )
        if validation_error:
            return error(validation_error)
        sort_order = params.get('sort_order')
        try:
            sort_order = int(sort_order) if sort_order not in (None, '') else 0
        except (TypeError, ValueError):
            return error("排序必须是数字")
        if _category_exists(values['code']):
            return error("新增失败：分类已存在")
        try:
            db.session.execute(
                text(
                    "INSERT INTO term_category "
                    "(`code`, `name`, `sort_order`, `created_by`, `modified_by`) "
                    "VALUES (:code, :name, :sort_order, :user_id, :user_id)"
                ),
                {
                    **values,
                    'sort_order': sort_order,
                    'user_id': _current_user_id(),
                }
            )
            db.session.commit()
        except Exception as exc:
            db.session.rollback()
            return error(f"新增失败: {exc}")
        return success(message="新增成功")


@api.resource('/books')
class BookApi(Resource):
    @login_required
    def get(self):
        rows = db.session.execute(
            text(
                "SELECT abbr, en, cn, category, "
                "(SELECT COUNT(*) FROM term "
                "WHERE FIND_IN_SET("
                "book.abbr, "
                "REPLACE(REPLACE(REPLACE(term.source, '，', ','), ', ', ','), ' ,', ',')"
                ") > 0) AS usage_count "
                "FROM book "
                "ORDER BY category ASC, cn ASC, abbr ASC"
            )
        ).mappings().all()
        items = []
        for row in rows:
            item = dict(row)
            item['usage_count'] = int(item.get('usage_count') or 0)
            items.append(item)
        return success(data={
            'items': items
        })

    @login_required
    def post(self):
        if not _is_admin_user():
            return error("仅管理员可新增来源")
        if not request.is_json:
            return error("请求体必须是JSON")
        params = request.get_json() or {}
        values, validation_error = _validate_required_text(
            params,
            ['abbr', 'cn', 'category']
        )
        if validation_error:
            return error(validation_error)
        if _book_exists(values['abbr']):
            return error("新增失败：来源已存在")
        try:
            db.session.execute(
                text(
                    "INSERT INTO book (abbr, en, cn, category) "
                    "VALUES (:abbr, :en, :cn, :category)"
                ),
                {
                    **values,
                    'en': _clean_text(params.get('en')) or None,
                }
            )
            db.session.commit()
        except Exception as exc:
            db.session.rollback()
            return error(f"新增失败: {exc}")
        return success(message="新增成功")


@api.resource('/books/<string:abbr>')
class BookItemApi(Resource):
    @login_required
    def put(self, abbr):
        if not _is_admin_user():
            return error("仅管理员可编辑来源")
        if not request.is_json:
            return error("请求体必须是JSON")
        book_abbr = _clean_text(abbr)
        if not _book_exists(book_abbr):
            return error("编辑失败：来源不存在")
        params = request.get_json() or {}
        values, validation_error = _validate_required_text(
            params,
            ['cn', 'category']
        )
        if validation_error:
            return error(validation_error)
        try:
            db.session.execute(
                text(
                    "UPDATE book SET en = :en, cn = :cn, category = :category "
                    "WHERE BINARY abbr = BINARY :abbr"
                ),
                {
                    **values,
                    'en': _clean_text(params.get('en')) or None,
                    'abbr': book_abbr,
                }
            )
            db.session.commit()
        except Exception as exc:
            db.session.rollback()
            return error(f"编辑失败: {exc}")
        return success(message="编辑成功")

    @login_required
    def delete(self, abbr):
        if not _is_admin_user():
            return error("仅管理员可删除来源")
        book_abbr = _clean_text(abbr)
        if not _book_exists(book_abbr):
            return error("删除失败：来源不存在")
        usage_count = _book_usage_count(book_abbr)
        if usage_count > 0:
            return error(f"删除失败：该来源正在被 {usage_count} 条术语使用")
        try:
            db.session.execute(
                text(
                    "DELETE FROM book "
                    "WHERE BINARY abbr = BINARY :abbr"
                ),
                {'abbr': book_abbr}
            )
            db.session.commit()
        except Exception as exc:
            db.session.rollback()
            return error(f"删除失败: {exc}")
        return success(message="删除成功")


@api.resource('/term-categories/<string:code>')
class TermCategoryItemApi(Resource):
    @login_required
    def put(self, code):
        if not _is_admin_user():
            return error("仅管理员可编辑分类")
        if not request.is_json:
            return error("请求体必须是JSON")
        category_code = _clean_text(code)
        if not _category_exists(category_code):
            return error("编辑失败：分类不存在")
        params = request.get_json() or {}
        values, validation_error = _validate_required_text(
            params,
            ['name']
        )
        if validation_error:
            return error(validation_error)
        sort_order = params.get('sort_order')
        try:
            sort_order = int(sort_order) if sort_order not in (None, '') else 0
        except (TypeError, ValueError):
            return error("排序必须是数字")
        try:
            db.session.execute(
                text(
                    "UPDATE term_category SET "
                    "`name` = :name, `sort_order` = :sort_order, "
                    "`modified_by` = :modified_by "
                    "WHERE BINARY `code` = BINARY :code"
                ),
                {
                    'name': values['name'],
                    'sort_order': sort_order,
                    'modified_by': _current_user_id(),
                    'code': category_code,
                }
            )
            db.session.commit()
        except Exception as exc:
            db.session.rollback()
            return error(f"编辑失败: {exc}")
        return success(message="编辑成功")

    @login_required
    def delete(self, code):
        if not _is_admin_user():
            return error("仅管理员可删除分类")
        category_code = _clean_text(code)
        if not _category_exists(category_code):
            return error("删除失败：分类不存在")
        usage_count = _category_usage_count(category_code)
        if usage_count > 0:
            return error(f"删除失败：该分类正在被 {usage_count} 条术语使用")
        try:
            db.session.execute(
                text(
                    "DELETE FROM term_category "
                    "WHERE BINARY `code` = BINARY :code"
                ),
                {'code': category_code}
            )
            db.session.commit()
        except Exception as exc:
            db.session.rollback()
            return error(f"删除失败: {exc}")
        return success(message="删除成功")


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


@api.resource('/terms/export/table')
class TermTableExportApi(Resource):
    @login_required
    def get(self):
        export_format = request.args.get('format', 'json').strip().lower()
        if export_format not in ('json', 'csv'):
            return error("不支持的导出格式")

        metadata, rebuilt = _ensure_term_export()
        category_map = _get_category_map()
        book_map = _get_book_map()
        items = [
            _serialize_table_export_item(item, category_map, book_map)
            for item in _read_cached_term_export_items()
        ]

        if export_format == 'json':
            payload = json.dumps({
                **metadata,
                'rebuilt': rebuilt,
                'items': items,
            }, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
            response = send_file(
                io.BytesIO(payload),
                mimetype='application/json; charset=utf-8',
                as_attachment=True,
                download_name='terms-table.json',
                conditional=False,
            )
        else:
            stream = io.StringIO()
            stream.write('\ufeff')
            writer = csv.writer(stream)
            writer.writerow([label for _, label in TERM_TABLE_EXPORT_COLUMNS])
            for item in items:
                writer.writerow([
                    item.get(field) if item.get(field) is not None else ''
                    for field, _ in TERM_TABLE_EXPORT_COLUMNS
                ])
            response = send_file(
                io.BytesIO(stream.getvalue().encode('utf-8')),
                mimetype='text/csv; charset=utf-8',
                as_attachment=True,
                download_name='terms-table.csv',
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

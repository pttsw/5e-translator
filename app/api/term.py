from flask_login import login_required
from flask_restful import Api, Resource, request
from sqlalchemy import inspect, text

from app.model import db
from .restful_utils import success

api = Api()


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
        select_columns = ['en', 'cn', 'category', *optional_columns]
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

        items = []
        for row in rows:
            item = {column: row.get(column) for column in select_columns}
            for column in (
                'source', 'note', 'modified_at', 'modified_by',
                'modified_reson', 'to_be_discussed'
            ):
                item.setdefault(column, None)
            if item['modified_at'] is not None:
                item['modified_at'] = str(item['modified_at'])
            items.append(item)

        return success(data={
            'count': int(count or 0),
            'items': items,
        })

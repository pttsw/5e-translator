# api_dashboard.py
from datetime import datetime, timedelta

from flask_restful import Resource, Api
from flask_login import current_user, login_required
from sqlalchemy import case, func

from .restful_utils import success
from app.model import FileModule, ProofreadModel, UserModel, WordsModel, session

api = Api()


def _build_daily_series(day_count=7):
    today = datetime.utcnow().date()
    dates = [today - timedelta(days=offset) for offset in range(day_count - 1, -1, -1)]
    labels = [day.strftime('%m-%d') for day in dates]
    return dates, labels


def _serialize_rank_rows(rows, current_user_id, current_user_info):
    result = []
    for index, row in enumerate(rows, start=1):
        accepted_count = int(row.accepted_count or 0)
        total_count = int(row.proofread_count or 0)
        result.append({
            'rank': index,
            'user_id': row.user_id,
            'username': row.nickname or row.username or f'User {row.user_id}',
            'proofread_count': total_count,
            'accepted_count': accepted_count,
            'acceptance_rate': round((accepted_count / total_count) * 100, 1) if total_count else 0,
            'is_current_user': str(row.user_id) == str(current_user_id),
            'is_supplemental': False,
        })

    top_rows = result[:10]
    if not any(row['is_current_user'] for row in top_rows):
        current_row = next(
            (row for row in result if row['is_current_user']),
            None
        )
        if current_row is None:
            current_row = {
                'rank': len(result) + 1,
                'user_id': current_user_id,
                'username': (
                    current_user_info.nickname
                    or current_user_info.username
                    or f'User {current_user_id}'
                ),
                'proofread_count': 0,
                'accepted_count': 0,
                'acceptance_rate': 0,
                'is_current_user': True,
                'is_supplemental': True,
            }
        else:
            current_row = {**current_row, 'is_supplemental': True}
        top_rows.append(current_row)
    return top_rows


def _build_trend(rows, labels):
    row_map = {
        _normalize_day_label(row.day): {
            'proofread_count': int(row.proofread_count or 0),
            'accepted_count': int(row.accepted_count or 0),
        }
        for row in rows
    }
    return {
        'labels': labels,
        'proofread': [row_map.get(label, {}).get('proofread_count', 0) for label in labels],
        'accepted': [row_map.get(label, {}).get('accepted_count', 0) for label in labels],
    }


def _normalize_day_label(day_value):
    if hasattr(day_value, 'strftime'):
        return day_value.strftime('%m-%d')
    return str(day_value)[5:10]


@api.resource('/dashboard/summary')
class DashboardSummaryApi(Resource):
    @login_required
    def get(self):
        current_user_id = int(current_user.get_id())
        current_user_info = UserModel.query.get(current_user_id)
        file_overview = session.query(
            func.count(FileModule.file).label('file_count'),
            func.coalesce(func.sum(FileModule.total), 0).label('total'),
            func.coalesce(func.sum(FileModule.translate), 0).label('translate'),
            func.coalesce(func.sum(FileModule.proofread), 0).label('proofread'),
            func.coalesce(func.sum(case((FileModule.locked == 1, 1), else_=0)), 0).label('locked_files'),
        ).one()
        word_count = session.query(func.count(WordsModel.id)).scalar() or 0
        my_translate_count = session.query(func.count(WordsModel.id)).filter(
            WordsModel.modified_by == current_user_id
        ).scalar() or 0
        my_claim_count = session.query(func.count(FileModule.file)).filter(
            FileModule.user_id == current_user_id
        ).scalar() or 0

        total = int(file_overview.total or 0)
        translate = int(file_overview.translate or 0)
        proofread = int(file_overview.proofread or 0)

        overview = {
            'file_count': int(file_overview.file_count or 0),
            'word_count': int(word_count),
            'locked_files': int(file_overview.locked_files or 0),
            'total': total,
            'translate': translate,
            'proofread': proofread,
            'translate_rate': round((translate / total) * 100, 1) if total else 0,
            'proofread_rate': round((proofread / total) * 100, 1) if total else 0,
            'my_translate_count': int(my_translate_count),
            'my_claim_count': int(my_claim_count),
        }

        status_distribution = [
            {'name': '未翻译', 'value': max(total - translate, 0)},
            {'name': '已翻译未校对', 'value': max(translate - proofread, 0)},
            {'name': '已校对', 'value': proofread},
        ]

        series_dates, series_labels = _build_daily_series(7)
        series_start = datetime.combine(series_dates[0], datetime.min.time())

        trend_query = session.query(
            func.date(ProofreadModel.modified_at).label('day'),
            func.count(ProofreadModel.id).label('proofread_count'),
            func.coalesce(func.sum(case((ProofreadModel.accepted == 1, 1), else_=0)), 0).label('accepted_count'),
        ).filter(
            ProofreadModel.modified_at >= series_start
        )
        proofread_rows = trend_query.group_by(
            func.date(ProofreadModel.modified_at)
        ).all()
        my_proofread_rows = trend_query.filter(
            ProofreadModel.modified_by == current_user_id
        ).group_by(
            func.date(ProofreadModel.modified_at)
        ).all()
        proofread_trends_7d = {
            'mine': _build_trend(my_proofread_rows, series_labels),
            'site': _build_trend(proofread_rows, series_labels),
        }

        rank_query = session.query(
            ProofreadModel.modified_by.label('user_id'),
            UserModel.username.label('username'),
            UserModel.nickname.label('nickname'),
            func.count(ProofreadModel.id).label('proofread_count'),
            func.coalesce(func.sum(case((ProofreadModel.accepted == 1, 1), else_=0)), 0).label('accepted_count'),
        ).outerjoin(
            UserModel, UserModel.id == ProofreadModel.modified_by
        ).group_by(
            ProofreadModel.modified_by,
            UserModel.username,
            UserModel.nickname
        ).order_by(
            func.count(ProofreadModel.id).desc(),
            func.coalesce(func.sum(case((ProofreadModel.accepted == 1, 1), else_=0)), 0).desc(),
            ProofreadModel.modified_by.asc()
        )
        total_rank_rows = rank_query.all()

        rank_7d_rows = rank_query.filter(
            ProofreadModel.modified_at >= series_start
        ).all()

        return success(data={
            'overview': overview,
            'status_distribution': status_distribution,
            'proofread_trends_7d': proofread_trends_7d,
            'user_rank_total': _serialize_rank_rows(
                total_rank_rows,
                current_user_id,
                current_user_info
            ),
            'user_rank_7d': _serialize_rank_rows(
                rank_7d_rows,
                current_user_id,
                current_user_info
            ),
        })

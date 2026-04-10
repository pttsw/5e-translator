# api_dashboard.py
from datetime import datetime, timedelta

from flask_restful import Resource, Api
from flask_login import login_required
from sqlalchemy import case, func

from .restful_utils import success
from app.model import FileModule, ProofreadModel, UserModel, WordsModel, session

api = Api()


def _build_daily_series(day_count=7):
    today = datetime.utcnow().date()
    dates = [today - timedelta(days=offset) for offset in range(day_count - 1, -1, -1)]
    labels = [day.strftime('%m-%d') for day in dates]
    return dates, labels


def _serialize_rank_rows(rows):
    result = []
    for index, row in enumerate(rows, start=1):
        accepted_count = int(row.accepted_count or 0)
        total_count = int(row.proofread_count or 0)
        result.append({
            'rank': index,
            'user_id': row.user_id,
            'username': row.username or f'User {row.user_id}',
            'proofread_count': total_count,
            'accepted_count': accepted_count,
            'acceptance_rate': round((accepted_count / total_count) * 100, 1) if total_count else 0,
        })
    return result


def _normalize_day_label(day_value):
    if hasattr(day_value, 'strftime'):
        return day_value.strftime('%m-%d')
    return str(day_value)[5:10]


@api.resource('/dashboard/summary')
class DashboardSummaryApi(Resource):
    @login_required
    def get(self):
        file_overview = session.query(
            func.count(FileModule.file).label('file_count'),
            func.coalesce(func.sum(FileModule.total), 0).label('total'),
            func.coalesce(func.sum(FileModule.translate), 0).label('translate'),
            func.coalesce(func.sum(FileModule.proofread), 0).label('proofread'),
            func.coalesce(func.sum(case((FileModule.locked == 1, 1), else_=0)), 0).label('locked_files'),
        ).one()
        word_count = session.query(func.count(WordsModel.id)).scalar() or 0

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
        }

        status_distribution = [
            {'name': '未翻译', 'value': max(total - translate, 0)},
            {'name': '已翻译未校对', 'value': max(translate - proofread, 0)},
            {'name': '已校对', 'value': proofread},
        ]

        series_dates, series_labels = _build_daily_series(7)
        series_start = datetime.combine(series_dates[0], datetime.min.time())

        proofread_rows = session.query(
            func.date(ProofreadModel.modified_at).label('day'),
            func.count(ProofreadModel.id).label('proofread_count'),
            func.coalesce(func.sum(case((ProofreadModel.accepted == 1, 1), else_=0)), 0).label('accepted_count'),
        ).filter(
            ProofreadModel.modified_at >= series_start
        ).group_by(
            func.date(ProofreadModel.modified_at)
        ).all()

        proofread_map = {
            _normalize_day_label(row.day): {
                'proofread_count': int(row.proofread_count or 0),
                'accepted_count': int(row.accepted_count or 0),
            }
            for row in proofread_rows
        }
        proofread_trend_7d = {
            'labels': series_labels,
            'proofread': [proofread_map.get(label, {}).get('proofread_count', 0) for label in series_labels],
            'accepted': [proofread_map.get(label, {}).get('accepted_count', 0) for label in series_labels],
        }

        total_rank_rows = session.query(
            ProofreadModel.modified_by.label('user_id'),
            UserModel.username.label('username'),
            func.count(ProofreadModel.id).label('proofread_count'),
            func.coalesce(func.sum(case((ProofreadModel.accepted == 1, 1), else_=0)), 0).label('accepted_count'),
        ).outerjoin(
            UserModel, UserModel.id == ProofreadModel.modified_by
        ).group_by(
            ProofreadModel.modified_by,
            UserModel.username
        ).order_by(
            func.count(ProofreadModel.id).desc(),
            func.coalesce(func.sum(case((ProofreadModel.accepted == 1, 1), else_=0)), 0).desc()
        ).limit(10).all()

        rank_7d_rows = session.query(
            ProofreadModel.modified_by.label('user_id'),
            UserModel.username.label('username'),
            func.count(ProofreadModel.id).label('proofread_count'),
            func.coalesce(func.sum(case((ProofreadModel.accepted == 1, 1), else_=0)), 0).label('accepted_count'),
        ).outerjoin(
            UserModel, UserModel.id == ProofreadModel.modified_by
        ).filter(
            ProofreadModel.modified_at >= series_start
        ).group_by(
            ProofreadModel.modified_by,
            UserModel.username
        ).order_by(
            func.count(ProofreadModel.id).desc(),
            func.coalesce(func.sum(case((ProofreadModel.accepted == 1, 1), else_=0)), 0).desc()
        ).limit(10).all()

        return success(data={
            'overview': overview,
            'status_distribution': status_distribution,
            'proofread_trend_7d': proofread_trend_7d,
            'user_rank_total': _serialize_rank_rows(total_rank_rows),
            'user_rank_7d': _serialize_rank_rows(rank_7d_rows),
        })

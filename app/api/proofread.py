# api_foo.py
from flask_restful import Resource, Api, request
from .restful_utils import *
from app.model import FileModule, ProofreadModel,WordsModel, session, SourceModel
from .base import BaseApi
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from sqlalchemy import text
from sqlalchemy.sql import func
from app.core.file_progress_service import clear_file_stale, mark_files_stale, refresh_progress_by_files, update_job_cache_word

import json

api = Api()


def _get_file_progress(file_path: str):
    if not file_path:
        return {}
    file_row = FileModule.query.filter_by(file=file_path).first()
    if file_row is None:
        return {}
    return {
        file_path: {
            'total': int(file_row.total or 0),
            'translate': int(file_row.translate or 0),
            'proofread': int(file_row.proofread or 0),
        }
    }

@api.resource('/proofread')
class ProofreadApi(Resource, BaseApi):
    model = ProofreadModel
    @login_required
    def get(self):
        return super().get()
     
    @login_required
    def put(self):
        return error('禁止更新')
        params = request.get_json()
        if params['file'] is None or params['word_id'] is None:
            return error("更新失败：file和word_id不能为空")
        item = self.model.query.filter(getattr(self.model, 'file') == params['file'])\
            .filter(getattr(self.model, 'word_id') == params['word_id']).first()
        if item is None:
            return error(f"更新失败：没有找到相关条目")
        if item.update(commit=True,  updateFunc=self._update,**params) is None:
            return error(f"更新失败：数据库更新失败")
        return success(message=f"Item has been updated successfully.", data = item.to_dict())

    @login_required
    def post(self):
        if not current_user.is_authenticated:
            return error('请先登录！')
        user_id = current_user.get_id()
        if request.is_json:
            data = request.get_json()
            current_file = data.pop('current_file', '').strip('/')
            data['modified_by'] = user_id
            data['accepted'] = 0
            # if ProofreadModel.query.filter_by(word_id = data['word_id']).filter_by(cn = data['cn']).first():
            #     return error("请勿重复提交已存在的翻译！")
            
            item = self.model.create(commit=True, **data, createFunc=self._create)
            if item is None:
                return error("新增失败：数据库新增失败")
            
            word = WordsModel.query.get(item.word_id)
            if word.update(commit=True, is_key = True) is None:
                return error("新增失败：数据库变更失败")
            related_files = [
                row[0] for row in session.query(SourceModel.file)
                .filter(SourceModel.word_id == item.word_id)
                .distinct()
                .all()
            ]
            if current_file:
                update_job_cache_word(current_file, item.word_id, is_key=1, is_proofread=0, cn_str=item.cn)
                clear_file_stale(current_file)
                progress = _get_file_progress(current_file)
            else:
                progress = {}
            stale_files = [file_path for file_path in related_files if file_path and file_path != current_file]
            mark_files_stale(stale_files)
            return success(message=f"新增成功", data={
                'proofread': item.to_dict(),
                'progress': progress,
                'stale_files': stale_files,
            })
        else:
            return error("The request payload is not in JSON format")
    def _create(self, words):
        if words is None:
            raise Exception("words is none")

    
    def _delete(self, words):
        if words is None:
            raise Exception("words is none")

    
    def _update(self,words):
        if words is None:
            raise Exception("words is none")            
        
@api.resource('/accepted')
class AcceptedApi(Resource, BaseApi):
    @login_required
    def post(self):
        if not current_user.is_authenticated:
            return error("请先登录！")
        if current_user.roles != 'admin':
            return error("无权限！")
        params = request.get_json()
        current_file = params.get('current_file', '').strip('/') if request.is_json else ''
        if 'id' in params.keys():
            proofread = ProofreadModel.query.get(params['id'])
            if proofread is None:
                return error("采纳失败：记录不存在")
            try:
                procedure = text("CALL accept(:proofreadId, :modifiedBy)")
                result = session.execute(procedure, {
                    'proofreadId': params['id'],
                    'modifiedBy': current_user.get_id()
                })
                session.commit()
            except Exception as e:
                session.rollback()
                print(f"存储过程执行失败: {e}")
                return error("采纳失败")
            related_files = [
                row[0] for row in session.query(SourceModel.file)
                .filter(SourceModel.word_id == proofread.word_id)
                .distinct()
                .all()
            ]
            if current_file:
                update_job_cache_word(current_file, proofread.word_id, is_key=1, is_proofread=1, cn_str=proofread.cn)
                clear_file_stale(current_file)
                progress = _get_file_progress(current_file)
            else:
                progress = {}
            stale_files = [file_path for file_path in related_files if file_path and file_path != current_file]
            mark_files_stale(stale_files)
            return success(data={'progress': progress, 'stale_files': stale_files})
        elif 'file' in params.keys():
            wids = SourceModel.query.filter(SourceModel.file == params['file']).with_entities(SourceModel.word_id).distinct()
            pids = ProofreadModel.query.group_by(ProofreadModel.word_id
                                                 ).having(ProofreadModel.word_id.in_(wids)
                                                 ).having(func.max(ProofreadModel.accepted) == 0
                                                 ).with_entities(func.max(ProofreadModel.id)
                                                 ).all()
            # result = ProofreadModel.list_to_dict(pids)
            cid = current_user.get_id()
            for pid in pids:
                try:
                    procedure = text("CALL accept(:proofreadId, :modifiedBy)")
                    result = session.execute(procedure, {
                        'proofreadId': pid[0],
                        'modifiedBy': cid
                    })
                    session.commit()
                except Exception as e:
                    session.rollback()
                    print(f"存储过程执行失败: {e}")
                    return error("采纳失败")
            for pid in pids:
                proofread = ProofreadModel.query.get(pid[0])
                if proofread is not None:
                    update_job_cache_word(params['file'], proofread.word_id, is_key=1, is_proofread=1, cn_str=proofread.cn)
            clear_file_stale(params['file'])
            progress = _get_file_progress(params['file'])
            affected_files = [
                row[0] for row in session.query(SourceModel.file)
                .filter(SourceModel.word_id.in_(wids))
                .distinct()
                .all()
            ]
            stale_files = [file_path for file_path in affected_files if file_path and file_path != params['file']]
            mark_files_stale(stale_files)
            return success(data={'accepted_count': len(pids), 'progress': progress, 'stale_files': stale_files})
        return success()

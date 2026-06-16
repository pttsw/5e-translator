# api_foo.py
from flask_restful import Resource, Api, request
from flask import current_app
from .restful_utils import *
from app.model import FileModule, UserModel, db, session
from .base import BaseApi
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from sqlalchemy import text
from sqlalchemy.sql import func

import json

api = Api()


def _escape_like(value):
    return value.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')


def _claim_query(file_name, is_folder):
    file_name = (file_name or '').strip('/')
    if is_folder:
        prefix = _escape_like(f"{file_name}/")
        return FileModule.query.filter(FileModule.file.like(f"{prefix}%", escape='\\'))
    return FileModule.query.filter(FileModule.file == file_name)


@api.resource('/file/claim')
class FileClaimApi(Resource):
    @login_required
    def put(self):
        params = request.get_json() or {}
        file_name = (params.get('file') or '').strip('/')
        is_folder = bool(params.get('is_folder'))
        confirmed = bool(params.get('confirmed'))
        if not file_name:
            return error("file不能为空")

        user_id = int(current_user.get_id())
        query = _claim_query(file_name, is_folder)
        rows = query.with_for_update().all()
        if not rows:
            db.session.rollback()
            return error("没有找到可占坑的文件")

        conflicting_user_ids = {
            row.user_id
            for row in rows
            if row.user_id and int(row.user_id) != user_id
        }
        if conflicting_user_ids:
            users = UserModel.query.filter(UserModel.id.in_(conflicting_user_ids)).all()
            names = sorted({user.nickname or user.username for user in users})
            db.session.rollback()
            return error(f"其中已有文件被 {'、'.join(names) or '其他用户'} 占坑，不能重复占坑")

        untranslated = sum(
            max((row.total or 0) - (row.translate or 0), 0)
            for row in rows
        )
        if untranslated > 2000 and not confirmed:
            db.session.rollback()
            return success(data={
                'requires_confirmation': True,
                'untranslated': untranslated,
                'file_count': len(rows),
            })

        try:
            updated_count = query.filter(
                (FileModule.user_id.is_(None))
                | (FileModule.user_id == 0)
                | (FileModule.user_id == user_id)
            ).update(
                {FileModule.user_id: user_id},
                synchronize_session=False
            )
            db.session.commit()
        except Exception:
            db.session.rollback()
            current_app.logger.exception(
                "Claim files failed for user_id=%s file=%s",
                user_id,
                file_name
            )
            return error("占坑失败")

        return success(message="占坑成功", data={
            'updated_count': updated_count,
            'untranslated': untranslated,
        })

    @login_required
    def delete(self):
        params = request.get_json() or {}
        file_name = (params.get('file') or '').strip('/')
        is_folder = bool(params.get('is_folder'))
        if not file_name:
            return error("file不能为空")

        user_id = int(current_user.get_id())
        query = _claim_query(file_name, is_folder)
        rows = query.with_for_update().all()
        if not rows:
            db.session.rollback()
            return error("没有找到可取消占坑的文件")
        if any(row.user_id and int(row.user_id) != user_id for row in rows):
            db.session.rollback()
            return error("其中包含其他用户的占坑，不能整批取消")

        try:
            updated_count = query.filter(FileModule.user_id == user_id).update(
                {FileModule.user_id: 0},
                synchronize_session=False
            )
            db.session.commit()
        except Exception:
            db.session.rollback()
            current_app.logger.exception(
                "Release files failed for user_id=%s file=%s",
                user_id,
                file_name
            )
            return error("取消占坑失败")

        return success(message="已取消占坑", data={
            'updated_count': updated_count,
        })


@api.resource('/file')
class FileApi(Resource, BaseApi):
    model = FileModule
    @login_required
    def get(self):
        return super().get()
     
    @login_required
    def put(self):
        file_name = request.args.get('file')
        params = request.get_json()
        if not current_user.is_authenticated:
            return error('请先登录！')
        if current_user.roles != 'admin':
            return error("无权限！")
        user_id = current_user.get_id()
        if request.is_json:
            data = request.get_json()
            data['modified_by'] = user_id
            
        if file_name is None or params['cn_json'] is None:
            return error("更新失败：file和cn_json不能为空")
        item = self.model.query.filter(getattr(self.model, 'file') == file_name).first()
        if item is None:
            return error(f"更新失败：没有找到相关条目")
        print(item.to_dict())
        if item.update(commit=True,  updateFunc=self._update, cn_json=json.dumps(params['cn_json'], ensure_ascii=False), locked=1) is None:
            return error(f"更新失败：数据库更新失败")
        return success(message=f"文件 {item.file} 已更新。", data = item.to_dict())

    @login_required
    def post(self):
        return error('禁止新增')
    def _create(self, file):
        if file is None:
            raise Exception("file is none")

    
    def _delete(self, file):
        if file is None:
            raise Exception("file is none")

    
    def _update(self,file):
        if file is None:
            raise Exception("file is none")

# api_foo.py
from flask_restful import Resource, Api, request
from .restful_utils import *
from app.model import FileModule, session
from .base import BaseApi
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from sqlalchemy import text
from sqlalchemy.sql import func

import json

api = Api()

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
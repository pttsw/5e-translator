# api_foo.py
from flask_restful import Resource, Api, request
from flask import current_app
from .restful_utils import *
from app.model import UserModel, InviteCodeModel, db, get_next_user_id, generate_invite_code
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from sqlalchemy.exc import IntegrityError

from .base import BaseApi
import json
api = Api()
login_manager = LoginManager()

class User(UserMixin, UserModel):
    # def __init__(self,user_id):
    #     self.id = user_id
        
    def __init__(self, username, password):
        self.username = username
        self.password = password
        
    def login(self):
        sql_user = self.query.filter_by(username = self.username).first()
        if not sql_user:
            return False, "该用户名未注册"
        if not sql_user.check_password(self.password):
            return False, "密码错误"
        if sql_user.password and not UserModel.is_password_hashed(sql_user.password):
            sql_user.set_password(self.password)
            sql_user.save()
        self.id = sql_user.id
        self.roles = sql_user.roles
        return True, ""
    
@login_manager.user_loader
def load_user(userid):
    return User.query.get(userid)
    # return User(userid)

@api.resource('/user')
class UserApi(Resource, BaseApi):
    model = User
    @login_required
    def get(self):
        if current_user.is_authenticated:
            user_id = current_user.get_id()
            user = UserModel.query.get(user_id)
            return self._get_id(user)
        else:
            return error('当前无用户登录')
    @login_required
    def post(self):
        return error("禁止新增")
        
    @login_required
    def update(self):
        return error("禁止修改")
    
    @login_required
    def put(self):
        data = request.get_json()
        if not data:
            return error(message="请求体不能为空")

        user = UserModel.query.get(current_user.get_id())
        if user is None:
            return error(message="当前用户不存在")

        has_nickname = 'nickname' in data
        nickname = (data.get('nickname') or '').strip()
        if has_nickname:
            if not nickname:
                return error(message="昵称不能为空")
            if len(nickname) > 255:
                return error(message="昵称不能超过 255 个字符")

        current_password = data.get('current_password') or ''
        new_password = data.get('new_password') or ''
        confirm_password = data.get('confirm_password') or ''
        wants_password_change = bool(
            current_password or new_password or confirm_password
        )

        if not has_nickname and not wants_password_change:
            return error(message="没有需要更新的内容")

        if wants_password_change:
            if not current_password:
                return error(message="请输入当前密码")
            if not user.check_password(current_password):
                return error(message="当前密码错误")
            if len(new_password) < 6:
                return error(message="新密码至少 6 位")
            if new_password != confirm_password:
                return error(message="两次输入的新密码不一致")
            if user.check_password(new_password):
                return error(message="新密码不能与当前密码相同")

        if has_nickname:
            user.nickname = nickname
        if wants_password_change:
            user.set_password(new_password)

        try:
            db.session.commit()
        except Exception:
            db.session.rollback()
            current_app.logger.exception(
                "Update profile failed for user_id=%s",
                current_user.get_id()
            )
            return error(message="个人信息更新失败")

        return success(message="个人信息已更新", data=user.to_dict())
    # def delete(self, id):
    #     s = NewsModel.query.get(id)
    #     if s:
    #         if s.delete():
    #             return success()
    #         else:
    #             return error()
    #     else :
    #         return error(f"删除失败：没有id为{id}的脚本")

    # def post(self):
    #     if request.is_json:
    #         data = request.get_json()
    #         new_script = NewsModel.create(script_name=data['script_name'], setting=json.dumps(
    #             data['setting']), modified_by=data['modified_by'])
            
    #         return success(message=f"Script {new_script.script_name} has been created successfully.")
    #     else:
    #         return error("The request payload is not in JSON format")
@api.resource('/login')
class LoginApi(Resource):
    def post(self):
        data = request.get_json()
        if not data:
            return error(message="请求体不能为空")
        if not data.get("username") or not data.get("password"):
            return error(message="用户名和密码不能为空")
        # 校验用户名密码
        user = User(data["username"],data["password"])
        ok, msg = user.login()
        if not ok:
            return error(message=msg)
        login_user(user)
        return success(message=f"Login successfully.", data = {'token':'admin_token'})


@api.resource('/register')
class RegisterApi(Resource):
    def post(self):
        data = request.get_json()
        if not data:
            return error(message="请求体不能为空")

        username = (data.get('username') or '').strip()
        password = data.get('password') or ''
        confirm_password = data.get('confirm_password') or data.get('confirmPassword') or ''
        invite_code = (data.get('invite_code') or data.get('inviteCode') or '').strip().upper()

        if not username:
            return error(message="用户名不能为空")
        if len(username) < 3:
            return error(message="用户名至少 3 位")
        if not password:
            return error(message="密码不能为空")
        if len(password) < 6:
            return error(message="密码至少 6 位")
        if confirm_password and confirm_password != password:
            return error(message="两次输入的密码不一致")
        if not invite_code:
            return error(message="邀请码不能为空")
        if UserModel.query.filter_by(username=username).first():
            return error(message="用户名已存在")
        invite = InviteCodeModel.query.filter_by(code=invite_code).first()
        if invite is None:
            return error(message="邀请码不存在")
        if invite.used_by:
            return error(message="邀请码已使用")

        user = UserModel(username=username, password=password)
        user.id = get_next_user_id()
        user.roles = data.get('roles') or 'editor'
        try:
            db.session.add(user)
            invite.used_by = user.id
            invite.used_at = db.func.now()
            db.session.commit()
        except IntegrityError as exc:
            db.session.rollback()
            current_app.logger.exception("Register failed due to integrity error for username=%s", username)
            if 'Duplicate entry' in str(exc):
                return error(message="用户名已存在")
            return error(message="注册失败：数据约束冲突")
        except Exception:
            db.session.rollback()
            current_app.logger.exception("Register failed for username=%s", username)
            return error(message="注册失败：数据库写入异常")
        return success(message="Register successfully.", data={
            'id': user.id,
            'username': user.username,
            'roles': user.roles
        })

@api.resource('/logout')
class LogoutApi(Resource):
    @login_required
    def post(self):
        logout_user()
        return success(message="Logged out successfully!")


@api.resource('/invite-code')
class InviteCodeApi(Resource):
    @login_required
    def get(self):
        if current_user.roles != 'admin':
            return error(message="只有管理员可以查看邀请码")
        query = InviteCodeModel.query
        show_used = request.args.get('show_used', '1')
        if show_used != '1':
            query = query.filter(InviteCodeModel.used_by.is_(None))
        items = query.order_by(InviteCodeModel.created_at.desc()).all()
        return success(data={
            'count': len(items),
            'items': [item.to_dict() for item in items]
        })

    @login_required
    def post(self):
        if current_user.roles != 'admin':
            return error(message="只有管理员可以生成邀请码")
        for _ in range(5):
            code = generate_invite_code()
            if InviteCodeModel.query.filter_by(code=code).first() is None:
                invite = InviteCodeModel(code=code, created_by=current_user.get_id())
                try:
                    db.session.add(invite)
                    db.session.commit()
                    return success(message="邀请码已生成", data=invite.to_dict())
                except IntegrityError:
                    db.session.rollback()
                    continue
                except Exception:
                    db.session.rollback()
                    current_app.logger.exception("Create invite code failed")
                    return error(message="邀请码生成失败")
        return error(message="邀请码生成失败，请重试")

#!/usr/bin/python3
# -*- coding: UTF-8 -*-

from sqlalchemy import Column, Integer, String, TIMESTAMP, text, func
from werkzeug.security import generate_password_hash, check_password_hash

from .base import CRUDMixin, db
import datetime


class UserModel(db.Model, CRUDMixin):
    __tablename__ = 'user'

    id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(String(length=255), unique=True)
    nickname = Column(String(length=255))
    password = Column(String(length=255))
    roles = Column(String(length=255))
    created_at = Column(
        TIMESTAMP, server_default="CURRENT_TIMESTAMP()")
    modified_at = Column(
        TIMESTAMP, server_default="CURRENT_TIMESTAMP()", onupdate=datetime.datetime.now)

    def __init__(self, username, password):
        self.username = username
        self.set_password(password)

    @staticmethod
    def is_password_hashed(password):
        if not password:
            return False
        return password.startswith('pbkdf2:') or password.startswith('scrypt:')

    def set_password(self, password):
        if password is None:
            self.password = None
            return
        if self.is_password_hashed(password):
            self.password = password
            return
        self.password = generate_password_hash(password)

    def check_password(self, password):
        if not self.password:
            return False
        if self.is_password_hashed(self.password):
            return check_password_hash(self.password, password)
        return self.password == password

    def update(self, commit=True, updateFunc=None, **kwargs):
        password = kwargs.pop('password', None)
        result = super().update(commit=commit, updateFunc=updateFunc, **kwargs)
        if result is None:
            return None
        if password is not None:
            self.set_password(password)
            try:
                if commit:
                    db.session.commit()
                return self
            except Exception:
                db.session.rollback()
                return None
        return result

    def to_dict(self, *keys):
        data = {
            "id": self.id,
            "username": self.username,
            "nickname": self.nickname,
            "roles":self.roles,
            "created_at": str(self.created_at),
            "modified_at": str(self.modified_at),
        }

        for key in keys:
            if isinstance(key, str):
                data[key] = getattr(self, key)
        return data

    
    def __repr__(self):
        return f"<Script {self.username}>"


def migrate_plaintext_passwords():
    users = UserModel.query.all()
    changed = False
    for user in users:
        if user.password and not UserModel.is_password_hashed(user.password):
            user.set_password(user.password)
            changed = True
    if changed:
        db.session.commit()
    return changed


def get_next_user_id():
    current_max_id = db.session.query(func.max(UserModel.id)).scalar()
    if current_max_id is None:
        return 1
    return int(current_max_id) + 1


def ensure_user_table_schema():
    statements = [
        "ALTER TABLE `user` MODIFY COLUMN `username` VARCHAR(255) NOT NULL",
        "ALTER TABLE `user` MODIFY COLUMN `password` VARCHAR(512)",
        "ALTER TABLE `user` MODIFY COLUMN `roles` VARCHAR(255) DEFAULT 'editor'",
        "CREATE UNIQUE INDEX `idx_user_username_unique` ON `user` (`username`)",
    ]
    for statement in statements:
        try:
            db.session.execute(text(statement))
            db.session.commit()
        except Exception:
            db.session.rollback()

    try:
        db.session.execute(text(
            """
            UPDATE `user`
            SET `roles` = 'editor'
            WHERE `roles` IS NULL OR `roles` = ''
            """
        ))
        db.session.commit()
    except Exception:
        db.session.rollback()

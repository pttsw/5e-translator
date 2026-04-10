#!/usr/bin/python3
# -*- coding: UTF-8 -*-

import secrets
import string

from sqlalchemy import Column, Integer, String, TIMESTAMP, text

from .base import CRUDMixin, db


class InviteCodeModel(db.Model, CRUDMixin):
    __tablename__ = 'invite_code'

    code = Column(String(length=32), primary_key=True)
    created_by = Column(Integer)
    used_by = Column(Integer)
    created_at = Column(TIMESTAMP, server_default="CURRENT_TIMESTAMP()")
    used_at = Column(TIMESTAMP, nullable=True)

    def __init__(self, code, created_by=None):
        self.code = code
        self.created_by = created_by

    def to_dict(self, *keys):
        data = {
            "code": self.code,
            "created_by": self.created_by,
            "used_by": self.used_by,
            "created_at": str(self.created_at) if self.created_at else "",
            "used_at": str(self.used_at) if self.used_at else "",
            "used": 1 if self.used_by else 0
        }
        for key in keys:
            if isinstance(key, str):
                data[key] = getattr(self, key)
        return data

    def __repr__(self):
        return f"<InviteCode {self.code}>"


def generate_invite_code(length=10):
    alphabet = string.ascii_uppercase + string.digits
    return ''.join(secrets.choice(alphabet) for _ in range(length))


def ensure_invite_code_table_schema():
    create_table_sql = """
    CREATE TABLE IF NOT EXISTS `invite_code` (
        `code` VARCHAR(32) NOT NULL,
        `created_by` INT NULL,
        `used_by` INT NULL,
        `created_at` TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        `used_at` TIMESTAMP NULL DEFAULT NULL,
        PRIMARY KEY (`code`),
        INDEX `idx_invite_code_used_by` (`used_by`)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """
    try:
        db.session.execute(text(create_table_sql))
        db.session.commit()
    except Exception:
        db.session.rollback()

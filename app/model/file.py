#!/usr/bin/python3
# -*- coding: UTF-8 -*-

import os

from sqlalchemy import Column, Integer, String, ForeignKey, TIMESTAMP, text
from sqlalchemy.dialects.mysql import TINYINT
from sqlalchemy.orm import relationship

from .base import CRUDMixin, db
import datetime


class FileModule(db.Model, CRUDMixin):
    __tablename__ = 'file'

    file = Column(String, primary_key=True,)
    parent_dir = Column(String, default='')
    source_file = Column(String)
    total = Column(Integer, default=0)
    translate = Column(Integer, default=0)
    proofread = Column(Integer, default=0)
    en_json = Column(String)
    cn_json = Column(String)
    locked = Column(TINYINT, default=0)
    stale = Column(TINYINT, default=0)
    user_id = Column(Integer)
    modified_at = Column(
        TIMESTAMP, server_default="CURRENT_TIMESTAMP()", onupdate=datetime.datetime.now)
    modified_by = Column(Integer)

    def __init__(self, file, source_file, total, translate, proofread, en_json, cn_json, modified_by, locked=0, stale=0, parent_dir=''):
        super().__init__()
        self.file = file
        self.parent_dir = parent_dir if parent_dir is not None else os.path.dirname(file)
        self.source_file = source_file        
        self.total = total
        self.translate = translate
        self.proofread = proofread
        self.en_json = en_json
        self.cn_json = cn_json
        self.modified_by = modified_by
        self.locked = locked
        self.stale = stale
    def to_dict(self, *keys):
        data = {
            "file": self.file,
            "parent_dir": self.parent_dir,
            "source_file": self.source_file,
            "total": self.total,
            "translate": self.translate,
            "proofread": self.proofread,
            "en_json": self.en_json,
            "cn_json": self.cn_json,
            "stale": self.stale,
            "user_id": self.user_id,
            "modified_at": str(self.modified_at),
            "modified_by": self.modified_by,
        }
        # if words :
            # data['words'] = self.words
        for key in keys:
            if isinstance(key, str):
                data[key] = getattr(self, key)
        return data

    def __repr__(self):
        return f"<File {self.file}>"


def ensure_file_table_schema():
    statements = [
        "ALTER TABLE `file` ADD COLUMN `stale` TINYINT DEFAULT 0",
        "ALTER TABLE `file` ADD COLUMN `parent_dir` VARCHAR(255) DEFAULT ''",
        "CREATE INDEX `idx_file_parent_dir` ON `file` (`parent_dir`)",
        "CREATE INDEX `idx_file_source_file` ON `file` (`source_file`)",
        "CREATE INDEX `idx_file_stale` ON `file` (`stale`)",
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
            UPDATE `file`
            SET `parent_dir` = CASE
                WHEN LOCATE('/', `file`) = 0 THEN ''
                ELSE SUBSTRING(`file`, 1, LENGTH(`file`) - LENGTH(SUBSTRING_INDEX(`file`, '/', -1)) - 1)
            END
            WHERE `parent_dir` IS NULL OR `parent_dir` = ''
            """
        ))
        db.session.commit()
    except Exception:
        db.session.rollback()

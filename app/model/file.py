#!/usr/bin/python3
# -*- coding: UTF-8 -*-

from sqlalchemy import Column, Integer, String, ForeignKey, TIMESTAMP
from sqlalchemy.dialects.mysql import TINYINT
from sqlalchemy.orm import relationship

from .base import CRUDMixin, db
import datetime


class FileModule(db.Model, CRUDMixin):
    __tablename__ = 'file'

    file = Column(String, primary_key=True,)
    source_file = Column(String)
    total = Column(Integer, default=0)
    translate = Column(Integer, default=0)
    proofread = Column(Integer, default=0)
    en_json = Column(String)
    cn_json = Column(String)
    locked = Column(TINYINT, default=0)
    modified_at = Column(
        TIMESTAMP, server_default="CURRENT_TIMESTAMP()", onupdate=datetime.datetime.now)
    modified_by = Column(Integer)

    def __init__(self, file, source_file, total, translate, proofread, en_json, cn_json, modified_by, locked=0):
        super().__init__()
        self.file = file
        self.source_file = source_file        
        self.total = total
        self.translate = translate
        self.proofread = proofread
        self.en_json = en_json
        self.cn_json = cn_json
        self.modified_by = modified_by
        self.locked = locked
    def to_dict(self, *keys):
        data = {
            "file": self.file,
            "source_file": self.source_file,
            "total": self.total,
            "translate": self.translate,
            "proofread": self.proofread,
            "en_json": self.en_json,
            "cn_json": self.cn_json,
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

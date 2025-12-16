#!/usr/bin/python3
# -*- coding: UTF-8 -*-

from sqlalchemy import Column, Integer, String, ForeignKey
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

    def __init__(self, file, source_file, total, translate, proofread):
        super().__init__()
        self.file = file
        self.source_file = source_file        
        self.total = total
        self.translate = translate
        self.proofread = proofread
    def to_dict(self, *keys):
        data = {
            "file": self.file,
            "source_file": self.source_file,
            "total": self.total,
            "translate": self.translate,
            "proofread": self.proofread,
        }
        # if words :
            # data['words'] = self.words
        for key in keys:
            if isinstance(key, str):
                data[key] = getattr(self, key)
        return data

    def __repr__(self):
        return f"<File {self.file}>"

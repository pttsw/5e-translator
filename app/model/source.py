#!/usr/bin/python3
# -*- coding: UTF-8 -*-

from sqlalchemy import Column, Integer, String, ForeignKey, text
from sqlalchemy.dialects.mysql import TINYINT
from sqlalchemy.orm import relationship

from .base import CRUDMixin, db
import datetime


class SourceModel(db.Model, CRUDMixin):
    __tablename__ = 'word_usage'

    id = Column(Integer, primary_key=True, autoincrement=True)
    word_id = Column(Integer, ForeignKey('words.id'), nullable=False)
    file = Column(String, nullable=False)
    uid = Column(String, default='')
    key_path = Column(String, default='')
    context_key = Column(String, default='')
    context_label = Column(String, default='')
    version = Column(String(length=10))
    words = relationship('WordsModel', backref='words')

    def __init__(self, word_id, file, version, uid='', key_path='', context_key='', context_label=''):
        super().__init__()
        self.word_id = word_id
        self.file = file
        self.version = version
        self.uid = uid or ''
        self.key_path = key_path or ''
        self.context_key = context_key or ''
        self.context_label = context_label or ''
        
    def to_dict(self, *keys):
        data = {
            "word_id": self.word_id,
            "file": self.file,
            "uid": self.uid,
            "key_path": self.key_path,
            "context_key": self.context_key,
            "context_label": self.context_label,
            "version": self.version,
            "words": self.words.to_dict()
        }
        # if words :
            # data['words'] = self.words
        for key in keys:
            if isinstance(key, str):
                data[key] = getattr(self, key)
        return data

    def __repr__(self):
        return f"<WordUsage {self.file}:{self.uid}>"


def ensure_word_usage_table_schema():
    statements = [
        """
        CREATE TABLE IF NOT EXISTS `word_usage` (
          `id` int NOT NULL AUTO_INCREMENT,
          `word_id` int NOT NULL,
          `file` varchar(255) NOT NULL,
          `uid` varchar(512) NOT NULL DEFAULT '',
          `key_path` varchar(1024) NOT NULL DEFAULT '',
          `context_key` varchar(1024) NOT NULL DEFAULT '',
          `context_label` text,
          `version` char(10) DEFAULT NULL,
          PRIMARY KEY (`id`),
          UNIQUE KEY `uk_word_usage_file_uid` (`file`, `uid`),
          KEY `idx_word_usage_word_id` (`word_id`),
          KEY `idx_word_usage_file_word` (`file`, `word_id`),
          KEY `idx_word_usage_context` (`context_key`(191)),
          CONSTRAINT `word_usage_ibfk_1` FOREIGN KEY (`word_id`) REFERENCES `words` (`id`) ON DELETE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
        """,
        "ALTER TABLE `word_usage` ADD COLUMN `uid` varchar(512) NOT NULL DEFAULT ''",
        "ALTER TABLE `word_usage` ADD COLUMN `key_path` varchar(1024) NOT NULL DEFAULT ''",
        "ALTER TABLE `word_usage` ADD COLUMN `context_key` varchar(1024) NOT NULL DEFAULT ''",
        "ALTER TABLE `word_usage` ADD COLUMN `context_label` text",
        "CREATE UNIQUE INDEX `uk_word_usage_file_uid` ON `word_usage` (`file`, `uid`)",
        "CREATE INDEX `idx_word_usage_word_id` ON `word_usage` (`word_id`)",
        "CREATE INDEX `idx_word_usage_file_word` ON `word_usage` (`file`, `word_id`)",
        "CREATE INDEX `idx_word_usage_context` ON `word_usage` (`context_key`(191))",
        """
        INSERT IGNORE INTO `word_usage` (`word_id`, `file`, `uid`, `version`)
        SELECT `word_id`, `file`, CONCAT('legacy:', `word_id`), `version`
        FROM `source`
        """,
    ]
    for statement in statements:
        try:
            db.session.execute(text(statement))
            db.session.commit()
        except Exception:
            db.session.rollback()

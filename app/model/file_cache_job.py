#!/usr/bin/python3
# -*- coding: UTF-8 -*-

import datetime

from sqlalchemy import Column, Integer, String, TIMESTAMP, text

from .base import CRUDMixin, db


class FileCacheJobModel(db.Model, CRUDMixin):
    __tablename__ = 'file_cache_job'

    id = Column(Integer, primary_key=True, autoincrement=True)
    file = Column(String(255), nullable=False)
    target_revision = Column(Integer, nullable=False, default=0)
    reason = Column(String(255), nullable=False, default='')
    priority = Column(Integer, nullable=False, default=100)
    status = Column(String(32), nullable=False, default='queued')
    attempts = Column(Integer, nullable=False, default=0)
    message = Column(String(1024), nullable=False, default='')
    created_at = Column(TIMESTAMP, server_default="CURRENT_TIMESTAMP()")
    updated_at = Column(
        TIMESTAMP,
        server_default="CURRENT_TIMESTAMP()",
        onupdate=datetime.datetime.now,
    )
    started_at = Column(TIMESTAMP)
    finished_at = Column(TIMESTAMP)

    def to_dict(self, *keys):
        data = {
            "id": self.id,
            "file": self.file,
            "target_revision": int(self.target_revision or 0),
            "reason": self.reason or '',
            "priority": int(self.priority or 0),
            "status": self.status or 'queued',
            "attempts": int(self.attempts or 0),
            "message": self.message or '',
            "created_at": str(self.created_at),
            "updated_at": str(self.updated_at),
            "started_at": str(self.started_at) if self.started_at else '',
            "finished_at": str(self.finished_at) if self.finished_at else '',
        }
        for key in keys:
            if isinstance(key, str):
                data[key] = getattr(self, key)
        return data


def ensure_file_cache_job_table_schema():
    statements = [
        """
        CREATE TABLE IF NOT EXISTS `file_cache_job` (
          `id` int NOT NULL AUTO_INCREMENT,
          `file` varchar(255) NOT NULL,
          `target_revision` int NOT NULL DEFAULT 0,
          `reason` varchar(255) NOT NULL DEFAULT '',
          `priority` int NOT NULL DEFAULT 100,
          `status` varchar(32) NOT NULL DEFAULT 'queued',
          `attempts` int NOT NULL DEFAULT 0,
          `message` varchar(1024) NOT NULL DEFAULT '',
          `created_at` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
          `updated_at` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
          `started_at` timestamp NULL DEFAULT NULL,
          `finished_at` timestamp NULL DEFAULT NULL,
          PRIMARY KEY (`id`),
          KEY `idx_file_cache_job_file_status` (`file`, `status`),
          KEY `idx_file_cache_job_status_priority` (`status`, `priority`, `id`)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
        """,
        "ALTER TABLE `file_cache_job` ADD COLUMN `target_revision` int NOT NULL DEFAULT 0",
        "ALTER TABLE `file_cache_job` ADD COLUMN `reason` varchar(255) NOT NULL DEFAULT ''",
        "ALTER TABLE `file_cache_job` ADD COLUMN `priority` int NOT NULL DEFAULT 100",
        "ALTER TABLE `file_cache_job` ADD COLUMN `status` varchar(32) NOT NULL DEFAULT 'queued'",
        "ALTER TABLE `file_cache_job` ADD COLUMN `attempts` int NOT NULL DEFAULT 0",
        "ALTER TABLE `file_cache_job` ADD COLUMN `message` varchar(1024) NOT NULL DEFAULT ''",
        "ALTER TABLE `file_cache_job` ADD COLUMN `started_at` timestamp NULL DEFAULT NULL",
        "ALTER TABLE `file_cache_job` ADD COLUMN `finished_at` timestamp NULL DEFAULT NULL",
        "CREATE INDEX `idx_file_cache_job_file_status` ON `file_cache_job` (`file`, `status`)",
        "CREATE INDEX `idx_file_cache_job_status_priority` ON `file_cache_job` (`status`, `priority`, `id`)",
    ]
    for statement in statements:
        try:
            db.session.execute(text(statement))
            db.session.commit()
        except Exception:
            db.session.rollback()

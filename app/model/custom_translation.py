#!/usr/bin/python3
# -*- coding: UTF-8 -*-

import datetime

from sqlalchemy import Column, Integer, String, TIMESTAMP, Text, text

from .base import CRUDMixin, db


class CustomProjectModel(db.Model, CRUDMixin):
    __tablename__ = 'custom_project'

    id = Column(Integer, primary_key=True, autoincrement=True)
    title = Column(String(255), nullable=False)
    owner_id = Column(Integer, nullable=False)
    original_filename = Column(String(255), nullable=False)
    stored_path = Column(String(1024), nullable=False)
    file_type = Column(String(32), nullable=False)
    status = Column(String(32), default='active')
    total = Column(Integer, default=0)
    translated = Column(Integer, default=0)
    proofread = Column(Integer, default=0)
    similar_matches_json = Column(Text)
    source_match_status = Column(String(32), default='pending')
    continued_after_match = Column(Integer, default=0)
    export_path = Column(String(1024), default='')
    conversion_json_path = Column(String(1024), default='')
    conversion_report = Column(Text)
    created_at = Column(TIMESTAMP, server_default="CURRENT_TIMESTAMP()")
    modified_at = Column(
        TIMESTAMP, server_default="CURRENT_TIMESTAMP()", onupdate=datetime.datetime.now)

    def to_dict(self, *keys):
        return {
            "id": self.id,
            "title": self.title,
            "owner_id": self.owner_id,
            "original_filename": self.original_filename,
            "stored_path": self.stored_path,
            "file_type": self.file_type,
            "status": self.status,
            "total": int(self.total or 0),
            "translated": int(self.translated or 0),
            "proofread": int(self.proofread or 0),
            "similar_matches_json": self.similar_matches_json or "[]",
            "source_match_status": self.source_match_status or "pending",
            "continued_after_match": int(self.continued_after_match or 0),
            "export_path": self.export_path or "",
            "conversion_json_path": self.conversion_json_path or "",
            "conversion_report": self.conversion_report or "",
            "created_at": str(self.created_at),
            "modified_at": str(self.modified_at),
        }


class CustomProjectMemberModel(db.Model, CRUDMixin):
    __tablename__ = 'custom_project_member'

    id = Column(Integer, primary_key=True, autoincrement=True)
    project_id = Column(Integer, nullable=False)
    user_id = Column(Integer, nullable=False)
    role = Column(String(32), default='editor')
    created_at = Column(TIMESTAMP, server_default="CURRENT_TIMESTAMP()")

    def to_dict(self, *keys):
        return {
            "id": self.id,
            "project_id": self.project_id,
            "user_id": self.user_id,
            "role": self.role,
            "created_at": str(self.created_at),
        }


class CustomInviteModel(db.Model, CRUDMixin):
    __tablename__ = 'custom_project_invite'

    id = Column(Integer, primary_key=True, autoincrement=True)
    project_id = Column(Integer, nullable=False)
    token = Column(String(64), nullable=False, unique=True)
    created_by = Column(Integer, nullable=False)
    created_at = Column(TIMESTAMP, server_default="CURRENT_TIMESTAMP()")

    def to_dict(self, *keys):
        return {
            "id": self.id,
            "project_id": self.project_id,
            "token": self.token,
            "created_by": self.created_by,
            "created_at": str(self.created_at),
        }


class CustomSegmentModel(db.Model, CRUDMixin):
    __tablename__ = 'custom_segment'

    id = Column(Integer, primary_key=True, autoincrement=True)
    project_id = Column(Integer, nullable=False)
    seq = Column(Integer, nullable=False)
    block_index = Column(Integer, default=0)
    block_type = Column(String(32), default='paragraph')
    en_text = Column(Text, nullable=False)
    cn_text = Column(Text)
    proofread = Column(Integer, default=0)
    modified_by = Column(Integer)
    origin_ref_json = Column(Text)
    modified_at = Column(
        TIMESTAMP, server_default="CURRENT_TIMESTAMP()", onupdate=datetime.datetime.now)

    def to_dict(self, *keys):
        return {
            "id": self.id,
            "project_id": self.project_id,
            "seq": int(self.seq or 0),
            "block_index": int(self.block_index or 0),
            "block_type": self.block_type or "paragraph",
            "en_text": self.en_text or "",
            "cn_text": self.cn_text or "",
            "proofread": int(self.proofread or 0),
            "modified_by": self.modified_by,
            "origin_ref_json": self.origin_ref_json or "{}",
            "modified_at": str(self.modified_at),
        }


def ensure_custom_translation_table_schema():
    statements = [
        """
        CREATE TABLE IF NOT EXISTS `custom_project` (
            `id` INT NOT NULL AUTO_INCREMENT,
            `title` VARCHAR(255) NOT NULL,
            `owner_id` INT NOT NULL,
            `original_filename` VARCHAR(255) NOT NULL,
            `stored_path` VARCHAR(1024) NOT NULL,
            `file_type` VARCHAR(32) NOT NULL,
            `status` VARCHAR(32) NOT NULL DEFAULT 'active',
            `total` INT NOT NULL DEFAULT 0,
            `translated` INT NOT NULL DEFAULT 0,
            `proofread` INT NOT NULL DEFAULT 0,
            `similar_matches_json` TEXT,
            `source_match_status` VARCHAR(32) NOT NULL DEFAULT 'pending',
            `continued_after_match` TINYINT NOT NULL DEFAULT 0,
            `export_path` VARCHAR(1024) DEFAULT '',
            `conversion_json_path` VARCHAR(1024) DEFAULT '',
            `conversion_report` TEXT,
            `created_at` TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            `modified_at` TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            PRIMARY KEY (`id`),
            INDEX `idx_custom_project_owner` (`owner_id`)
        ) DEFAULT CHARSET=utf8mb4
        """,
        """
        CREATE TABLE IF NOT EXISTS `custom_project_member` (
            `id` INT NOT NULL AUTO_INCREMENT,
            `project_id` INT NOT NULL,
            `user_id` INT NOT NULL,
            `role` VARCHAR(32) NOT NULL DEFAULT 'editor',
            `created_at` TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (`id`),
            UNIQUE KEY `idx_custom_member_unique` (`project_id`, `user_id`),
            INDEX `idx_custom_member_user` (`user_id`)
        ) DEFAULT CHARSET=utf8mb4
        """,
        """
        CREATE TABLE IF NOT EXISTS `custom_project_invite` (
            `id` INT NOT NULL AUTO_INCREMENT,
            `project_id` INT NOT NULL,
            `token` VARCHAR(64) NOT NULL,
            `created_by` INT NOT NULL,
            `created_at` TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (`id`),
            UNIQUE KEY `idx_custom_invite_token` (`token`),
            INDEX `idx_custom_invite_project` (`project_id`)
        ) DEFAULT CHARSET=utf8mb4
        """,
        """
        CREATE TABLE IF NOT EXISTS `custom_segment` (
            `id` INT NOT NULL AUTO_INCREMENT,
            `project_id` INT NOT NULL,
            `seq` INT NOT NULL,
            `block_index` INT NOT NULL DEFAULT 0,
            `block_type` VARCHAR(32) NOT NULL DEFAULT 'paragraph',
            `en_text` TEXT NOT NULL,
            `cn_text` TEXT,
            `proofread` INT NOT NULL DEFAULT 0,
            `modified_by` INT,
            `origin_ref_json` TEXT,
            `modified_at` TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            PRIMARY KEY (`id`),
            INDEX `idx_custom_segment_project_seq` (`project_id`, `seq`)
        ) DEFAULT CHARSET=utf8mb4
        """,
    ]
    for statement in statements:
        try:
            db.session.execute(text(statement))
            db.session.commit()
        except Exception:
            db.session.rollback()


    # Existing deployments may already have the initial custom tables. Keep this
    # migration additive so it never touches the established translation tables.
    upgrades = [
        "ALTER TABLE `custom_project` ADD COLUMN `source_match_status` VARCHAR(32) NOT NULL DEFAULT 'pending'",
        "ALTER TABLE `custom_project` ADD COLUMN `continued_after_match` TINYINT NOT NULL DEFAULT 0",
        "ALTER TABLE `custom_project` ADD COLUMN `export_path` VARCHAR(1024) DEFAULT ''",
        "ALTER TABLE `custom_segment` ADD COLUMN `origin_ref_json` TEXT",
    ]
    for statement in upgrades:
        try:
            db.session.execute(text(statement))
            db.session.commit()
        except Exception:
            db.session.rollback()

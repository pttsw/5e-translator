from sqlalchemy import text

from .base import db


def ensure_term_table_schema():
    try:
        db.session.execute(text(
            "ALTER TABLE `term` ADD COLUMN `modified_at` "
            "TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP "
            "ON UPDATE CURRENT_TIMESTAMP"
        ))
        db.session.commit()
    except Exception:
        db.session.rollback()

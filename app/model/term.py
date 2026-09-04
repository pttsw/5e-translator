from sqlalchemy import text

from .base import db


TERM_CATEGORY_LABELS = {
    'action': '动作',
    'adventure': '冒险',
    'area': '地点',
    'background': '背景',
    'facility': '据点',
    'bestiary': '怪物',
    'book': '书籍',
    'card': '卡牌',
    'class': '职业',
    'classFeature': '职业特性',
    'condition': '状态',
    'creature': '生物',
    'deity': '神祇',
    'feat': '专长',
    'group': '组织',
    'hazard': '危害',
    'item': '物品',
    'itemMastery': '物品精通',
    'itemProperty': '物品词条',
    'language': '语言',
    'name': '名字',
    'normal': '常规翻译',
    'object': '物件',
    'plane': '位面',
    'psionic': '灵能',
    'race': '种族',
    'recipe': '食谱',
    'sense': '感官',
    'sentence': '句子',
    'skill': '技能',
    'spell': '法术',
    'status': '状态',
    'subclassFeature': '子职特性',
    'trap': '陷阱',
    'variantrule': '规则术语',
    'vehicle': '载具',
}


def _ensure_term_category_table_schema():
    statements = [
        (
            "CREATE TABLE IF NOT EXISTS `term_category` ("
            "`code` VARCHAR(50) COLLATE utf8mb4_0900_as_cs NOT NULL, "
            "`name` VARCHAR(100) NOT NULL, "
            "`sort_order` INT NOT NULL DEFAULT 0, "
            "`created_at` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP, "
            "`updated_at` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP "
            "ON UPDATE CURRENT_TIMESTAMP, "
            "`created_by` INT DEFAULT NULL, "
            "`modified_by` INT DEFAULT NULL, "
            "PRIMARY KEY (`code`)"
            ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 "
            "COLLATE=utf8mb4_0900_ai_ci"
        ),
        (
            "ALTER TABLE `term_category` MODIFY COLUMN `code` "
            "VARCHAR(50) COLLATE utf8mb4_0900_as_cs NOT NULL"
        ),
    ]
    for statement in statements:
        try:
            db.session.execute(text(statement))
            db.session.commit()
        except Exception:
            db.session.rollback()


def _seed_term_categories():
    for index, (code, name) in enumerate(TERM_CATEGORY_LABELS.items(), start=1):
        try:
            db.session.execute(
                text(
                    "INSERT IGNORE INTO `term_category` "
                    "(`code`, `name`, `sort_order`) "
                    "VALUES (:code, :name, :sort_order)"
                ),
                {'code': code, 'name': name, 'sort_order': index}
            )
        except Exception:
            db.session.rollback()
            return
    try:
        db.session.execute(text(
            "INSERT IGNORE INTO `term_category` (`code`, `name`, `sort_order`) "
            "SELECT DISTINCT TRIM(`category`), TRIM(`category`), 1000 "
            "FROM `term` "
            "WHERE TRIM(`category`) != ''"
        ))
        db.session.commit()
    except Exception:
        db.session.rollback()


def ensure_term_table_schema():
    statements = [
        (
            "ALTER TABLE `term` MODIFY COLUMN `en` "
            "VARCHAR(255) COLLATE utf8mb4_0900_as_cs NOT NULL"
        ),
        (
            "ALTER TABLE `term` MODIFY COLUMN `category` "
            "CHAR(50) COLLATE utf8mb4_0900_as_cs NOT NULL"
        ),
    ]
    for statement in statements:
        try:
            db.session.execute(text(statement))
            db.session.commit()
        except Exception:
            db.session.rollback()

    try:
        db.session.execute(text(
            "ALTER TABLE `term` ADD COLUMN `modified_at` "
            "TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP "
            "ON UPDATE CURRENT_TIMESTAMP"
        ))
        db.session.commit()
    except Exception:
        db.session.rollback()

    _ensure_term_category_table_schema()
    _seed_term_categories()

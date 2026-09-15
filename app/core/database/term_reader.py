"""Bounded, read-only term-table provider for reference tools (no Redis writes)."""
import os


class TermTableReader:
    def __call__(self, phrase):
        if os.getenv('TRANSLATOR_DB_BACKEND', '').lower() == 'memory':
            return []
        import pymysql
        from config import DB_CONFIG
        connection = pymysql.connect(
            host=DB_CONFIG['HOST'], port=DB_CONFIG['PORT'], user=DB_CONFIG['USER'],
            password=DB_CONFIG['PASSWORD'], database=DB_CONFIG['DATABASE'], charset='utf8mb4',
            connect_timeout=5, read_timeout=5, write_timeout=5,
            cursorclass=pymysql.cursors.DictCursor,
        )
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    'SELECT en, cn, category, source FROM term '
                    'WHERE LOWER(TRIM(en)) = LOWER(%s) ORDER BY category, cn, source LIMIT 201',
                    (phrase.strip(),),
                )
                return cursor.fetchall()
        finally:
            connection.close()

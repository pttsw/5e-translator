import pymysql
import time
from config import logger


class MySQLDatabase:
    def __init__(self, host, port, user, password, database):
        self.host = host
        self.port = port
        self.user = user
        self.password = password
        self.database = database
        self.connection = None
        self.cursor = None
        self.ok = False
        self.connect()

    def connect(self):
        """建立数据库连接"""
        try:
            self.connection = pymysql.connect(
                host=self.host,
                user=self.user,
                port=self.port,
                password=self.password,
                charset='utf8mb4',
                cursorclass=pymysql.cursors.DictCursor,
                database=self.database
            )
            self.cursor = self.connection.cursor()
            # print("成功连接到数据库")
            self.ok = True
        except pymysql.MySQLError as e:
            print(f"连接失败，错误原因: {e}")
            self.ok = False
            
    def close(self):
        """关闭数据库连接"""
        if self.cursor:
            self.cursor.close()
        if self.connection and self.connection.open:
            self.connection.close()
            # print("数据库连接已关闭")

    def execute_query(self, query, params=None):
        """执行查询语句"""
        try:
            self.cursor.execute(query, params)
            return self.cursor.fetchall()
        except pymysql.MySQLError as e:
            logger.error(f"查询失败，SQL:{query} 错误原因: {e}")
            return None

    def execute_non_query(self, query, params=None):
        """执行非查询语句（增、删、改）"""
        retryable_error_codes = {1205, 1213}
        max_retries = 3
        base_sleep_seconds = 0.2

        for attempt in range(max_retries + 1):
            try:
                self.cursor.execute(query, params)
                self.connection.commit()
                return True
            except pymysql.MySQLError as e:
                self.connection.rollback()
                error_code = e.args[0] if getattr(e, "args", None) else None
                is_retryable = error_code in retryable_error_codes
                if is_retryable and attempt < max_retries:
                    sleep_seconds = base_sleep_seconds * (2 ** attempt)
                    logger.warning(
                        f"语句执行遇到可重试错误(code={error_code})，"
                        f"{sleep_seconds:.1f}秒后重试第{attempt + 1}/{max_retries}次: {query}"
                    )
                    time.sleep(sleep_seconds)
                    continue
                logger.error(f"语句执行失败，错误原因: {e},{query},{params}")
                return False

    def insert(self, table, data):
        """插入数据"""
        columns = ', '.join(data.keys())
        placeholders = ', '.join(['%s'] * len(data))
        query = f"INSERT INTO {table} ({columns}) VALUES ({placeholders})"
        return self.execute_non_query(query, tuple(data.values()))

    def update(self, table, data, condition):
        """更新数据"""
        set_clause = ', '.join([f"{column} = %s" for column in data.keys()])
        where_clause = ' AND '.join([f"{column} = %s" for column in condition.keys()])
        query = f"UPDATE {table} SET {set_clause} WHERE {where_clause}"
        return self.execute_non_query(query, tuple(data.values()) + tuple(condition.values()))

    def delete(self, table, condition):
        """删除数据"""
        where_clause = ' AND '.join([f"{column} = %s" for column in condition.keys()])
        query = f"DELETE FROM {table} WHERE {where_clause}"
        return self.execute_non_query(query, tuple(condition.values()))

    def select(self, table, columns='*', condition=None, order_by=None, limit=None):
        """查询数据"""
        columns_clause = ', '.join(columns) if isinstance(columns, (list, tuple)) else columns
        query = f"SELECT {columns_clause} FROM {table}"
        params = tuple()
        if condition:
            where_clause = ' AND '.join([f"{column} = %s" for column in condition.keys()])
            query = f"{query} WHERE {where_clause}"
            params = params + tuple(condition.values())
        if order_by:
            query = f"{query} ORDER BY {order_by}"
        if limit:
            query = f"{query} LIMIT {limit}"
        return self.execute_query(query, params)
    
    def paginate(self, table, page_number, page_size, conditions=None):
        offset = (page_number - 1) * page_size
        query = f"SELECT * FROM {table}"
        if conditions:
            placeholders = ' AND '.join([f"{k}=%s" for k in conditions.keys()])
            query += f" WHERE {placeholders}"
            params = tuple(conditions.values())
        else:
            params = None
        query += f" LIMIT %s OFFSET %s"
        if params:
            params = params + (page_size, offset)
        else:
            params = (page_size, offset)
        return self.execute_query(query, params)

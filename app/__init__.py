#!/usr/bin/python3
# -*- coding: UTF-8 -*-

from flask import Flask
from flask_cors import CORS
from .api import login_manager, api_bp as api_blueprint
from .model import db, migrate, ensure_file_table_schema, migrate_plaintext_passwords, ensure_user_table_schema, ensure_invite_code_table_schema

from config import DB_CONFIG, swagger

# 导入并安装pymysql作为MySQLdb的替代品
import pymysql
pymysql.install_as_MySQLdb()

# 创建Flask应用实例
app = Flask(__name__)

# 配置应用
app.config['SQLALCHEMY_DATABASE_URI'] = f"mysql://{DB_CONFIG['USER']}:{DB_CONFIG['PASSWORD']}@{DB_CONFIG['HOST']}:{DB_CONFIG['PORT']}/{DB_CONFIG['DATABASE']}"
app.config['JSON_AS_ASCII'] = False 
app.config['SECRET_KEY'] = 'your-secret-key'

# 初始化扩展
CORS(app)
db.init_app(app)
swagger.init_app(app)
migrate.init_app(app)
login_manager.init_app(app)

# 注册蓝图
app.register_blueprint(api_blueprint, url_prefix='/api/v1')

with app.app_context():
    ensure_invite_code_table_schema()
    ensure_user_table_schema()
    ensure_file_table_schema()
    migrate_plaintext_passwords()

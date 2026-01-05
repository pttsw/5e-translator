#!/usr/bin/python3
# -*- coding: UTF-8 -*-

from app import app

if __name__ == '__main__':
    # 只有当直接运行app.py时才会执行，用于开发环境
    app.run(host='0.0.0.0', port=80, debug=True)
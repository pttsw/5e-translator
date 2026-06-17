import os
import multiprocessing

bind = "0.0.0.0:8000"
# workers = multiprocessing.cpu_count() * 2 + 1
workers = 1
worker_class = "sync"
chdir = "/data/5e-translator"
pythonpath = "/data/5e-translator"
raw_env = [
    "SKIP_APP_BOOTSTRAP=0",
]
accesslog = "/data/5e-translator/log/gunicorn_access.log"
errorlog = "/data/5e-translator/log/gunicorn_error.log"
loglevel = "info"
pidfile = "/data/5e-translator/gunicorn.pid"
daemon = True

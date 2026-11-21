import os

bind = f"0.0.0.0:{os.getenv('PORT', '10000')}"
workers = 1
worker_class = "gthread"
threads = 4
timeout = 200
graceful_timeout = 20
accesslog = "-"
errorlog = "-"

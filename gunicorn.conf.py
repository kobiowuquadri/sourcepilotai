import os

# Render (and most hosts) inject PORT. Never hard-code it or set it manually on Render.
bind = f"0.0.0.0:{os.getenv('PORT', '').strip() or '10000'}"
workers = 1
worker_class = "gthread"
threads = 4
# Tavily research streams for up to ~5 minutes; keep the worker alive throughout.
timeout = 330
graceful_timeout = 20
accesslog = "-"
errorlog = "-"

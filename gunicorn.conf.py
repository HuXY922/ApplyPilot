from production_config import ProductionConfig
_config = ProductionConfig.from_env()
bind = f'0.0.0.0:{_config.port}'
workers = 1
worker_class = 'gthread'
threads = 4
timeout = 30
graceful_timeout = 30
accesslog = None
errorlog = '-'
loglevel = 'warning'
umask = 0o077
# App security uses configured HTTPS origin; it never trusts forwarded Host.
forwarded_allow_ips = ''
secure_scheme_headers = {}

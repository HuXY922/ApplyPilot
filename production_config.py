"""Strict production configuration. Never include secret values in errors/repr."""
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

@dataclass(frozen=True)
class ProductionConfig:
    port: int
    database_path: Path
    public_origin: str
    host: str
    user_token: str = field(repr=False)
    worker_token: str = field(repr=False)
    radar_token: str = field(repr=False)

    @classmethod
    def from_env(cls, env=None):
        env = os.environ if env is None else env
        def required(key):
            value = env.get(key)
            if not isinstance(value, str) or not value:
                raise ValueError(key + ' is required')
            return value
        raw_port = required('PORT')
        if not raw_port.isascii() or not raw_port.isdecimal() or not 1 <= int(raw_port) <= 65535:
            raise ValueError('PORT must be between 1 and 65535')
        origin = required('PUBLIC_ORIGIN')
        try:
            u = urlsplit(origin)
            host = u.hostname
            if (u.scheme != 'https' or not host or u.username or u.password
                or u.path or u.query or u.fragment or origin != 'https://' + u.netloc
                or not re.fullmatch(r'[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?', host)
                or host in ('localhost','healthcheck.railway.app') or '.' not in host
                or any(not label or len(label)>63 or label.startswith('-') or label.endswith('-') for label in host.split('.'))
                or u.netloc != host + (':' + str(u.port) if u.port is not None else '')
                or (u.port is not None and not 1 <= u.port <= 65535)):
                raise ValueError()
        except ValueError:
            raise ValueError('PUBLIC_ORIGIN must be an exact HTTPS origin without a path or credentials') from None
        path = Path(required('DATABASE_PATH'))
        if not path.is_absolute() or path.suffix not in ('.sqlite3', '.sqlite', '.db'):
            raise ValueError('DATABASE_PATH must be an absolute SQLite file path')
        path = path.resolve()
        # On Railway, fail closed if the volume is missing or misconfigured.
        if env.get('RAILWAY_ENVIRONMENT_ID') or env.get('RAILWAY_PROJECT_ID'):
            mount = env.get('RAILWAY_VOLUME_MOUNT_PATH')
            if mount != '/data' or not Path(mount).is_dir() or not path.is_relative_to(Path(mount).resolve()):
                raise ValueError('Railway requires a /data persistent volume and DATABASE_PATH inside it')
        tokens = [required('RECRUITMENT_'+r+'_TOKEN') for r in ('USER','WORKER','RADAR')]
        if any(not re.fullmatch(r'[A-Za-z0-9_-]{32,256}',t) for t in tokens) or len(set(tokens)) != 3:
            raise ValueError('Three distinct 32–256 character URL-safe tokens are required')
        return cls(int(raw_port),path,origin,u.netloc,*tokens)

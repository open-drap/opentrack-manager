try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

import os, re, aiosqlite, sqlite3
from datetime import datetime
from pathlib import Path

_TS_RE = re.compile(r'^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$')

def _pg_params(params):
    result = []
    for p in params:
        if isinstance(p, str) and _TS_RE.match(p):
            result.append(datetime.strptime(p, "%Y-%m-%d %H:%M:%S"))
        else:
            result.append(p)
    return result

DRIVER = os.getenv("DB_DRIVER", "sqlite").lower()
SQLITE_PATH = os.getenv("DB_PATH", "uptime.db")
DATABASE_URL = os.getenv("DATABASE_URL", "")
DB_SSL_CA = os.getenv("DB_SSL_CA", "")

if DB_SSL_CA and not os.path.isfile(DB_SSL_CA):
    _script_dir = Path(__file__).resolve().parent
    _alt = _script_dir / DB_SSL_CA
    if _alt.is_file():
        DB_SSL_CA = str(_alt)

_pool = None

async def get_pool():
    global _pool
    if _pool is not None:
        return _pool
    if DRIVER == "postgres":
        import asyncpg, ssl as _ssl
        _ssl_ctx = _ssl.create_default_context()
        _ssl_ctx.check_hostname = False
        _ssl_ctx.verify_mode = _ssl.CERT_NONE
        # Strip sslmode from URL — asyncpg uses the ssl kwarg instead
        _url = DATABASE_URL.split("?")[0]
        _pool = await asyncpg.create_pool(_url, ssl=_ssl_ctx)
    elif DRIVER == "mysql":
        import aiomysql
        import ssl as ssl_module
        ssl_ctx = None
        if DB_SSL_CA and os.path.isfile(DB_SSL_CA):
            ssl_ctx = ssl_module.create_default_context(cafile=DB_SSL_CA)
            ssl_ctx.check_hostname = False
            ssl_ctx.verify_mode = ssl_module.CERT_REQUIRED
        _pool = await aiomysql.create_pool(
            host=os.getenv("DB_HOST", "localhost"),
            port=int(os.getenv("DB_PORT", "4000")),
            user=os.getenv("DB_USER", "root"),
            password=os.getenv("DB_PASSWORD", ""),
            db=os.getenv("DB_NAME", "test"),
            cursorclass=aiomysql.cursors.DictCursor,
            ssl=ssl_ctx,
        )
    return _pool

def _convert_placeholders(sql: str) -> str:
    if DRIVER == "postgres":
        i = 0
        result = []
        for c in sql:
            if c == '?':
                i += 1
                result.append(f'${i}')
            else:
                result.append(c)
        return ''.join(result)
    if DRIVER == "mysql":
        return sql.replace('?', '%s')
    return sql

class _DB:
    def __init__(self):
        self._conn = None
        self._cursor = None

    async def connect(self):
        if DRIVER == "sqlite":
            self._conn = await aiosqlite.connect(SQLITE_PATH)
            self._conn.row_factory = sqlite3.Row
        else:
            pool = await get_pool()
            self._conn = await pool.acquire()
            if DRIVER == "mysql":
                self._cursor = await self._conn.cursor()
        return self

    async def close(self):
        if not self._conn:
            return
        if DRIVER == "sqlite":
            await self._conn.close()
        else:
            if DRIVER == "mysql" and self._cursor:
                await self._cursor.close()
            pool = await get_pool()
            await pool.release(self._conn)
        self._conn = None
        self._cursor = None

    async def __aenter__(self):
        await self.connect()
        return self

    async def __aexit__(self, *args):
        await self.close()

    async def execute(self, sql: str, params: list = None):
        sql = _convert_placeholders(sql)
        if params is None:
            params = []
        if DRIVER == "mysql":
            await self._cursor.execute(sql, params)
            return self._cursor
        elif DRIVER == "postgres":
            return await self._conn.execute(sql, *_pg_params(params))
        else:
            return await self._conn.execute(sql, params)

    async def executemany(self, sql: str, params_list: list):
        sql = _convert_placeholders(sql)
        if DRIVER == "mysql":
            await self._cursor.executemany(sql, params_list)
        elif DRIVER == "postgres":
            await self._conn.executemany(sql, params_list)
        else:
            await self._conn.executemany(sql, params_list)

    async def commit(self):
        if DRIVER == "sqlite":
            await self._conn.commit()
        elif DRIVER == "mysql":
            await self._conn.commit()

    async def fetchone(self, sql: str, params: list = None):
        if params is None:
            params = []
        if DRIVER == "mysql":
            await self.execute(sql, params)
            row = await self._cursor.fetchone()
            return dict(row) if row else None
        elif DRIVER == "postgres":
            row = await self._conn.fetchrow(_convert_placeholders(sql), *_pg_params(params))
            return dict(row) if row else None
        else:
            async with self._conn.execute(sql, params) as cur:
                row = await cur.fetchone()
            return dict(row) if row else None

    async def fetchall(self, sql: str, params: list = None):
        if params is None:
            params = []
        if DRIVER == "mysql":
            await self.execute(sql, params)
            rows = await self._cursor.fetchall()
            return [dict(r) for r in rows]
        elif DRIVER == "postgres":
            rows = await self._conn.fetch(_convert_placeholders(sql), *_pg_params(params))
            return [dict(r) for r in rows]
        else:
            async with self._conn.execute(sql, params) as cur:
                rows = await cur.fetchall()
            return [dict(r) for r in rows]
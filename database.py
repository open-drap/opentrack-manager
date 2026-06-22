from db import _DB, DRIVER

PK = "INTEGER PRIMARY KEY AUTOINCREMENT" if DRIVER == "sqlite" else "SERIAL PRIMARY KEY"
# SQLite stores timestamps as TEXT; PostgreSQL uses proper TIMESTAMP columns
TS_COL = "TEXT DEFAULT ''" if DRIVER == "sqlite" else "TIMESTAMP"

async def init_db():
    async with _DB() as db:
        if DRIVER == "mysql":
            await db.execute("SET SESSION sql_mode = ''")
        await db.execute(f"""
            CREATE TABLE IF NOT EXISTS users (
                id {PK},
                username TEXT UNIQUE NOT NULL,
                email TEXT UNIQUE NOT NULL,
                password TEXT NOT NULL,
                telegram_chat_id TEXT DEFAULT '',
                vault_key TEXT DEFAULT '',
                master_pin_hash TEXT DEFAULT '',
                master_pin_salt TEXT DEFAULT '',
                pin_enabled INTEGER DEFAULT 0,
                pin_timeout_minutes INTEGER DEFAULT 5,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.execute(f"""
            CREATE TABLE IF NOT EXISTS monitors (
                id {PK},
                user_id INTEGER NOT NULL,
                name TEXT DEFAULT '',
                url TEXT NOT NULL,
                type TEXT DEFAULT 'http',
                keyword TEXT DEFAULT '',
                port INTEGER DEFAULT 0,
                interval INTEGER DEFAULT 30,
                status TEXT DEFAULT 'pending',
                last_check TIMESTAMP,
                response_time REAL,
                down_since TIMESTAMP,
                ssl_expiry TEXT DEFAULT '',
                domain_expiry TEXT DEFAULT '',
                uptime_count INTEGER DEFAULT 0,
                down_count INTEGER DEFAULT 0,
                notified INTEGER DEFAULT 0,
                is_public INTEGER DEFAULT 0,
                custom_headers TEXT DEFAULT '{{}}',
                method TEXT DEFAULT 'GET',
                expected_status INTEGER DEFAULT 0,
                maintenance_from TEXT DEFAULT '',
                maintenance_to TEXT DEFAULT '',
                response_history TEXT DEFAULT '[]',
                tags TEXT DEFAULT '',
                FOREIGN KEY(user_id) REFERENCES users(id)
            )
        """)
        await db.execute(f"""
            CREATE TABLE IF NOT EXISTS incidents (
                id {PK},
                monitor_id INTEGER,
                started_at TIMESTAMP,
                ended_at TIMESTAMP,
                duration INTEGER,
                root_cause TEXT DEFAULT '',
                FOREIGN KEY(monitor_id) REFERENCES monitors(id)
            )
        """)
        await db.execute(f"""
            CREATE TABLE IF NOT EXISTS vault (
                id {PK},
                user_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                url TEXT DEFAULT '',
                username TEXT DEFAULT '',
                password TEXT DEFAULT '',
                api_key TEXT DEFAULT '',
                notes TEXT DEFAULT '',
                category TEXT DEFAULT '',
                password_changed_at {TS_COL},
                favorite INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(user_id) REFERENCES users(id)
            )
        """)
        await db.execute(f"""
            CREATE TABLE IF NOT EXISTS authenticator (
                id {PK},
                user_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                issuer TEXT DEFAULT '',
                secret TEXT NOT NULL,
                algorithm TEXT DEFAULT 'SHA1',
                digits INTEGER DEFAULT 6,
                period INTEGER DEFAULT 30,
                category TEXT DEFAULT '',
                favorite INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(user_id) REFERENCES users(id)
            )
        """)
        await db.execute(f"""
            CREATE TABLE IF NOT EXISTS audit_log (
                id {PK},
                user_id INTEGER NOT NULL,
                action TEXT NOT NULL,
                detail TEXT DEFAULT '',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(user_id) REFERENCES users(id)
            )
        """)
        await db.execute(f"""
            CREATE TABLE IF NOT EXISTS backup_codes (
                id {PK},
                user_id INTEGER NOT NULL,
                code_hash TEXT NOT NULL,
                used_at TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(user_id) REFERENCES users(id)
            )
        """)
        await db.execute(f"""
            CREATE TABLE IF NOT EXISTS share_links (
                id {PK},
                user_id INTEGER NOT NULL,
                vault_id INTEGER NOT NULL,
                token TEXT UNIQUE NOT NULL,
                expires_at TIMESTAMP NOT NULL,
                used_at TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(user_id) REFERENCES users(id),
                FOREIGN KEY(vault_id) REFERENCES vault(id)
            )
        """)
        await db.execute(f"""
            CREATE TABLE IF NOT EXISTS emergency_requests (
                id {PK},
                user_id INTEGER NOT NULL,
                trusted_contact TEXT DEFAULT '',
                note TEXT DEFAULT '',
                status TEXT DEFAULT 'requested',
                requested_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                approved_at TIMESTAMP,
                available_at TIMESTAMP,
                FOREIGN KEY(user_id) REFERENCES users(id)
            )
        """)
        await db.execute(f"""
            CREATE TABLE IF NOT EXISTS servers (
                id {PK},
                user_id INTEGER NOT NULL,
                hostname TEXT DEFAULT '',
                token TEXT UNIQUE NOT NULL,
                cpu REAL DEFAULT 0,
                ram REAL DEFAULT 0,
                disk REAL DEFAULT 0,
                last_heartbeat TIMESTAMP,
                ip TEXT DEFAULT '',
                uptime TEXT DEFAULT '',
                load_avg TEXT DEFAULT '',
                os_info TEXT DEFAULT '',
                docker_count INTEGER DEFAULT 0,
                nginx_status TEXT DEFAULT '',
                db_mysql TEXT DEFAULT '',
                db_postgres TEXT DEFAULT '',
                db_redis TEXT DEFAULT '',
                bw_rx REAL DEFAULT 0,
                bw_tx REAL DEFAULT 0,
                latency REAL DEFAULT 0,
                disk_growth REAL DEFAULT 0,
                svc_count INTEGER DEFAULT 0,
                proj_count INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(user_id) REFERENCES users(id)
            )
        """)
        await db.execute(f"""
            CREATE TABLE IF NOT EXISTS notes (
                id {PK},
                user_id INTEGER NOT NULL,
                title TEXT DEFAULT '',
                content TEXT DEFAULT '',
                reminder_at TIMESTAMP,
                reminder_sent INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(user_id) REFERENCES users(id)
            )
        """)
        # Migrate servers table for new metric columns
        _server_new_cols = [
            ("ip", "TEXT DEFAULT ''"),
            ("uptime", "TEXT DEFAULT ''"),
            ("load_avg", "TEXT DEFAULT ''"),
            ("os_info", "TEXT DEFAULT ''"),
            ("docker_count", "INTEGER DEFAULT 0"),
            ("nginx_status", "TEXT DEFAULT ''"),
            ("db_mysql", "TEXT DEFAULT ''"),
            ("db_postgres", "TEXT DEFAULT ''"),
            ("db_redis", "TEXT DEFAULT ''"),
            ("bw_rx", "REAL DEFAULT 0"),
            ("bw_tx", "REAL DEFAULT 0"),
            ("latency", "REAL DEFAULT 0"),
            ("disk_growth", "REAL DEFAULT 0"),
            ("svc_count", "INTEGER DEFAULT 0"),
            ("proj_count", "INTEGER DEFAULT 0"),
            ("projects", "TEXT DEFAULT '[]'"),
            ("ram_alerted", "INTEGER DEFAULT 0"),
            ("cpu_alerted", "INTEGER DEFAULT 0"),
            ("disk_alerted", "INTEGER DEFAULT 0"),
            ("offline_alerted", "INTEGER DEFAULT 0"),
        ]
        if DRIVER == "postgres":
            for col, typ in _server_new_cols:
                try:
                    await db.execute(f"ALTER TABLE servers ADD COLUMN IF NOT EXISTS {col} {typ}")
                except:
                    pass
            # Migrate vault.password_changed_at from TEXT → TIMESTAMP (needed for asyncpg type safety)
            try:
                await db.execute("""
                    ALTER TABLE vault
                    ALTER COLUMN password_changed_at TYPE TIMESTAMP
                    USING CASE WHEN password_changed_at IS NULL OR password_changed_at = ''
                          THEN NULL
                          ELSE password_changed_at::TIMESTAMP END
                """)
                await db.commit()
            except:
                pass
        elif DRIVER == "sqlite":
            for col, typ in _server_new_cols:
                try:
                    await db.execute(f"ALTER TABLE servers ADD COLUMN {col} {typ}")
                except:
                    pass

        # SQLite-only: add columns that may be missing from older schemas
        if DRIVER == "sqlite":
            for col, typ in [
                ("vault_key", "TEXT DEFAULT ''"),
                ("master_pin_hash", "TEXT DEFAULT ''"),
                ("master_pin_salt", "TEXT DEFAULT ''"),
                ("pin_enabled", "INTEGER DEFAULT 0"),
                ("pin_timeout_minutes", "INTEGER DEFAULT 5"),
                ("response_history", "TEXT DEFAULT '[]'"),
                ("tags", "TEXT DEFAULT ''"),
                ("category", "TEXT DEFAULT ''"),
                ("password_changed_at", "TEXT DEFAULT ''"),
                ("favorite", "INTEGER DEFAULT 0"),
            ]:
                tbl = "vault" if col in ("category", "password_changed_at", "favorite") else \
                       "monitors" if col in ("response_history", "tags") else "users"
                try:
                    await db.execute(f"ALTER TABLE {tbl} ADD COLUMN {col} {typ}")
                except:
                    pass
        await db.commit()
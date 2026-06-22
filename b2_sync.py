import os
import asyncio
import logging
import sqlite3
import tempfile
from pathlib import Path

B2_KEY_ID       = os.getenv("B2_KEY_ID", "")
B2_APP_KEY      = os.getenv("B2_APP_KEY", "")
B2_BUCKET       = os.getenv("B2_BUCKET", "")
B2_ENDPOINT     = os.getenv("B2_ENDPOINT", "")   # e.g. https://s3.us-west-004.backblazeb2.com
B2_DB_KEY       = os.getenv("B2_DB_KEY", "uptime.db")
DB_PATH         = os.getenv("DB_PATH", "uptime.db")
SYNC_INTERVAL   = int(os.getenv("B2_SYNC_INTERVAL", "300"))  # seconds between uploads

def _enabled() -> bool:
    return bool(B2_KEY_ID and B2_APP_KEY and B2_BUCKET and B2_ENDPOINT)

def _client():
    import boto3
    return boto3.client(
        "s3",
        endpoint_url=B2_ENDPOINT,
        aws_access_key_id=B2_KEY_ID,
        aws_secret_access_key=B2_APP_KEY,
    )

def _do_restore() -> bool:
    """Download DB from B2 to DB_PATH. Returns True if successful."""
    _client().download_file(B2_BUCKET, B2_DB_KEY, DB_PATH)
    return True

def _do_upload():
    """Upload a consistent snapshot of DB_PATH to B2."""
    src = Path(DB_PATH)
    if not src.exists():
        return
    # Use SQLite's online backup API so we never upload a mid-write file
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        tmp_path = tmp.name
    try:
        src_conn = sqlite3.connect(str(src))
        dst_conn = sqlite3.connect(tmp_path)
        src_conn.backup(dst_conn)
        src_conn.close()
        dst_conn.close()
        _client().upload_file(tmp_path, B2_BUCKET, B2_DB_KEY)
    finally:
        Path(tmp_path).unlink(missing_ok=True)

async def restore_from_b2():
    """Called once at startup — downloads the latest DB from B2."""
    if not _enabled():
        logging.info("[B2] No B2 credentials set, skipping restore")
        return
    loop = asyncio.get_event_loop()
    try:
        await loop.run_in_executor(None, _do_restore)
        logging.info(f"[B2] Database restored from B2 bucket={B2_BUCKET} key={B2_DB_KEY}")
    except Exception as e:
        logging.warning(f"[B2] Restore skipped (starting fresh): {e}")

async def upload_to_b2():
    """Upload current DB snapshot to B2."""
    if not _enabled():
        return
    loop = asyncio.get_event_loop()
    try:
        await loop.run_in_executor(None, _do_upload)
        logging.info("[B2] Database synced to B2")
    except Exception as e:
        logging.warning(f"[B2] Sync failed: {e}")

async def b2_sync_worker():
    """Background task: upload DB to B2 every SYNC_INTERVAL seconds."""
    while True:
        await asyncio.sleep(SYNC_INTERVAL)
        await upload_to_b2()

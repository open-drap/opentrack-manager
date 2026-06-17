try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

from fastapi import FastAPI, Request, Depends, HTTPException, Response
from fastapi.staticfiles import StaticFiles
from fastapi.responses import RedirectResponse, FileResponse
from fastapi.responses import JSONResponse
import asyncio, os, json, base64, secrets, string, hashlib, httpx, uuid, re
from pathlib import Path
from datetime import datetime, UTC, timedelta
from cryptography.fernet import Fernet
from database import init_db
from auth import SECRET, hash_password, verify_password, create_token, create_pin_unlock, verify_pin_unlock, get_current_user, get_current_user_raw
from monitors import monitor_worker, send_telegram, server_alert_worker
from captcha import generate as gen_captcha
from db import _DB
import pyotp, qrcode, io
from urllib.parse import quote

os.makedirs("static", exist_ok=True)

BASE_DIR = Path(__file__).resolve().parent
FRONTEND_DIST = Path(os.getenv("FRONTEND_DIST", BASE_DIR / "Design System Overview" / "dist"))
FRONTEND_INDEX = FRONTEND_DIST / "index.html"
FRONTEND_ASSETS = FRONTEND_DIST / "assets"

app = FastAPI(title="UptimeWatch")
app.mount("/static", StaticFiles(directory="static"), name="static")

if FRONTEND_ASSETS.exists():
    app.mount("/assets", StaticFiles(directory=str(FRONTEND_ASSETS)), name="frontend-assets")

@app.on_event("startup")
async def startup():
    await init_db()
    asyncio.create_task(monitor_worker())
    asyncio.create_task(server_alert_worker())
    from bot import poll_bot
    asyncio.create_task(poll_bot())
    asyncio.create_task(note_reminder_worker())

@app.get("/api/me")
async def get_me(user=Depends(get_current_user)):
    return {"username": user.get("username"), "email": user.get("email")}

def _now():
    return datetime.now(UTC)

def _ts(dt=None):
    return (dt or _now()).strftime("%Y-%m-%d %H:%M:%S")

def _hash_pin(pin: str, salt: str | None = None):
    salt = salt or secrets.token_hex(16)
    digest = hashlib.sha256((salt + pin.strip()).encode()).hexdigest()
    return digest, salt

def _verify_pin(pin: str, salt: str, stored_hash: str) -> bool:
    if not pin or not salt or not stored_hash:
        return False
    digest = hashlib.sha256((salt + pin.strip()).encode()).hexdigest()
    return secrets.compare_digest(digest, stored_hash)

async def audit_log(user_id: int, action: str, detail: str = ""):
    try:
        async with _DB() as db:
            await db.execute(
                "INSERT INTO audit_log (user_id, action, detail) VALUES (?,?,?)",
                [user_id, action, detail[:500]]
            )
            await db.commit()
    except:
        pass

async def get_user_by_id(user_id: int):
    async with _DB() as db:
        return await db.fetchone("SELECT * FROM users WHERE id=?", [user_id])

def _safe_json(obj, fallback=None):
    try:
        return json.loads(obj or "[]")
    except:
        return fallback if fallback is not None else []

def _parse_dt(value):
    if not value:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M"):
        try:
            return datetime.strptime(value, fmt).replace(tzinfo=UTC)
        except:
            pass
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except:
        return None

def calc_security_score(user: dict, monitors=None, vault=None, auths=None):
    monitors = monitors or []
    vault = vault or []
    auths = auths or []
    score = 100
    if not monitors:
        score -= 20
    else:
        down = sum(1 for m in monitors if m.get("status") == "down")
        score -= min(25, down * 8)
        if any(not m.get("is_public") for m in monitors):
            score -= 2
    weak_pw = 0
    old_pw = 0
    dup_pw = 0
    passwords = [v.get("password") for v in vault if v.get("password")]
    seen = set()
    for pw in passwords:
        if len(pw) < 8:
            weak_pw += 1
        if pw in seen:
            dup_pw += 1
        seen.add(pw)
    for v in vault:
        dt = _parse_dt(v.get("password_changed_at"))
        if dt and (_now() - dt).days >= 180:
            old_pw += 1
    no_2fa = sum(1 for v in vault if v.get("password") and not any(a.get("name", "").lower() == (v.get("name") or "").lower() for a in auths))
    score -= min(20, weak_pw * 6)
    score -= min(10, old_pw * 3)
    score -= min(10, dup_pw * 5)
    score -= min(15, no_2fa * 3)
    return max(0, min(100, score))

def security_label(score: int):
    if score >= 85:
        return "Strong"
    if score >= 65:
        return "Good"
    if score >= 45:
        return "Warning"
    return "Critical"

def _frontend_response():
    if not FRONTEND_INDEX.exists():
        raise HTTPException(status_code=500, detail="Frontend build not found")
    return FileResponse(str(FRONTEND_INDEX))

@app.get("/api/captcha")
async def captcha():
    return gen_captcha()

@app.get("/")
async def home():
    return _frontend_response()

@app.get("/login")
async def login_page():
    return _frontend_response()

@app.get("/register")
async def register_page():
    return _frontend_response()

@app.post("/api/register")
async def register(request: Request):
    data = await request.json()
    username = (data.get("username") or "").strip()
    email = (data.get("email") or "").strip()
    password = (data.get("password") or "").strip()
    honeypot = data.get("honeypot") or ""
    if not username or not email or not password:
        return {"ok": False, "error": "All fields required"}
    if honeypot:
        return {"ok": False, "error": "Request blocked"}
    try:
        async with _DB() as db:
            await db.execute(
                "INSERT INTO users (username, email, password) VALUES (?,?,?)",
                [username, email, hash_password(password)]
            )
            await db.commit()
        async with _DB() as db:
            user = await db.fetchone("SELECT id FROM users WHERE username=?", [username])
        if user:
            await audit_log(user["id"], "register", f"account created: {username}")
        return {"ok": True}
    except Exception as e:
        err = str(e)
        if "UNIQUE" in err or "duplicate" in err.lower():
            return {"ok": False, "error": "Username or email already exists"}
        return {"ok": False, "error": f"Registration failed: {err}"}

@app.post("/api/login")
async def login(request: Request):
    data = await request.json()
    username = data.get("username", "").strip()
    password = data.get("password", "").strip()
    honeypot = data.get("honeypot") or ""
    if honeypot:
        return {"ok": False, "error": "Request blocked"}
    async with _DB() as db:
        user = await db.fetchone("SELECT * FROM users WHERE username=? OR email=?", [username, username])
    if not user or not verify_password(password, user["password"]):
        return {"ok": False, "error": "Invalid credentials"}
    token = create_token(user["id"], user["username"])
    resp = Response(content='{"ok":true}', media_type="application/json")
    resp.set_cookie("token", token, httponly=True, max_age=604800)
    await audit_log(user["id"], "login", f"username={username}")
    return resp

@app.get("/logout")
async def logout(request: Request):
    response = RedirectResponse("/")
    response.delete_cookie("token")
    token = request.cookies.get("token")
    try:
        from jose import jwt
        payload = jwt.decode(token or "", SECRET, algorithms=["HS256"])
        await audit_log(int(payload.get("sub")), "logout", "")
    except:
        pass
    return response

@app.get("/dashboard")
async def dashboard():
    return _frontend_response()

@app.get("/status/{username}")
async def status_page(username: str):
    return _frontend_response()

@app.get("/servers")
async def servers_page():
    return _frontend_response()

@app.get("/incidents")
async def incidents_page():
    return _frontend_response()

@app.get("/vault")
async def vault_page():
    return _frontend_response()

@app.get("/authenticator")
async def authenticator_page():
    return _frontend_response()

@app.get("/analytics")
async def analytics_page():
    return _frontend_response()

@app.get("/notes")
async def notes_page():
    return _frontend_response()

@app.get("/lock")
async def lock_page():
    return _frontend_response()

@app.get("/monitors")
async def monitors_page():
    return _frontend_response()

@app.get("/activity")
async def activity_page():
    return _frontend_response()

@app.get("/settings")
async def settings_page():
    return _frontend_response()

@app.get("/profile")
async def profile_page():
    return _frontend_response()

@app.get("/projects")
async def projects_page():
    return _frontend_response()

@app.get("/monitoring")
async def monitoring_page():
    return _frontend_response()

@app.get("/api/status/{username}")
async def public_status(username: str):
    async with _DB() as db:
        u = await db.fetchone("SELECT id FROM users WHERE username=?", [username])
        if not u:
            raise HTTPException(404)
        monitors = await db.fetchall(
            "SELECT * FROM monitors WHERE user_id=? AND is_public=1", [u["id"]]
        )
    return {"username": username, "monitors": monitors}

@app.get("/api/security/summary")
async def security_summary(user=Depends(get_current_user)):
    async with _DB() as db:
        monitors = await db.fetchall("SELECT * FROM monitors WHERE user_id=?", [user["id"]])
        vault = await db.fetchall("SELECT * FROM vault WHERE user_id=?", [user["id"]])
        auths = await db.fetchall("SELECT * FROM authenticator WHERE user_id=?", [user["id"]])
    f = get_vault_key(user)
    for r in vault:
        r["password"] = decrypt_val(f, r.get("password",""))
        r["api_key"] = decrypt_val(f, r.get("api_key",""))
    score = calc_security_score(user, monitors, vault, auths)
    weak = sum(1 for r in vault if r.get("password") and len(r["password"]) < 8)
    old = sum(1 for r in vault if _parse_dt(r.get("password_changed_at")) and (_now() - _parse_dt(r.get("password_changed_at"))).days >= 180)
    dup = len(vault) - len({r.get("password") for r in vault if r.get("password")}) if vault else 0
    no_2fa = sum(1 for r in vault if r.get("password") and not any(a.get("name","").lower() == (r.get("name") or "").lower() for a in auths))
    return {
        "score": score,
        "label": security_label(score),
        "monitors": len(monitors),
        "down_monitors": sum(1 for m in monitors if m.get("status") == "down"),
        "vault_entries": len(vault),
        "weak_passwords": weak,
        "old_passwords": old,
        "duplicate_passwords": dup,
        "missing_2fa": no_2fa,
        "authenticator_accounts": len(auths),
    }

@app.get("/api/security/alerts")
async def security_alerts(user=Depends(get_current_user)):
    s = await security_summary(user)
    alerts = []
    if s["down_monitors"]:
        alerts.append({"type": "danger", "title": "Downtime detected", "detail": f"{s['down_monitors']} monitor(s) are down"})
    if s["weak_passwords"]:
        alerts.append({"type": "warning", "title": "Weak passwords", "detail": f"{s['weak_passwords']} password(s) are shorter than 8 characters"})
    if s["missing_2fa"]:
        alerts.append({"type": "warning", "title": "2FA gap", "detail": f"{s['missing_2fa']} vault item(s) do not have a matching authenticator entry"})
    if not alerts:
        alerts.append({"type": "success", "title": "Security baseline looks good", "detail": "No critical issues found in the latest scan"})
    return alerts

@app.get("/api/activity")
async def activity_feed(user=Depends(get_current_user)):
    async with _DB() as db:
        audits = await db.fetchall(
            "SELECT action, detail, created_at FROM audit_log WHERE user_id=? ORDER BY id DESC LIMIT 100",
            [user["id"]]
        )
        incidents = await db.fetchall(
            """SELECT 'incident' AS action,
                      COALESCE(m.name, m.url, 'Monitor') || ' incident' AS detail,
                      COALESCE(i.ended_at, i.started_at) AS created_at
               FROM incidents i JOIN monitors m ON i.monitor_id=m.id
               WHERE m.user_id=?
               ORDER BY i.id DESC LIMIT 100""",
            [user["id"]]
        )
    items = audits + incidents
    items.sort(key=lambda x: x.get("created_at") or "", reverse=True)
    return items[:100]

@app.get("/api/security/health")
async def health_score(user=Depends(get_current_user)):
    summary = await security_summary(user)
    await audit_log(user["id"], "health_score", f"score={summary['score']}")
    return summary

@app.post("/api/security/pin/setup")
async def setup_pin(request: Request, user=Depends(get_current_user)):
    data = await request.json()
    pin = (data.get("pin") or "").strip()
    confirm = (data.get("confirm") or "").strip()
    if not pin or pin != confirm:
        return {"ok": False, "error": "PINs do not match"}
    if len(pin) < 4:
        return {"ok": False, "error": "PIN too short"}
    pin_hash, salt = _hash_pin(pin)
    async with _DB() as db:
        await db.execute(
            "UPDATE users SET master_pin_hash=?, master_pin_salt=?, pin_enabled=1, pin_timeout_minutes=? WHERE id=?",
            [pin_hash, salt, int(data.get("timeout", 5)) or 5, user["id"]]
        )
        await db.commit()
    await audit_log(user["id"], "pin_setup", "master pin enabled")
    return {"ok": True}

@app.post("/api/security/pin/verify")
async def verify_pin(request: Request, user=Depends(get_current_user_raw)):
    data = await request.json()
    pin = (data.get("pin") or "").strip()
    if not user.get("pin_enabled"):
        return {"ok": True, "unlocked": True}
    if not _verify_pin(pin, user.get("master_pin_salt",""), user.get("master_pin_hash","")):
        return {"ok": False, "error": "Invalid PIN"}
    minutes = int(user.get("pin_timeout_minutes") or 5)
    unlock = create_pin_unlock(user["id"], minutes)
    resp = JSONResponse({"ok": True, "unlocked": True, "timeout": minutes})
    resp.set_cookie("pin_unlock", unlock, httponly=True, max_age=minutes*60)
    await audit_log(user["id"], "pin_unlock", f"timeout={minutes}")
    return resp

@app.post("/api/security/backup-codes")
async def generate_backup_codes(user=Depends(get_current_user)):
    codes = [secrets.token_hex(4).upper() for _ in range(8)]
    async with _DB() as db:
        await db.execute("DELETE FROM backup_codes WHERE user_id=?", [user["id"]])
        for code in codes:
            code_hash = hashlib.sha256(code.encode()).hexdigest()
            await db.execute("INSERT INTO backup_codes (user_id, code_hash) VALUES (?,?)", [user["id"], code_hash])
        await db.commit()
    await audit_log(user["id"], "backup_codes_generate", "8 codes generated")
    return {"ok": True, "codes": codes}

@app.post("/api/security/backup-codes/verify")
async def verify_backup_code(request: Request, user=Depends(get_current_user_raw)):
    data = await request.json()
    code = (data.get("code") or "").strip().upper()
    code_hash = hashlib.sha256(code.encode()).hexdigest()
    async with _DB() as db:
        row = await db.fetchone(
            "SELECT id FROM backup_codes WHERE user_id=? AND code_hash=? AND used_at IS NULL",
            [user["id"], code_hash]
        )
        if not row:
            return {"ok": False, "error": "Invalid backup code"}
        await db.execute("UPDATE backup_codes SET used_at=? WHERE id=?", [_ts(), row["id"]])
        await db.commit()
    unlock = create_pin_unlock(user["id"], int(user.get("pin_timeout_minutes") or 5))
    resp = JSONResponse({"ok": True})
    resp.set_cookie("pin_unlock", unlock, httponly=True, max_age=int(user.get("pin_timeout_minutes") or 5) * 60)
    await audit_log(user["id"], "backup_code_use", "")
    return resp

@app.get("/api/security/export")
async def export_backup(user=Depends(get_current_user)):
    f = get_vault_key(user)
    async with _DB() as db:
        vault = await db.fetchall("SELECT * FROM vault WHERE user_id=?", [user["id"]])
        auths = await db.fetchall("SELECT * FROM authenticator WHERE user_id=?", [user["id"]])
    for v in vault:
        v["password"] = decrypt_val(f, v.get("password",""))
        v["api_key"] = decrypt_val(f, v.get("api_key",""))
    payload = {"version": 1, "exported_at": _ts(), "vault": vault, "authenticator": auths}
    await audit_log(user["id"], "export", "security export generated")
    return JSONResponse(payload)

@app.get("/api/backup/db")
async def download_db(user=Depends(get_current_user)):
    await audit_log(user["id"], "db_backup_download", "database")
    import tempfile
    if os.getenv("DB_DRIVER", "sqlite") == "postgres":
        return {"ok": False, "error": "DB download only available for SQLite"}
    return FileResponse(os.getenv("DB_PATH", "uptime.db"), filename="uptime.db", media_type="application/x-sqlite3")

@app.post("/api/security/import")
async def import_backup(request: Request, user=Depends(get_current_user)):
    data = await request.json()
    f = get_vault_key(user)
    vault = data.get("vault", [])
    auths = data.get("authenticator", [])
    async with _DB() as db:
        for item in vault:
            name = (item.get("name") or "").strip()
            if not name:
                continue
            pw = encrypt_val(f, item.get("password",""))
            ak = encrypt_val(f, item.get("api_key",""))
            await db.execute(
                "INSERT INTO vault (user_id, name, url, username, password, api_key, notes, category, password_changed_at, favorite) VALUES (?,?,?,?,?,?,?,?,?,?)",
                [user["id"], name, item.get("url",""), item.get("username",""), pw, ak, item.get("notes",""), item.get("category",""), item.get("password_changed_at",""), 1 if item.get("favorite") else 0]
            )
        for item in auths:
            name = (item.get("name") or "").strip()
            secret = (item.get("secret") or "").strip()
            if not name or not secret:
                continue
            await db.execute(
                "INSERT INTO authenticator (user_id, name, issuer, secret, algorithm, digits, period, category, favorite) VALUES (?,?,?,?,?,?,?,?,?)",
                [user["id"], name, item.get("issuer",""), secret, item.get("algorithm","SHA1"), item.get("digits",6), item.get("period",30), item.get("category",""), 1 if item.get("favorite") else 0]
            )
        await db.commit()
    await audit_log(user["id"], "import", f"vault={len(vault)} auth={len(auths)}")
    return {"ok": True, "imported_vault": len(vault), "imported_authenticator": len(auths)}

@app.get("/api/authenticator/export")
async def export_authenticator(user=Depends(get_current_user)):
    return await export_backup(user)

@app.post("/api/authenticator/import")
async def import_authenticator(request: Request, user=Depends(get_current_user)):
    return await import_backup(request, user)

@app.get("/api/users/search")
async def search_users(q: str = "", user=Depends(get_current_user)):
    if len(q.strip()) < 2:
        return []
    async with _DB() as db:
        rows = await db.fetchall(
            "SELECT id, username FROM users WHERE username LIKE ? AND id != ? LIMIT 10",
            [f"%{q.strip()}%", user["id"]]
        )
    return [{"id": r["id"], "username": r["username"]} for r in rows]

@app.post("/api/vault/{vid}/share-to-user")
async def share_vault_to_user(vid: int, request: Request, user=Depends(get_current_user)):
    data = await request.json()
    target_username = (data.get("username") or "").strip()
    if not target_username:
        return {"ok": False, "error": "Username required"}
    async with _DB() as db:
        item = await db.fetchone("SELECT * FROM vault WHERE id=? AND user_id=?", [vid, user["id"]])
        if not item:
            return {"ok": False, "error": "Item not found"}
        target = await db.fetchone("SELECT * FROM users WHERE username=?", [target_username])
        if not target:
            return {"ok": False, "error": "User not found"}
        if target["id"] == user["id"]:
            return {"ok": False, "error": "Cannot share with yourself"}
    f_src = get_vault_key(dict(user))
    password = decrypt_val(f_src, item.get("password", ""))
    api_key_val = decrypt_val(f_src, item.get("api_key", ""))
    target_dict = dict(target)
    f_tgt = get_vault_key(target_dict)
    pw_enc = encrypt_val(f_tgt, password)
    ak_enc = encrypt_val(f_tgt, api_key_val)
    async with _DB() as db:
        await db.execute("""
            INSERT INTO vault (user_id, name, url, username, password, api_key, notes, category, password_changed_at, favorite)
            VALUES (?,?,?,?,?,?,?,?,?,?)
        """, [
            target["id"],
            f"[Shared] {item.get('name', '')}",
            item.get("url", ""),
            item.get("username", ""),
            pw_enc, ak_enc,
            f"Shared by {user['username']}. {item.get('notes', '')}".strip(". "),
            item.get("category", ""),
            _ts(), 0
        ])
        await db.commit()
    await audit_log(user["id"], "vault_share_user", f"vid={vid} to={target_username}")
    return {"ok": True}

@app.post("/api/vault/{vid}/share")
async def create_share_link(vid: int, request: Request, user=Depends(get_current_user)):
    data = await request.json()
    hours = max(1, min(int(data.get("hours", 24)), 168))
    token = secrets.token_urlsafe(24)
    expires = _now() + timedelta(hours=hours)
    async with _DB() as db:
        row = await db.fetchone("SELECT id FROM vault WHERE id=? AND user_id=?", [vid, user["id"]])
        if not row:
            return {"ok": False, "error": "Not found"}
        await db.execute(
            "INSERT INTO share_links (user_id, vault_id, token, expires_at) VALUES (?,?,?,?)",
            [user["id"], vid, token, _ts(expires)]
        )
        await db.commit()
    await audit_log(user["id"], "share_create", f"vault_id={vid} hours={hours}")
    return {"ok": True, "url": f"/share/{token}", "expires_at": _ts(expires)}

@app.get("/share/{token}")
async def public_share(token: str):
    async with _DB() as db:
        row = await db.fetchone("""
            SELECT s.*, v.name, v.url, v.username, v.password, v.api_key, v.notes, v.category
            FROM share_links s JOIN vault v ON s.vault_id=v.id
            WHERE s.token=?
        """, [token])
        if not row:
            raise HTTPException(404)
        expires = _parse_dt(row["expires_at"])
        if expires and expires < _now():
            raise HTTPException(410)
    return {
        "name": row["name"], "url": row["url"], "username": row["username"],
        "notes": row["notes"], "category": row["category"], "expires_at": row["expires_at"],
    }

@app.post("/api/emergency/request")
async def request_emergency_access(request: Request, user=Depends(get_current_user)):
    data = await request.json()
    trusted = (data.get("trusted_contact") or "").strip()
    note = (data.get("note") or "").strip()
    async with _DB() as db:
        await db.execute(
            "INSERT INTO emergency_requests (user_id, trusted_contact, note) VALUES (?,?,?)",
            [user["id"], trusted, note]
        )
        await db.commit()
    await audit_log(user["id"], "emergency_request", trusted)
    return {"ok": True}

@app.get("/api/emergency")
async def list_emergency_requests(user=Depends(get_current_user)):
    async with _DB() as db:
        return await db.fetchall("SELECT * FROM emergency_requests WHERE user_id=? ORDER BY id DESC", [user["id"]])

@app.post("/api/emergency/{rid}/approve")
async def approve_emergency(rid: int, user=Depends(get_current_user)):
    available = _now() + timedelta(hours=24)
    async with _DB() as db:
        await db.execute(
            "UPDATE emergency_requests SET status='approved', approved_at=?, available_at=? WHERE id=? AND user_id=?",
            [_ts(), _ts(available), rid, user["id"]]
        )
        await db.commit()
    await audit_log(user["id"], "emergency_approve", f"id={rid}")
    return {"ok": True, "available_at": _ts(available)}

@app.post("/api/emergency/{rid}/redeem")
async def redeem_emergency(rid: int, user=Depends(get_current_user_raw)):
    async with _DB() as db:
        row = await db.fetchone("SELECT * FROM emergency_requests WHERE id=? AND user_id=?", [rid, user["id"]])
        if not row:
            return {"ok": False, "error": "Not found"}
        available = _parse_dt(row["available_at"])
        if row["status"] != "approved":
            return {"ok": False, "error": "Not approved yet"}
        if available and available > _now():
            return {"ok": False, "error": "Access not yet available"}
    unlock = create_pin_unlock(user["id"], int(user.get("pin_timeout_minutes") or 5))
    resp = JSONResponse({"ok": True})
    resp.set_cookie("pin_unlock", unlock, httponly=True, max_age=int(user.get("pin_timeout_minutes") or 5) * 60)
    await audit_log(user["id"], "emergency_redeem", f"id={rid}")
    return resp

@app.get("/api/bot-info")
async def bot_info():
    from bot import get_bot_username, BOT_TOKEN
    if not BOT_TOKEN:
        return {"username": None, "enabled": False}
    uname = await get_bot_username()
    return {"username": uname, "enabled": True}

@app.get("/api/settings")
async def get_settings(user=Depends(get_current_user)):
    return {
        "telegram_chat_id": user.get("telegram_chat_id",""),
        "pin_enabled": bool(user.get("pin_enabled")),
        "pin_timeout_minutes": int(user.get("pin_timeout_minutes") or 5),
    }

@app.post("/api/settings")
async def save_settings(request: Request, user=Depends(get_current_user)):
    data = await request.json()
    new_ci = data.get("telegram_chat_id")
    pin_enabled = data.get("pin_enabled")
    pin_timeout = data.get("pin_timeout_minutes")
    
    updates = []
    params = []
    if new_ci is not None:
        updates.append("telegram_chat_id=?")
        params.append(new_ci)
    if pin_enabled is not None:
        updates.append("pin_enabled=?")
        params.append(1 if pin_enabled else 0)
    if pin_timeout is not None:
        updates.append("pin_timeout_minutes=?")
        params.append(int(pin_timeout))
        
    if not updates:
        return {"ok": True}
        
    params.append(user["id"])
    async with _DB() as db:
        await db.execute(f"UPDATE users SET {', '.join(updates)} WHERE id=?", params)
        await db.commit()
    await audit_log(user["id"], "settings_update", "notification settings saved")
    return {"ok": True}

@app.post("/api/telegram/test")
async def test_telegram(user=Depends(get_current_user)):
    from bot import BOT_TOKEN
    if not user.get("telegram_chat_id"):
        return {"ok": False, "error": "No chat ID linked. Send /start yourusername to the bot first."}
    await send_telegram(BOT_TOKEN, user["telegram_chat_id"],
        "✅ *UptimeWatch*\n🔔 Telegram alerts are working!")
    await audit_log(user["id"], "telegram_test", "")
    return {"ok": True}

@app.post("/api/notify/test")
async def test_notify(user=Depends(get_current_user)):
    from monitors import notify_all
    await notify_all(user, "✅ *UptimeWatch*\n🔔 Notification test — all channels!")
    await audit_log(user["id"], "notify_test", "all channels")
    return {"ok": True}

@app.post("/api/telegram/send-scheduled")
async def send_scheduled_telegram(request: Request, user=Depends(get_current_user)):
    data = await request.json()
    message = (data.get("message") or "").strip()
    if not message:
        return {"ok": False, "error": "Message required"}
    from bot import BOT_TOKEN
    if not user.get("telegram_chat_id"):
        return {"ok": False, "error": "No chat ID linked"}
    await send_telegram(BOT_TOKEN, user["telegram_chat_id"], f"⏰ *Scheduled Alert*\n\n{message}")
    await audit_log(user["id"], "telegram_scheduled", message[:100])
    return {"ok": True}

@app.get("/api/notes")
async def get_notes(user=Depends(get_current_user)):
    async with _DB() as db:
        return await db.fetchall(
            "SELECT * FROM notes WHERE user_id=? ORDER BY reminder_at ASC, id DESC", [user["id"]]
        )

@app.post("/api/notes")
async def create_note(request: Request, user=Depends(get_current_user)):
    data = await request.json()
    title = (data.get("title") or "").strip()
    content = (data.get("content") or "").strip()
    reminder_at = (data.get("reminder_at") or "").strip()
    if not title:
        return {"ok": False, "error": "Title required"}
    async with _DB() as db:
        await db.execute(
            "INSERT INTO notes (user_id, title, content, reminder_at) VALUES (?,?,?,?)",
            [user["id"], title, content, reminder_at or None]
        )
        await db.commit()
    await audit_log(user["id"], "note_create", title)
    return {"ok": True}

@app.put("/api/notes/{note_id}")
async def update_note(note_id: int, request: Request, user=Depends(get_current_user)):
    data = await request.json()
    title = (data.get("title") or "").strip()
    content = (data.get("content") or "").strip()
    reminder_at = (data.get("reminder_at") or "").strip()
    if not title:
        return {"ok": False, "error": "Title required"}
    now = _ts()
    async with _DB() as db:
        await db.execute(
            "UPDATE notes SET title=?, content=?, reminder_at=?, updated_at=?, reminder_sent=0 WHERE id=? AND user_id=?",
            [title, content, reminder_at or None, now, note_id, user["id"]]
        )
        await db.commit()
    await audit_log(user["id"], "note_update", title)
    return {"ok": True}

@app.delete("/api/notes/{note_id}")
async def delete_note(note_id: int, user=Depends(get_current_user)):
    async with _DB() as db:
        await db.execute("DELETE FROM notes WHERE id=? AND user_id=?", [note_id, user["id"]])
        await db.commit()
    await audit_log(user["id"], "note_delete", f"id={note_id}")
    return {"ok": True}

async def note_reminder_worker():
    while True:
        try:
            async with _DB() as db:
                due = await db.fetchall("""
                    SELECT n.*, u.telegram_chat_id FROM notes n
                    JOIN users u ON n.user_id=u.id
                    WHERE n.reminder_at IS NOT NULL
                    AND n.reminder_sent=0
                    AND n.reminder_at <= ?
                """, [_ts()])
            for note in due:
                if note.get("telegram_chat_id"):
                    from bot import BOT_TOKEN
                    msg = f"📝 *Note Reminder*\n\n*{note['title']}*\n{note['content']}"
                    await send_telegram(BOT_TOKEN, note["telegram_chat_id"], msg)
                async with _DB() as db:
                    await db.execute("UPDATE notes SET reminder_sent=1 WHERE id=?", [note["id"]])
                    await db.commit()
        except:
            pass
        await asyncio.sleep(30)

@app.get("/api/monitors")
async def get_monitors(user=Depends(get_current_user)):
    async with _DB() as db:
        return await db.fetchall("SELECT * FROM monitors WHERE user_id=? ORDER BY id DESC", [user["id"]])

@app.post("/api/monitors")
async def add_monitor(request: Request, user=Depends(get_current_user)):
    data = await request.json()
    url = data.get("url","").strip()
    if not url:
        return {"ok": False, "error": "URL required"}
    if not url.startswith("http") and data.get("type","http") in ("http","ssl"):
        url = "https://" + url
    headers = data.get("custom_headers", {})
    tags = data.get("tags", "")
    if isinstance(tags, list):
        tags = ",".join(tags)
    async with _DB() as db:
        await db.execute("""
            INSERT INTO monitors (user_id, name, url, type, keyword, port, interval, is_public,
                custom_headers, method, expected_status, maintenance_from, maintenance_to, tags)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, [
            user["id"], data.get("name", url), url, data.get("type","http"),
            data.get("keyword",""), data.get("port", 0), data.get("interval", 60),
            1 if data.get("is_public") else 0,
            json.dumps(headers), data.get("method","GET"), data.get("expected_status", 0),
            data.get("maintenance_from",""), data.get("maintenance_to",""),
            tags,
        ])
        await db.commit()
    await audit_log(user["id"], "monitor_add", url)
    return {"ok": True}

@app.put("/api/monitors/{monitor_id}")
async def update_monitor(monitor_id: int, request: Request, user=Depends(get_current_user)):
    data = await request.json()
    headers = data.get("custom_headers", {})
    tags = data.get("tags", "")
    if isinstance(tags, list):
        tags = ",".join(tags)
    async with _DB() as db:
        await db.execute("""
            UPDATE monitors SET name=?, url=?, type=?, keyword=?, port=?, interval=?,
            is_public=?, custom_headers=?, method=?, expected_status=?,
            maintenance_from=?, maintenance_to=?, tags=?
            WHERE id=? AND user_id=?
        """, [
            data.get("name",""), data.get("url",""), data.get("type","http"),
            data.get("keyword",""), data.get("port",0), data.get("interval",60),
            1 if data.get("is_public") else 0, json.dumps(headers),
            data.get("method","GET"), data.get("expected_status",0),
            data.get("maintenance_from",""), data.get("maintenance_to",""),
            tags, monitor_id, user["id"],
        ])
        await db.commit()
    await audit_log(user["id"], "monitor_update", f"id={monitor_id}")
    return {"ok": True}

@app.delete("/api/monitors/{monitor_id}")
async def delete_monitor(monitor_id: int, user=Depends(get_current_user)):
    async with _DB() as db:
        await db.execute("DELETE FROM incidents WHERE monitor_id=?", [monitor_id])
        await db.execute("DELETE FROM monitors WHERE id=? AND user_id=?", [monitor_id, user["id"]])
        await db.commit()
    await audit_log(user["id"], "monitor_delete", f"id={monitor_id}")
    return {"ok": True}

@app.post("/api/check-now/{monitor_id}")
async def check_now(monitor_id: int, user=Depends(get_current_user)):
    from monitors import process_monitor
    async with _DB() as db:
        m = await db.fetchone("""
            SELECT m.*, u.telegram_chat_id
            FROM monitors m JOIN users u ON m.user_id=u.id WHERE m.id=? AND m.user_id=?
        """, [monitor_id, user["id"]])
    if m:
        asyncio.create_task(process_monitor(dict(m)))
        await audit_log(user["id"], "monitor_check_now", f"id={monitor_id}")
    return {"ok": True}

@app.post("/api/servers")
async def add_server(request: Request, user=Depends(get_current_user)):
    data = await request.json()
    hostname = (data.get("hostname") or "").strip()
    if not hostname:
        return {"ok": False, "error": "Hostname required"}
    token = "sk_live_" + secrets.token_hex(24)
    async with _DB() as db:
        await db.execute(
            "INSERT INTO servers (user_id, hostname, token) VALUES (?,?,?)",
            [user["id"], hostname, token]
        )
        await db.commit()
        row = await db.fetchone("SELECT id, token FROM servers WHERE user_id=? ORDER BY id DESC", [user["id"]])
    await audit_log(user["id"], "server_add", hostname)
    return {"ok": True, "id": row["id"], "token": row["token"]}


@app.get("/api/servers")
async def list_servers(user=Depends(get_current_user)):
    async with _DB() as db:
        rows = await db.fetchall("SELECT * FROM servers WHERE user_id=? ORDER BY id DESC", [user["id"]])
    for r in rows:
        dt = _parse_dt(r["last_heartbeat"])
        r["is_online"] = bool(dt and (_now() - dt).total_seconds() < 120)
    return rows


@app.get("/api/servers/{server_id}/projects")
async def get_server_projects(server_id: int, user=Depends(get_current_user)):
    async with _DB() as db:
        row = await db.fetchone("SELECT projects FROM servers WHERE id=? AND user_id=?", [server_id, user["id"]])
    if not row:
        raise HTTPException(404, "Not found")
    return _safe_json(row.get("projects", "[]"), [])


@app.delete("/api/servers/{server_id}")
async def delete_server(server_id: int, user=Depends(get_current_user)):
    async with _DB() as db:
        await db.execute("DELETE FROM servers WHERE id=? AND user_id=?", [server_id, user["id"]])
        await db.commit()
    await audit_log(user["id"], "server_delete", f"id={server_id}")
    return {"ok": True}


@app.post("/api/servers/{server_id}/token")
async def regenerate_token(server_id: int, user=Depends(get_current_user)):
    token = "sk_live_" + secrets.token_hex(24)
    async with _DB() as db:
        await db.execute("UPDATE servers SET token=? WHERE id=? AND user_id=?", [token, server_id, user["id"]])
        await db.commit()
    await audit_log(user["id"], "server_token_regenerate", f"id={server_id}")
    return {"ok": True, "token": token}


@app.post("/api/metrics")
async def receive_metrics(request: Request):
    auth = request.headers.get("Authorization", "").replace("Bearer ", "").strip()
    if not auth:
        raise HTTPException(401, detail="Missing token")
    async with _DB() as db:
        server = await db.fetchone("SELECT id FROM servers WHERE token=?", [auth])
        if not server:
            raise HTTPException(401, detail="Invalid token")
        data = await request.json()
        hostname = (data.get("hostname") or "").strip()
        cpu = float(data.get("cpu", 0))
        ram = float(data.get("ram", 0))
        disk = float(data.get("disk", 0))
        ip = (data.get("ip") or "").strip()
        uptime = (data.get("uptime") or "").strip()
        load_avg = (data.get("load_avg") or "").strip()
        os_info = (data.get("os_info") or "").strip()
        docker_count = int(data.get("docker_count", 0))
        nginx_status = (data.get("nginx_status") or "").strip()
        db_mysql = (data.get("db_mysql") or "").strip()
        db_postgres = (data.get("db_postgres") or "").strip()
        db_redis = (data.get("db_redis") or "").strip()
        bw_rx = float(data.get("bw_rx", 0))
        bw_tx = float(data.get("bw_tx", 0))
        latency = float(data.get("latency", 0))
        disk_growth = float(data.get("disk_growth", 0))
        svc_count = int(data.get("svc_count", 0))
        proj_count = int(data.get("proj_count", 0))
        projects = json.dumps(data.get("projects", [])[:20])
        now = _ts()
        await db.execute(
            """UPDATE servers SET hostname=?, cpu=?, ram=?, disk=?, last_heartbeat=?,
               ip=?, uptime=?, load_avg=?, os_info=?, docker_count=?,
               nginx_status=?, db_mysql=?, db_postgres=?, db_redis=?,
               bw_rx=?, bw_tx=?, latency=?, disk_growth=?, svc_count=?, proj_count=?, projects=?
               WHERE id=?""",
            [hostname, cpu, ram, disk, now,
             ip, uptime, load_avg, os_info, docker_count,
             nginx_status, db_mysql, db_postgres, db_redis,
             bw_rx, bw_tx, latency, disk_growth, svc_count, proj_count, projects,
             server["id"]]
        )
        await db.commit()
    return {"ok": True}


INSTALL_SH = r'''#!/bin/bash
TOKEN=$1
if [ -z "$TOKEN" ]; then
  echo "Usage: curl -sSL https://opentrack-manager.hf.space/install.sh | bash -s YOUR_TOKEN"
  exit 1
fi
mkdir -p ~/.monitor_agent
curl -sSL -o ~/.monitor_agent/monitor.sh https://opentrack-manager.hf.space/monitor.sh
chmod +x ~/.monitor_agent/monitor.sh
echo "$TOKEN" > ~/.monitor_agent/token.txt
nohup ~/.monitor_agent/monitor.sh > /dev/null 2>&1 &
echo "UptimeWatch agent installed and running."
'''

MONITOR_SH = r'''#!/bin/bash
TOKEN=$(cat ~/.monitor_agent/token.txt 2>/dev/null)
[ -z "$TOKEN" ] && exit 1

BASE_URL="https://opentrack-manager.hf.space"
LAST_DISK_FILE="/tmp/.uw_last_disk"
SCRIPT_PATH="$(realpath "$0" 2>/dev/null || echo "$0")"

esc_json() { printf '%s' "$1" | sed 's/\\/\\\\/g; s/"/\\"/g' | tr -d '\r\n\t'; }

get_cpu() {
  c=$(top -bn1 2>/dev/null | awk '/^(%Cpu|Cpu\(s\))/{gsub(/,/," "); for(i=1;i<=NF;i++) if($i~/^[0-9]/ && $(i+1)~/us/) {printf "%d", $i; exit}}')
  [ -z "$c" ] && c=$(vmstat 1 2 2>/dev/null | awk 'NR==4{print 100-$15}')
  printf '%d' "${c:-0}"
}

svc_check() {
  systemctl is-active --quiet "$1" 2>/dev/null && echo "active" && return
  pgrep -x "$1" >/dev/null 2>&1 && echo "active" && return
  echo "inactive"
}

while true; do
  CPU=$(get_cpu)
  RAM=$(free 2>/dev/null | awk '/Mem:/{printf "%.0f", $3/$2*100}'); RAM="${RAM:-0}"
  DISK=$(df / 2>/dev/null | awk 'END{gsub(/%/,""); print $5}'); DISK="${DISK:-0}"
  HOST=$(hostname 2>/dev/null || echo "unknown")

  # IP
  IP=$(hostname -I 2>/dev/null | awk '{print $1}')
  [ -z "$IP" ] && IP=$(ip route get 1 2>/dev/null | awk '{for(i=1;i<=NF;i++) if($i=="src") print $(i+1)}')
  IP="${IP:-unknown}"

  # Uptime
  UPTIME=$(uptime -p 2>/dev/null)
  [ -z "$UPTIME" ] && UPTIME=$(awk '{d=int($1/86400);h=int(($1%86400)/3600);m=int(($1%3600)/60); printf "%dd %dh %dm",d,h,m}' /proc/uptime 2>/dev/null)
  UPTIME="${UPTIME:-unknown}"

  # Load average
  LOAD=$(awk '{print $1" "$2" "$3}' /proc/loadavg 2>/dev/null); LOAD="${LOAD:-0 0 0}"

  # OS info
  OS=$(grep PRETTY_NAME /etc/os-release 2>/dev/null | cut -d= -f2 | tr -d '"')
  [ -z "$OS" ] && OS=$(uname -s -r 2>/dev/null); OS="${OS:-unknown}"

  # Docker
  DOCKER=0
  command -v docker &>/dev/null && DOCKER=$(docker ps -q 2>/dev/null | wc -l | tr -d ' ')
  DOCKER="${DOCKER:-0}"

  # Services
  NGINX=$(svc_check nginx)
  MYSQL="inactive"
  for s in mysql mysqld mariadb; do [ "$(svc_check $s)" = "active" ] && MYSQL="active" && break; done
  PGRES="inactive"
  for s in postgresql postgres; do [ "$(svc_check $s)" = "active" ] && PGRES="active" && break; done
  REDIS="inactive"
  for s in redis redis-server; do [ "$(svc_check $s)" = "active" ] && REDIS="active" && break; done

  SVC_COUNT=0
  command -v systemctl &>/dev/null && SVC_COUNT=$(systemctl list-units --type=service --state=running 2>/dev/null | grep -c "active running")
  SVC_COUNT="${SVC_COUNT:-0}"

  PROJ=0
  PROJ_JSON="[]"
  if [ -d /var/www ]; then
    _pj=""
    for _d in /var/www/*/; do
      [ -d "$_d" ] || continue
      _n=$(basename "$_d")
      _t="unknown"
      [ -f "$_d/manage.py" ] && _t="django"
      if [ "$_t" = "unknown" ]; then
        { [ -f "$_d/Dockerfile" ] || [ -f "$_d/docker-compose.yml" ] || [ -f "$_d/docker-compose.yaml" ]; } && _t="docker"
      fi
      [ "$_t" = "unknown" ] && [ -f "$_d/requirements.txt" ] && _t="python"
      [ "$_t" = "unknown" ] && [ -f "$_d/package.json" ] && _t="node"
      [ "$_t" = "unknown" ] && [ -f "$_d/composer.json" ] && _t="php"
      [ "$_t" = "unknown" ] && [ -f "$_d/go.mod" ] && _t="go"
      [ "$_t" = "unknown" ] && [ -f "$_d/Gemfile" ] && _t="ruby"
      [ -n "$_pj" ] && _pj="$_pj,"
      _pj="$_pj{\"name\":\"$(esc_json "$_n")\",\"type\":\"$_t\"}"
      PROJ=$((PROJ+1))
    done
    PROJ_JSON="[$_pj]"
  fi
  PROJ="${PROJ:-0}"

  # Bandwidth (1-second sample)
  IFACE=$(ip route 2>/dev/null | awk '/default/{print $5; exit}')
  if [ -n "$IFACE" ] && [ -f /proc/net/dev ]; then
    RX1=$(awk -v i=" $IFACE:" '$0~i{print $2}' /proc/net/dev)
    TX1=$(awk -v i=" $IFACE:" '$0~i{print $10}' /proc/net/dev)
    sleep 1
    RX2=$(awk -v i=" $IFACE:" '$0~i{print $2}' /proc/net/dev)
    TX2=$(awk -v i=" $IFACE:" '$0~i{print $10}' /proc/net/dev)
    BW_RX=$(( ${RX2:-0} - ${RX1:-0} )); [ $BW_RX -lt 0 ] && BW_RX=0
    BW_TX=$(( ${TX2:-0} - ${TX1:-0} )); [ $BW_TX -lt 0 ] && BW_TX=0
  else
    sleep 1; BW_RX=0; BW_TX=0
  fi

  # Latency
  LATENCY=$(ping -c 1 -W 2 8.8.8.8 2>/dev/null | awk -F'time=' '/time=/{print $2}' | awk '{print $1}')
  LATENCY="${LATENCY:-0}"

  # Disk growth (KB since last run)
  DISK_KB=$(df / 2>/dev/null | awk 'END{print $3}'); DISK_KB="${DISK_KB:-0}"
  LAST_KB=$(cat "$LAST_DISK_FILE" 2>/dev/null || echo "$DISK_KB")
  DISK_GROWTH=$(( DISK_KB - LAST_KB )); [ $DISK_GROWTH -lt 0 ] && DISK_GROWTH=0
  echo "$DISK_KB" > "$LAST_DISK_FILE"

  PAYLOAD="{\"hostname\":\"$(esc_json "$HOST")\",\"cpu\":$CPU,\"ram\":$RAM,\"disk\":$DISK,\"ip\":\"$(esc_json "$IP")\",\"uptime\":\"$(esc_json "$UPTIME")\",\"load_avg\":\"$(esc_json "$LOAD")\",\"os_info\":\"$(esc_json "$OS")\",\"docker_count\":$DOCKER,\"nginx_status\":\"$NGINX\",\"db_mysql\":\"$MYSQL\",\"db_postgres\":\"$PGRES\",\"db_redis\":\"$REDIS\",\"bw_rx\":$BW_RX,\"bw_tx\":$BW_TX,\"latency\":$LATENCY,\"disk_growth\":$DISK_GROWTH,\"svc_count\":$SVC_COUNT,\"proj_count\":$PROJ,\"projects\":$PROJ_JSON}"

  HTTP_CODE=$(curl -sSL -w "%{http_code}" -o /dev/null -X POST "$BASE_URL/api/metrics" \
    -H "Authorization: Bearer $TOKEN" \
    -H "Content-Type: application/json" \
    -d "$PAYLOAD" 2>/dev/null)

  if [ "$HTTP_CODE" = "401" ] || [ "$HTTP_CODE" = "410" ]; then
    # Server was deleted from dashboard — self-uninstall
    systemctl stop opentrack-agent 2>/dev/null || true
    systemctl disable opentrack-agent 2>/dev/null || true
    rm -f /etc/systemd/system/opentrack-agent.service
    systemctl daemon-reload 2>/dev/null || true
    (crontab -l 2>/dev/null | grep -v opentrack-agent) | crontab - 2>/dev/null || true
    rm -f "$SCRIPT_PATH" 2>/dev/null || true
    exit 0
  fi

  sleep 29
done
'''


@app.get("/install.sh")
async def install_script():
    return Response(content=INSTALL_SH, media_type="text/x-shellscript", headers={"Content-Disposition": "attachment; filename=install.sh"})


@app.get("/monitor.sh")
async def monitor_script():
    return Response(content=MONITOR_SH, media_type="text/x-shellscript", headers={"Content-Disposition": "attachment; filename=monitor.sh"})


def get_vault_key(user):
    key = user.get("vault_key") or ""
    if not key:
        key = base64.urlsafe_b64encode(Fernet.generate_key()).decode()
        async def save_key():
            async with _DB() as db:
                await db.execute("UPDATE users SET vault_key=? WHERE id=?", [key, user["id"]])
                await db.commit()
        import asyncio
        asyncio.create_task(save_key())
        user["vault_key"] = key
    try:
        return Fernet(base64.urlsafe_b64decode(key.encode()))
    except:
        k = Fernet.generate_key()
        key = base64.urlsafe_b64encode(k).decode()
        async def save_key2():
            async with _DB() as db:
                await db.execute("UPDATE users SET vault_key=? WHERE id=?", [key, user["id"]])
                await db.commit()
        import asyncio
        asyncio.create_task(save_key2())
        user["vault_key"] = key
        return Fernet(k)

def encrypt_val(f, val):
    if not val: return ""
    return f.encrypt(val.encode()).decode()

def decrypt_val(f, val):
    if not val: return ""
    try: return f.decrypt(val.encode()).decode()
    except: return val

@app.get("/api/vault")
async def get_vault(user=Depends(get_current_user)):
    f = get_vault_key(user)
    async with _DB() as db:
        rows = await db.fetchall("SELECT * FROM vault WHERE user_id=? ORDER BY id DESC", [user["id"]])
    for r in rows:
        r["password"] = decrypt_val(f, r.get("password",""))
        r["api_key"] = decrypt_val(f, r.get("api_key",""))
    return rows

@app.post("/api/vault")
async def add_vault(request: Request, user=Depends(get_current_user)):
    data = await request.json()
    name = (data.get("name") or "").strip()
    if not name:
        return {"ok": False, "error": "Name required"}
    f = get_vault_key(user)
    pw = encrypt_val(f, data.get("password",""))
    ak = encrypt_val(f, data.get("api_key",""))
    now = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")
    async with _DB() as db:
        await db.execute("""
            INSERT INTO vault (user_id, name, url, username, password, api_key, notes, category, password_changed_at, favorite)
            VALUES (?,?,?,?,?,?,?,?,?,?)
        """, [user["id"], name, data.get("url",""), data.get("username",""),
              pw, ak, data.get("notes",""), data.get("category",""), now,
              1 if data.get("favorite") else 0])
        await db.commit()
    await audit_log(user["id"], "vault_add", name)
    return {"ok": True}

@app.put("/api/vault/{vid}")
async def update_vault(vid: int, request: Request, user=Depends(get_current_user)):
    data = await request.json()
    name = (data.get("name") or "").strip()
    if not name:
        return {"ok": False, "error": "Name required"}
    f = get_vault_key(user)
    pw = encrypt_val(f, data.get("password",""))
    ak = encrypt_val(f, data.get("api_key",""))
    now = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")
    async with _DB() as db:
        await db.execute("""
            UPDATE vault SET name=?, url=?, username=?, password=?, api_key=?, notes=?,
            category=?, password_changed_at=?, favorite=?
            WHERE id=? AND user_id=?
        """, [name, data.get("url",""), data.get("username",""),
              pw, ak, data.get("notes",""), data.get("category",""),
              now, 1 if data.get("favorite") else 0, vid, user["id"]])
        await db.commit()
    await audit_log(user["id"], "vault_update", f"id={vid}")
    return {"ok": True}

@app.delete("/api/vault/{vid}")
async def delete_vault(vid: int, user=Depends(get_current_user)):
    async with _DB() as db:
        await db.execute("DELETE FROM vault WHERE id=? AND user_id=?", [vid, user["id"]])
        await db.commit()
    await audit_log(user["id"], "vault_delete", f"id={vid}")
    return {"ok": True}

@app.get("/api/authenticator")
async def get_authenticator(user=Depends(get_current_user)):
    async with _DB() as db:
        rows = await db.fetchall("SELECT * FROM authenticator WHERE user_id=? ORDER BY favorite DESC, name ASC", [user["id"]])
    now = int(datetime.now(UTC).timestamp())
    for r in rows:
        try:
            totp = pyotp.TOTP(r["secret"], interval=r["period"])
            r["current_code"] = totp.at(now)
            r["remaining"] = r["period"] - (now % r["period"])
        except:
            r["current_code"] = "—"
            r["remaining"] = 0
    return rows

@app.post("/api/authenticator")
async def add_authenticator(request: Request, user=Depends(get_current_user)):
    data = await request.json()
    name = (data.get("name") or "").strip()
    secret = (data.get("secret") or "").strip().replace(" ", "")
    if not name or not secret:
        return {"ok": False, "error": "Name and secret required"}
    try:
        pyotp.TOTP(secret)
    except:
        return {"ok": False, "error": "Invalid TOTP secret"}
    async with _DB() as db:
        await db.execute("""
            INSERT INTO authenticator (user_id, name, issuer, secret, algorithm, digits, period, category, favorite)
            VALUES (?,?,?,?,?,?,?,?,?)
        """, [user["id"], name, data.get("issuer",""), secret,
              data.get("algorithm","SHA1"), data.get("digits",6),
              data.get("period",30), data.get("category",""), 1 if data.get("favorite") else 0])
        await db.commit()
    await audit_log(user["id"], "auth_add", name)
    return {"ok": True}

@app.put("/api/authenticator/{aid}")
async def update_authenticator(aid: int, request: Request, user=Depends(get_current_user)):
    data = await request.json()
    name = (data.get("name") or "").strip()
    if not name:
        return {"ok": False, "error": "Name required"}
    async with _DB() as db:
        await db.execute("""
            UPDATE authenticator SET name=?, issuer=?, category=?, favorite=?
            WHERE id=? AND user_id=?
        """, [name, data.get("issuer",""), data.get("category",""),
              1 if data.get("favorite") else 0, aid, user["id"]])
        await db.commit()
    await audit_log(user["id"], "auth_update", f"id={aid}")
    return {"ok": True}

@app.post("/api/authenticator/{aid}/toggle-favorite")
async def toggle_favorite(aid: int, user=Depends(get_current_user)):
    async with _DB() as db:
        r = await db.fetchone("SELECT favorite FROM authenticator WHERE id=? AND user_id=?", [aid, user["id"]])
        if not r:
            return {"ok": False, "error": "Not found"}
        new_fav = 0 if r["favorite"] else 1
        await db.execute("UPDATE authenticator SET favorite=? WHERE id=? AND user_id=?", [new_fav, aid, user["id"]])
        await db.commit()
    await audit_log(user["id"], "auth_favorite", f"id={aid} favorite={new_fav}")
    return {"ok": True, "favorite": new_fav}

@app.delete("/api/authenticator/{aid}")
async def delete_authenticator(aid: int, user=Depends(get_current_user)):
    async with _DB() as db:
        await db.execute("DELETE FROM authenticator WHERE id=? AND user_id=?", [aid, user["id"]])
        await db.commit()
    await audit_log(user["id"], "auth_delete", f"id={aid}")
    return {"ok": True}

@app.get("/api/authenticator/generate-qr")
async def generate_qr(secret: str = "", name: str = "", issuer: str = ""):
    if not secret or not name:
        return {"ok": False, "error": "Secret and name required"}
    uri = pyotp.totp.TOTP(secret).provisioning_uri(name=name, issuer_name=issuer or name)
    return {"uri": uri, "otpauth": uri}

@app.post("/api/authenticator/verify-secret")
async def verify_secret(request: Request):
    data = await request.json()
    secret = (data.get("secret") or "").strip()
    code = (data.get("code") or "").strip()
    if not secret or not code:
        return {"ok": False, "error": "Secret and code required"}
    try:
        totp = pyotp.TOTP(secret)
        valid = totp.verify(code)
        return {"ok": valid}
    except:
        return {"ok": False, "error": "Invalid secret"}

@app.post("/api/password/generate")
async def generate_password(request: Request):
    data = await request.json()
    length = min(max(int(data.get("length", 16)), 8), 128)
    use_upper = data.get("upper", True)
    use_lower = data.get("lower", True)
    use_digits = data.get("digits", True)
    use_symbols = data.get("symbols", True)
    exclude = data.get("exclude", "")
    chars = ""
    if use_upper: chars += string.ascii_uppercase
    if use_lower: chars += string.ascii_lowercase
    if use_digits: chars += string.digits
    if use_symbols: chars += string.punctuation
    if not chars:
        chars = string.ascii_letters + string.digits
    for c in exclude:
        chars = chars.replace(c, "")
    if not chars:
        chars = string.ascii_letters + string.digits
    password = "".join(secrets.choice(chars) for _ in range(length))
    return {"password": password}

@app.post("/api/password/strength")
async def check_strength(request: Request):
    data = await request.json()
    pw = data.get("password", "")
    score = 0
    if len(pw) >= 8: score += 20
    if len(pw) >= 12: score += 10
    if len(pw) >= 16: score += 10
    if any(c.isupper() for c in pw): score += 15
    if any(c.islower() for c in pw): score += 15
    if any(c.isdigit() for c in pw): score += 15
    if any(c in string.punctuation for c in pw): score += 15
    if score >= 90: label = "Strong"; color = "#34d399"
    elif score >= 60: label = "Medium"; color = "#fbbf24"
    else: label = "Weak"; color = "#f87171"
    return {"score": score, "label": label, "color": color}

@app.post("/api/password/breach-check")
async def breach_check(request: Request):
    data = await request.json()
    password = data.get("password", "")
    sha1 = hashlib.sha1(password.encode()).hexdigest().upper()
    prefix, suffix = sha1[:5], sha1[5:]
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(f"https://api.pwnedpasswords.com/range/{prefix}")
            if r.status_code == 200:
                hashes = [line.split(":") for line in r.text.splitlines()]
                for h, count in hashes:
                    if h == suffix:
                        return {"breached": True, "count": int(count)}
                return {"breached": False, "count": 0}
    except:
        pass
    return {"breached": None, "count": 0, "error": "Could not check"}

@app.get("/api/incidents")
async def get_incidents(user=Depends(get_current_user)):
    async with _DB() as db:
        return await db.fetchall("""
            SELECT i.*, m.name as monitor_name, m.url as monitor_url
            FROM incidents i JOIN monitors m ON i.monitor_id=m.id
            WHERE m.user_id=? ORDER BY i.id DESC LIMIT 100
        """, [user["id"]])

@app.post("/api/incidents/{incident_id}/resolve")
async def resolve_incident(incident_id: int, request: Request, user=Depends(get_current_user)):
    data = await request.json()
    now = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")
    async with _DB() as db:
        await db.execute(
            "UPDATE incidents SET ended_at=?, root_cause=? WHERE id=? AND monitor_id IN (SELECT id FROM monitors WHERE user_id=?)",
            [now, data.get("root_cause",""), incident_id, user["id"]]
        )
        await db.commit()
    return {"ok": True}


@app.get("/{full_path:path}")
async def spa_fallback(full_path: str):
    # API paths that aren't registered should return 404, not the SPA
    if full_path.startswith("api/"):
        raise HTTPException(status_code=404, detail="Not found")
    # Serve real files from dist (e.g. manifest, icons, SW) with traversal guard
    if full_path:
        candidate = (FRONTEND_DIST / full_path).resolve()
        try:
            candidate.relative_to(FRONTEND_DIST.resolve())
            if candidate.is_file():
                return FileResponse(str(candidate))
        except ValueError:
            pass
    # All other routes → serve index.html so React Router handles them
    return _frontend_response()
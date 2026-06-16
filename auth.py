import bcrypt, os
from jose import jwt
from datetime import datetime, timedelta, timezone
from fastapi import Depends, HTTPException, Cookie
from typing import Optional
from db import _DB

SECRET = os.getenv("JWT_SECRET", "CHANGE_THIS_SECRET_KEY_2024")
ALGO = "HS256"
PIN_ALGO = ALGO

def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()

def verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode(), hashed.encode())

def create_token(user_id: int, username: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(days=7)
    return jwt.encode({"sub": str(user_id), "username": username, "exp": expire}, SECRET, algorithm=ALGO)

def create_pin_unlock(user_id: int, minutes: int = 5) -> str:
    expire = datetime.now(timezone.utc) + timedelta(minutes=minutes)
    return jwt.encode({"sub": str(user_id), "purpose": "pin_unlock", "exp": expire}, SECRET, algorithm=PIN_ALGO)

def verify_pin_unlock(token: str, user_id: int) -> bool:
    try:
        payload = jwt.decode(token, SECRET, algorithms=[PIN_ALGO])
        return int(payload.get("sub")) == int(user_id) and payload.get("purpose") == "pin_unlock"
    except:
        return False

async def get_current_user_raw(token: Optional[str] = Cookie(None)):
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    try:
        payload = jwt.decode(token, SECRET, algorithms=[ALGO])
        user_id = int(payload.get("sub"))
        username = payload.get("username")
    except:
        raise HTTPException(status_code=401, detail="Invalid token")
    async with _DB() as db:
        user = await db.fetchone("SELECT * FROM users WHERE id=?", [user_id])
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    return user

async def get_current_user(token: Optional[str] = Cookie(None), pin_unlock: Optional[str] = Cookie(None)):
    return await get_current_user_raw(token)
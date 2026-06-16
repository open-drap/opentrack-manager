import asyncio, httpx, os
from db import _DB

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
bot_username = ""
bot_fetched = False

async def get_bot_username():
    global bot_username, bot_fetched
    if bot_fetched and bot_username:
        return bot_username
    if not BOT_TOKEN:
        return ""
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(f"https://api.telegram.org/bot{BOT_TOKEN}/getMe")
            data = r.json()
            if data.get("ok"):
                bot_username = data["result"].get("username", "")
                bot_fetched = True
    except:
        pass
    return bot_username

async def poll_bot():
    if not BOT_TOKEN:
        return
    uname = await get_bot_username()
    print(f"Telegram bot @{uname} running")
    offset = 0
    async with httpx.AsyncClient(timeout=35) as c:
        while True:
            try:
                r = await c.get(
                    f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates",
                    params={"offset": offset, "timeout": 30}
                )
                data = r.json()
                if data.get("ok") and data.get("result"):
                    for upd in data["result"]:
                        offset = upd["update_id"] + 1
                        await handle(upd, c)
            except Exception as e:
                msg = str(e)
                if msg:
                    print(f"Bot poll error: {e}")
            await asyncio.sleep(1)

async def handle(upd: dict, c: httpx.AsyncClient):
    msg = upd.get("message") or upd.get("my_chat_member", {}).get("chat") and {}
    chat = msg.get("chat", {}) if isinstance(msg, dict) else {}
    chat_id = chat.get("id")
    text = (msg.get("text") or "").strip() if isinstance(msg, dict) else ""

    if not chat_id or not text:
        return

    payload = ""
    if text.startswith("/start"):
        parts = text.split(None, 1)
        payload = parts[1].strip() if len(parts) > 1 else ""
    else:
        payload = text

    payload = payload.lstrip("@")

    if not payload:
        await send(c, chat_id, "Send your username to link alerts.\nExample: `/start myusername`")
        return

    if payload.isdigit():
        async with _DB() as db:
            user = await db.fetchone("SELECT * FROM users WHERE id=?", [int(payload)])
    else:
        async with _DB() as db:
            user = await db.fetchone("SELECT * FROM users WHERE username=?", [payload])

    if user:
        async with _DB() as db:
            await db.execute("UPDATE users SET telegram_chat_id=? WHERE id=?", [str(chat_id), user["id"]])
            await db.commit()
        await send(c, chat_id,
            f"✅ Linked to *@{user['username']}*!\n\n"
            f"You'll now receive:\n"
            f"🔴 Monitor down \& recovery alerts\n"
            f"🐢 Slow response warnings\n"
            f"🖥 Server offline \& back online\n"
            f"⚠️ High RAM / CPU / Disk alerts\n"
            f"📝 Note reminders\n"
            f"📨 Scheduled messages"
        )
    else:
        await send(c, chat_id,
            f"❌ User '{payload}' not found.\n\n"
            f"Make sure you register at the dashboard first, then send your exact username."
        )

async def send(c: httpx.AsyncClient, chat_id: int, text: str):
    try:
        await c.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}
        )
    except:
        pass
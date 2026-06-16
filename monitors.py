import httpx, asyncio, time, socket, ssl, dns.resolver, json, os
from datetime import datetime, UTC
from db import _DB

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")

async def send_telegram(token: str, chat_id: str, text: str):
  t = token or BOT_TOKEN
  if not t or not chat_id: return
  try:
    async with httpx.AsyncClient(timeout=10) as client:
      await client.post(f"https://api.telegram.org/bot{t}/sendMessage",
        json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"})
  except: pass

async def notify_all(u: dict, text: str):
  if u.get("telegram_chat_id"):
    await send_telegram(BOT_TOKEN, u["telegram_chat_id"], text)

async def check_ssl(hostname: str) -> tuple:
  try:
    ctx = ssl.create_default_context()
    conn = ctx.wrap_socket(socket.socket(), server_hostname=hostname)
    conn.settimeout(5)
    conn.connect((hostname, 443))
    cert = conn.getpeercert()
    exp = datetime.strptime(cert['notAfter'], "%b %d %H:%M:%S %Y %Z").replace(tzinfo=UTC)
    days = (exp - datetime.now(UTC)).days
    conn.close()
    return True, days, f"{days} days"
  except Exception as e:
    return False, None, str(e)

async def check_dns(domain: str) -> bool:
  try:
    dns.resolver.resolve(domain, 'A')
    return True
  except: return False

async def check_port(host: str, port: int) -> tuple:
  start = time.time()
  try:
    reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout=10)
    writer.close()
    return True, round((time.time() - start) * 1000, 1)
  except: return False, None

async def check_udp(host: str, port: int) -> tuple:
  start = time.time()
  try:
    _, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout=10)
    writer.close()
    return True, round((time.time() - start) * 1000, 1)
  except: return False, None

async def check_http(url: str, keyword: str = "", headers: dict = None, method: str = "GET", expected_status: int = 0) -> tuple:
  start = time.time()
  try:
    async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
      if method == "HEAD":
        resp = await client.head(url, headers=headers)
      else:
        resp = await client.get(url, headers=headers)
    elapsed = round((time.time() - start) * 1000, 1)
    is_up = resp.status_code < 400
    if expected_status and resp.status_code != expected_status:
      is_up = False
    if keyword and keyword not in resp.text:
      is_up = False
    return is_up, elapsed, resp.status_code
  except Exception as e:
    return False, None, str(e)

def in_maintenance(m: dict) -> bool:
  mf, mt = m.get("maintenance_from", ""), m.get("maintenance_to", "")
  if not mf or not mt: return False
  now = datetime.now(UTC)
  try:
    f = datetime.fromisoformat(mf)
    t = datetime.fromisoformat(mt)
    return f <= now <= t
  except: return False

async def run_check(monitor: dict) -> tuple:
  mtype = monitor.get("type", "http")
  headers = {}
  try:
    h = json.loads(monitor.get("custom_headers", "{}"))
    if isinstance(h, dict): headers = h
  except: pass
  method = monitor.get("method", "GET")
  expected = monitor.get("expected_status", 0)
  if mtype == "http":
    return await check_http(monitor["url"], monitor.get("keyword", ""), headers, method, expected)
  elif mtype == "ssl":
    ok, days, msg = await check_ssl(monitor["url"])
    return ok, days, msg
  elif mtype == "port":
    ok, ms = await check_port(monitor["url"], monitor.get("port", 80))
    return ok, ms, None
  elif mtype == "udp":
    ok, ms = await check_udp(monitor["url"], monitor.get("port", 80))
    return ok, ms, None
  elif mtype == "dns":
    ok = await check_dns(monitor["url"])
    return ok, None, None
  elif mtype == "ping":
    proc = await asyncio.create_subprocess_exec("ping", "-c", "1", "-W", "3", monitor["url"],
      stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
    await proc.wait()
    return proc.returncode == 0, None, None
  return False, None, None

async def monitor_worker():
  while True:
    try:
      async with _DB() as db:
        monitors = await db.fetchall("""
          SELECT m.*, u.telegram_chat_id
          FROM monitors m JOIN users u ON m.user_id=u.id
        """)
      tasks = [process_monitor(m) for m in monitors]
      if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    except Exception as e:
      print(f"Worker error: {e}")
    await asyncio.sleep(15)

RAM_ALERT  = 85   # % threshold to alert
CPU_ALERT  = 90
DISK_ALERT = 90
OFFLINE_MINUTES = 5

async def server_alert_worker():
    while True:
        try:
            async with _DB() as db:
                servers = await db.fetchall("""
                    SELECT s.*, u.telegram_chat_id
                    FROM servers s JOIN users u ON s.user_id=u.id
                """)
            now = datetime.now(UTC)
            for s in servers:
                chat_id = s.get("telegram_chat_id") or ""
                if not chat_id:
                    continue
                host = s.get("hostname") or f"Server #{s['id']}"
                last_hb = s.get("last_heartbeat")

                # ── Offline detection ──────────────────────────────────────
                offline = False
                if last_hb:
                    try:
                        lhb = datetime.strptime(last_hb, "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)
                        offline = (now - lhb).total_seconds() > OFFLINE_MINUTES * 60
                    except:
                        pass

                async with _DB() as db:
                    if offline and not s.get("offline_alerted"):
                        await send_telegram(BOT_TOKEN, chat_id,
                            f"🔴 *Server Offline*\n🖥 `{host}`\n⏱ No heartbeat for {OFFLINE_MINUTES}+ minutes\n🕐 {now.strftime('%Y-%m-%d %H:%M:%S')}")
                        await db.execute("UPDATE servers SET offline_alerted=1 WHERE id=?", [s["id"]])
                        await db.commit()
                    elif not offline and s.get("offline_alerted"):
                        await send_telegram(BOT_TOKEN, chat_id,
                            f"✅ *Server Back Online*\n🖥 `{host}`\n💡 Heartbeat restored")
                        await db.execute("UPDATE servers SET offline_alerted=0 WHERE id=?", [s["id"]])
                        await db.commit()

                if offline:
                    continue  # skip metric alerts while offline

                ram  = float(s.get("ram")  or 0)
                cpu  = float(s.get("cpu")  or 0)
                disk = float(s.get("disk") or 0)

                async with _DB() as db:
                    # ── RAM alert ──────────────────────────────────────────
                    if ram >= RAM_ALERT and not s.get("ram_alerted"):
                        await send_telegram(BOT_TOKEN, chat_id,
                            f"⚠️ *High RAM Usage*\n🖥 `{host}`\n💾 RAM: *{ram:.0f}%*\nConsider restarting services or adding memory.")
                        await db.execute("UPDATE servers SET ram_alerted=1 WHERE id=?", [s["id"]])
                        await db.commit()
                    elif ram < 75 and s.get("ram_alerted"):
                        await db.execute("UPDATE servers SET ram_alerted=0 WHERE id=?", [s["id"]])
                        await db.commit()

                    # ── CPU alert ──────────────────────────────────────────
                    if cpu >= CPU_ALERT and not s.get("cpu_alerted"):
                        await send_telegram(BOT_TOKEN, chat_id,
                            f"⚠️ *High CPU Usage*\n🖥 `{host}`\n⚡ CPU: *{cpu:.0f}%*\nLoad avg: {s.get('load_avg','?')}")
                        await db.execute("UPDATE servers SET cpu_alerted=1 WHERE id=?", [s["id"]])
                        await db.commit()
                    elif cpu < 80 and s.get("cpu_alerted"):
                        await db.execute("UPDATE servers SET cpu_alerted=0 WHERE id=?", [s["id"]])
                        await db.commit()

                    # ── Disk alert ─────────────────────────────────────────
                    if disk >= DISK_ALERT and not s.get("disk_alerted"):
                        await send_telegram(BOT_TOKEN, chat_id,
                            f"⚠️ *High Disk Usage*\n🖥 `{host}`\n💿 Disk: *{disk:.0f}%*\nClean up or expand storage soon.")
                        await db.execute("UPDATE servers SET disk_alerted=1 WHERE id=?", [s["id"]])
                        await db.commit()
                    elif disk < 80 and s.get("disk_alerted"):
                        await db.execute("UPDATE servers SET disk_alerted=0 WHERE id=?", [s["id"]])
                        await db.commit()
        except Exception as e:
            print(f"Server alert worker error: {e}")
        await asyncio.sleep(60)


async def process_monitor(m: dict):
  if in_maintenance(m):
    return
  is_up, elapsed, status_code = await run_check(m)
  now = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")
  prev_status = m["status"]

  history = json.loads(m.get("response_history", "[]"))
  if elapsed is not None:
    history.append(elapsed)
    history = history[-20:]
  history_json = json.dumps(history)

  async with _DB() as db:
    if is_up:
      await db.execute("""
        UPDATE monitors SET status='up', last_check=?, response_time=?,
        down_since=NULL, notified=0,
        uptime_count=uptime_count+1, response_history=? WHERE id=?
      """, [now, elapsed, history_json, m["id"]])
      if m["type"] == "ssl" and status_code:
        ssl_val = status_code if isinstance(status_code, str) else str(status_code)
        await db.execute("UPDATE monitors SET ssl_expiry=? WHERE id=?", [ssl_val, m["id"]])
      if prev_status == "down" and m["notified"]:
        await notify_all(m, f"✅ *RECOVERED*\n🔗 `{m['url']}`\n⏱ {elapsed}ms\n🕐 {now}")
        if m.get("down_since"):
          try:
            ds = datetime.strptime(m["down_since"], "%Y-%m-%d %H:%M:%S")
            dur = int((datetime.now(UTC) - ds.replace(tzinfo=UTC)).total_seconds())
            await db.execute("UPDATE incidents SET ended_at=?, duration=? WHERE monitor_id=? AND ended_at IS NULL",
              [now, dur, m["id"]])
          except: pass
      if elapsed and elapsed > 3000:
        await notify_all(m, f"🐢 *SLOW RESPONSE*\n🔗 `{m['url']}`\n⏱ {elapsed}ms\n🕐 {now}")
    else:
      down_since = m["down_since"] or now
      await db.execute("""
        UPDATE monitors SET status='down', last_check=?, response_time=?,
        down_since=?, down_count=down_count+1, response_history=? WHERE id=?
      """, [now, elapsed, down_since, history_json, m["id"]])
      if not m["notified"]:
        status_text = status_code if status_code else "N/A"
        await notify_all(m, f"🔴 *DOWN ALERT*\n🔗 `{m['url']}`\n❌ {status_text}\n🕐 {now}")
        await db.execute("UPDATE monitors SET notified=1 WHERE id=?", [m["id"]])
        if not m.get("down_since"):
          await db.execute("INSERT INTO incidents (monitor_id, started_at) VALUES (?,?)", [m["id"], now])
    await db.commit()
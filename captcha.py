import uuid, time

CHALLENGES: dict = {}
CLEANUP_INTERVAL = 300
last_cleanup = time.time()

def _cleanup():
    global last_cleanup
    now = time.time()
    if now - last_cleanup < CLEANUP_INTERVAL:
        return
    expired = [k for k, v in CHALLENGES.items() if v["expires"] < now]
    for k in expired:
        CHALLENGES.pop(k, None)
    last_cleanup = now

def generate() -> dict:
    _cleanup()
    token = uuid.uuid4().hex[:16]
    CHALLENGES[token] = {"created_at": time.time(), "expires": time.time() + 300}
    return {"token": token}

def verify(token: str, answer: str) -> bool:
    _cleanup()
    challenge = CHALLENGES.pop(token, None)
    if not challenge:
        return False
    if challenge["expires"] < time.time():
        return False
    elapsed = time.time() - challenge["created_at"]
    # submitted too fast (< 2s) = bot
    if elapsed < 2:
        return False
    return True

import sqlite3, os, time, json
DB_PATH = os.getenv("CACHE_DB_PATH", "data/cache.sqlite3")
os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
def _conn():
    conn = sqlite3.connect(DB_PATH, isolation_level=None, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    return conn
CONN = _conn()
CONN.execute("CREATE TABLE IF NOT EXISTS kvstore (k TEXT, ts INTEGER, ttl INTEGER, v BLOB);")
def put(key, value, ttl=120, max_rows=100):
    now = int(time.time())
    CONN.execute("INSERT INTO kvstore(k, ts, ttl, v) VALUES(?,?,?,?)", (key, now, ttl, json.dumps(value)))
    CONN.execute("DELETE FROM kvstore WHERE k=? AND ts NOT IN (SELECT ts FROM kvstore WHERE k=? ORDER BY ts DESC LIMIT ?)", (key, key, max_rows))
def get_latest(key):
    now = int(time.time())
    row = CONN.execute("SELECT v, ts, ttl FROM kvstore WHERE k=? ORDER BY ts DESC LIMIT 1", (key,)).fetchone()
    if not row: return None
    v, ts, ttl = row
    if ts + ttl < now: return None
    try: return json.loads(v)
    except Exception: return v
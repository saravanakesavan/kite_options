"""
One-time script: upsert user with a fresh bcrypt hash (no passlib dependency).
Run with:  python3 reset_password.py
"""
import sqlite3, hashlib, base64, bcrypt

DB_PATH  = "trading_app.db"
USERNAME = "saravanakesavan"
EMAIL    = "saravanakesavan98@gmail.com"
PASSWORD = "abcd@4321"

def prepare_password(password: str) -> bytes:
    """SHA-256 pre-hash → base64 bytes, always under bcrypt's 72-byte limit."""
    digest = hashlib.sha256(password.encode("utf-8")).digest()
    return base64.b64encode(digest)

new_hash = bcrypt.hashpw(prepare_password(PASSWORD), bcrypt.gensalt()).decode("utf-8")

conn = sqlite3.connect(DB_PATH)
cur  = conn.cursor()

cur.execute("SELECT id, username, email FROM users")
print(f"Current users in DB: {cur.fetchall()}")

cur.execute("UPDATE users SET hashed_password=? WHERE username=?", (new_hash, USERNAME))
conn.commit()

if cur.rowcount == 0:
    print(f"User not found — inserting...")
    cur.execute(
        "INSERT INTO users (username, email, hashed_password, is_active) VALUES (?, ?, ?, 1)",
        (USERNAME, EMAIL, new_hash)
    )
    conn.commit()
    print(f"✓ User created with id={cur.lastrowid}")
else:
    print(f"✓ Password hash updated for '{USERNAME}'")

# Verify it works before exiting
cur.execute("SELECT hashed_password FROM users WHERE username=?", (USERNAME,))
stored = cur.fetchone()[0]
ok = bcrypt.checkpw(prepare_password(PASSWORD), stored.encode("utf-8"))
print(f"  Verification check: {'✓ PASS' if ok else '✗ FAIL'}")
print("  Now restart your backend server and try logging in.")
conn.close()

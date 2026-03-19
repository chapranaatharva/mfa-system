from flask import Flask, request, jsonify, session, render_template
from flask_session import Session
import random, string, sqlite3, os, time, secrets, smtplib, ssl, pickle
from datetime import datetime, timedelta, date
from collections import defaultdict
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from dotenv import load_dotenv
import face_recognition
import numpy as np
import cv2
import base64

app = Flask(__name__)
app.secret_key = secrets.token_hex(32)
app.config["SESSION_TYPE"] = "filesystem"
Session(app)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB       = os.path.join(BASE_DIR, "mfa_logs.db")
USERS_DB = os.path.join(BASE_DIR, "users.db")

load_dotenv(os.path.join(BASE_DIR, ".env"))
EMAIL_ADDRESS  = os.getenv("EMAIL_ADDRESS", "")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD", "")

SESSION_EXPIRY_MINS = 30
OTP_EXPIRY_SECS     = 60

_otp_store    = {}
_rate_limiter = defaultdict(list)

def get_db():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    return conn

def get_users_db():
    conn = sqlite3.connect(USERS_DB)
    conn.row_factory = sqlite3.Row
    return conn

def send_otp_email(to_address, otp):
    try:
        msg            = MIMEMultipart("alternative")
        msg["Subject"] = "Your MFA Verification Code"
        msg["From"]    = f"MFA System <{EMAIL_ADDRESS}>"
        msg["To"]      = to_address
        html = f"""
        <html><body style="font-family:Segoe UI,sans-serif;background:#0f1117;margin:0;padding:32px;">
          <div style="max-width:400px;margin:auto;background:#13151f;border-radius:12px;
                      padding:32px;border:1px solid #1e2133;">
            <p style="color:#5a5f78;font-size:12px;margin:0 0 8px;text-transform:uppercase;letter-spacing:.08em;">MFA System</p>
            <h2 style="color:#e8eaf6;font-size:22px;margin:0 0 24px;">Verification Code</h2>
            <div style="background:#0f1117;border-radius:8px;padding:20px;text-align:center;margin-bottom:24px;">
              <span style="font-size:36px;font-weight:700;letter-spacing:8px;color:#5B8DEF;">{otp}</span>
            </div>
            <p style="color:#5a5f78;font-size:13px;margin:0;">Expires in <strong style="color:#e8eaf6;">60 seconds</strong>. Do not share.</p>
          </div>
        </body></html>"""
        msg.attach(MIMEText(html, "html"))
        ctx = ssl.create_default_context()
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ctx) as s:
            s.login(EMAIL_ADDRESS, EMAIL_PASSWORD)
            s.sendmail(EMAIL_ADDRESS, to_address, msg.as_string())
        return True, ""
    except Exception as e:
        return False, str(e)

def check_rate_limit(email):
    now    = time.time()
    cutoff = now - 600
    reqs   = [t for t in _rate_limiter[email] if t > cutoff]
    _rate_limiter[email] = reqs
    if len(reqs) >= 3:
        wait = int(600 - (now - reqs[0]))
        return False, wait
    _rate_limiter[email].append(now)
    return True, 0

def log_attempt(email, otp_status, face_status, result):
    conn = get_db()
    conn.execute(
        "INSERT INTO auth_logs (timestamp,phone,otp_status,face_status,result) VALUES (?,?,?,?,?)",
        (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), email, otp_status, face_status, result))
    conn.commit()
    conn.close()

def create_session_token(email, name):
    token   = secrets.token_hex(32)
    created = datetime.now()
    expires = created + timedelta(minutes=SESSION_EXPIRY_MINS)
    conn    = get_db()
    conn.execute(
        "INSERT INTO sessions (token,email,name,created,expires) VALUES (?,?,?,?,?)",
        (token, email, name,
         created.strftime("%Y-%m-%d %H:%M:%S"),
         expires.strftime("%Y-%m-%d %H:%M:%S")))
    conn.commit()
    conn.close()
    return token, expires

# ── Main pages ────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/register")
def register_page():
    return render_template("register.html")

# ── MFA API ───────────────────────────────────────────────────────────

@app.route("/api/send-otp", methods=["POST"])
def send_otp():
    data  = request.json
    email = data.get("email", "").strip()
    if "@" not in email or "." not in email:
        return jsonify({"ok": False, "error": "Invalid email address."})
    allowed, wait = check_rate_limit(email)
    if not allowed:
        m, s = wait // 60, wait % 60
        return jsonify({"ok": False, "error": f"Too many requests. Try again in {m}m {s}s."})
    otp = ''.join(random.choices(string.digits, k=6))
    _otp_store[email] = {"otp": otp, "expires": time.time() + OTP_EXPIRY_SECS}
    ok, err = send_otp_email(email, otp)
    if not ok:
        return jsonify({"ok": False, "error": f"Failed to send email: {err}"})
    return jsonify({"ok": True, "message": f"OTP sent to {email}"})

@app.route("/api/verify-otp", methods=["POST"])
def verify_otp():
    data    = request.json
    email   = data.get("email", "").strip()
    entered = data.get("otp", "").strip()
    record  = _otp_store.get(email)
    if not record:
        return jsonify({"ok": False, "error": "No OTP found. Request a new one."})
    if time.time() > record["expires"]:
        del _otp_store[email]
        return jsonify({"ok": False, "error": "OTP expired. Request a new one."})
    if entered != record["otp"]:
        return jsonify({"ok": False, "error": "Incorrect OTP."})
    del _otp_store[email]
    session["otp_verified"] = True
    session["email"]        = email
    return jsonify({"ok": True})

@app.route("/api/verify-face", methods=["POST"])
def verify_face():
    if not session.get("otp_verified"):
        return jsonify({"ok": False, "error": "OTP not verified."})
    data       = request.json
    image_data = data.get("image", "")
    try:
        header, encoded = image_data.split(",", 1)
        img_bytes  = base64.b64decode(encoded)
        img_array  = np.frombuffer(img_bytes, np.uint8)
        frame      = cv2.imdecode(img_array, cv2.IMREAD_COLOR)
        rgb_frame  = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    except Exception as e:
        return jsonify({"ok": False, "error": f"Image decode error: {e}"})
    locs = face_recognition.face_locations(rgb_frame, model="hog")
    if not locs:
        return jsonify({"ok": False, "error": "No face detected."})
    encs = face_recognition.face_encodings(rgb_frame, locs)
    if not encs:
        return jsonify({"ok": False, "error": "Could not encode face."})
    submitted_enc = encs[0]
    try:
        conn  = get_users_db()
        users = conn.execute("SELECT email, name, encoding FROM users").fetchall()
        conn.close()
    except Exception as e:
        return jsonify({"ok": False, "error": f"User database error: {e}"})
    matched_email = None
    matched_name  = None
    for user in users:
        known_enc = pickle.loads(user["encoding"])
        m = face_recognition.compare_faces([known_enc], submitted_enc, tolerance=0.50)
        if m[0]:
            matched_email = user["email"]
            matched_name  = user["name"]
            break
    if not matched_email:
        log_attempt(session.get("email"), "PASSED", "FAILED", "DENIED")
        return jsonify({"ok": False, "error": "Face not recognised."})
    token, expires = create_session_token(matched_email, matched_name)
    log_attempt(matched_email, "PASSED", "PASSED", "GRANTED")
    _rate_limiter[matched_email] = []
    session.clear()
    session["authenticated"] = True
    session["token"]         = token
    session["name"]          = matched_name
    session["email"]         = matched_email
    return jsonify({
        "ok":      True,
        "name":    matched_name,
        "email":   matched_email,
        "token":   token,
        "expires": expires.strftime("%Y-%m-%d %H:%M:%S")
    })

# ── Registration API ──────────────────────────────────────────────────

@app.route("/api/register-face", methods=["POST"])
def register_face():
    data       = request.json
    name       = data.get("name",  "").strip()
    email      = data.get("email", "").strip()
    image_data = data.get("image", "")

    if not name or not email:
        return jsonify({"ok": False, "error": "Name and email are required."})
    if "@" not in email or "." not in email:
        return jsonify({"ok": False, "error": "Invalid email address."})

    try:
        header, encoded = image_data.split(",", 1)
        img_bytes = base64.b64decode(encoded)
        img_array = np.frombuffer(img_bytes, np.uint8)
        frame     = cv2.imdecode(img_array, cv2.IMREAD_COLOR)
        rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    except Exception as e:
        return jsonify({"ok": False, "error": f"Image decode error: {e}"})

    locs = face_recognition.face_locations(rgb_frame, model="hog")
    if not locs:
        return jsonify({"ok": False, "error": "No face detected. Please retake."})

    encs = face_recognition.face_encodings(rgb_frame, locs)
    if not encs:
        return jsonify({"ok": False, "error": "Could not encode face. Try better lighting."})

    try:
        conn = get_users_db()
        conn.execute(
            "INSERT INTO users (email, name, encoding, created) VALUES (?,?,?,datetime('now'))",
            (email, name, pickle.dumps(encs[0])))
        conn.commit()
        conn.close()
        return jsonify({"ok": True, "name": name, "email": email})
    except Exception as e:
        if "UNIQUE constraint" in str(e):
            return jsonify({"ok": False, "error": "Email already registered."})
        return jsonify({"ok": False, "error": str(e)})

# ── Admin routes ──────────────────────────────────────────────────────

@app.route("/admin")
def admin():
    return render_template("admin_dashboard.html")

@app.route("/api/stats")
def stats():
    conn    = get_db()
    total   = conn.execute("SELECT COUNT(*) FROM auth_logs").fetchone()[0]
    granted = conn.execute("SELECT COUNT(*) FROM auth_logs WHERE result='GRANTED'").fetchone()[0]
    denied  = conn.execute("SELECT COUNT(*) FROM auth_logs WHERE result='DENIED'").fetchone()[0]
    locked  = conn.execute("SELECT COUNT(*) FROM auth_logs WHERE result='LOCKED OUT'").fetchone()[0]
    conn.close()
    rate = f"{int(granted/total*100)}%" if total else "-"
    return jsonify({"total": total, "granted": granted, "denied": denied, "locked": locked, "rate": rate})

@app.route("/api/admin/logs")
def admin_logs():
    conn = get_db()
    rows = conn.execute(
        "SELECT timestamp, phone as email, otp_status, face_status, result "
        "FROM auth_logs ORDER BY id DESC LIMIT 100"
    ).fetchall()
    conn.close()
    return jsonify({"logs": [dict(r) for r in rows]})

@app.route("/api/admin/sessions")
def admin_sessions():
    conn = get_db()
    has_table = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='sessions'"
    ).fetchone()
    if not has_table:
        conn.close()
        return jsonify({"sessions": []})
    rows = conn.execute(
        "SELECT email, name, expires FROM sessions "
        "WHERE revoked=0 AND expires > datetime('now') ORDER BY expires DESC"
    ).fetchall()
    conn.close()
    return jsonify({"sessions": [dict(r) for r in rows]})

@app.route("/api/admin/users")
def admin_users():
    try:
        conn  = get_users_db()
        users = conn.execute(
            "SELECT email, name, created FROM users ORDER BY id DESC"
        ).fetchall()
        conn.close()
        return jsonify({"users": [dict(u) for u in users]})
    except Exception as e:
        return jsonify({"users": [], "error": str(e)})

@app.route("/api/admin/activity")
def admin_activity():
    conn = get_db()
    rows = conn.execute("""
        SELECT date(timestamp) as day,
            SUM(CASE WHEN result='GRANTED'    THEN 1 ELSE 0 END) as granted,
            SUM(CASE WHEN result='DENIED'     THEN 1 ELSE 0 END) as denied,
            COUNT(*) as total
        FROM auth_logs
        WHERE timestamp >= date('now', '-7 days')
        GROUP BY date(timestamp) ORDER BY day ASC
    """).fetchall()
    conn.close()
    days_map = {r["day"]: dict(r) for r in rows}
    result = []
    for i in range(6, -1, -1):
        d     = (date.today() - timedelta(days=i)).isoformat()
        entry = days_map.get(d, {"day": d, "granted": 0, "denied": 0, "total": 0})
        entry["label"] = (date.today() - timedelta(days=i)).strftime("%a")
        result.append(entry)
    return jsonify({"days": result})

if __name__ == "__main__":
    app.run(debug=True, port=5000)

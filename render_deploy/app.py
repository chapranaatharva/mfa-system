from flask import Flask, request, jsonify, session, render_template
from flask_session import Session
import random, string, sqlite3, os, time, secrets, smtplib, ssl
from datetime import datetime, timedelta
from collections import defaultdict
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-key")
app.config["SESSION_TYPE"] = "filesystem"
app.config["SESSION_FILE_DIR"] = "./flask_session"
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(minutes=30)
Session(app)

EMAIL_ADDRESS  = os.environ.get("EMAIL_ADDRESS", "")
EMAIL_PASSWORD = os.environ.get("EMAIL_PASSWORD", "")
DEMO_MODE      = True  # Always demo on Render

DB_LOGS = "mfa_logs.db"

rate_limit    = defaultdict(list)
lockout_until = defaultdict(float)
active_sessions = {}

# ── DB ──────────────────────────────────────────────────────────────────────
def get_logs_db():
    conn = sqlite3.connect(DB_LOGS)
    conn.row_factory = sqlite3.Row
    conn.execute("""CREATE TABLE IF NOT EXISTS auth_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        email TEXT, event TEXT, detail TEXT,
        ip TEXT, timestamp TEXT)""")
    conn.commit()
    return conn

def log_event(email, event, detail="", ip=""):
    conn = get_logs_db()
    conn.execute("INSERT INTO auth_logs (email,event,detail,ip,timestamp) VALUES (?,?,?,?,?)",
                 (email, event, detail, ip, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    conn.commit()
    conn.close()

# ── OTP ─────────────────────────────────────────────────────────────────────
def generate_otp():
    return ''.join(random.choices(string.digits, k=6))

def send_otp_email(to_email, otp):
    if not EMAIL_ADDRESS or not EMAIL_PASSWORD:
        return False
    try:
        msg = MIMEMultipart()
        msg["From"] = EMAIL_ADDRESS
        msg["To"]   = to_email
        msg["Subject"] = "Your MFA Verification Code"
        body = f"""
        <html><body style="font-family:sans-serif;background:#0f1117;color:#e8eaf6;padding:32px">
        <div style="max-width:420px;margin:auto;background:#13151f;border-radius:12px;padding:32px">
          <h2 style="color:#5B8DEF;margin-top:0">Your Verification Code</h2>
          <p style="font-size:36px;font-weight:bold;letter-spacing:8px;color:#fff;text-align:center">{otp}</p>
          <p style="color:#5a5f78;font-size:13px">Valid for 5 minutes. Don't share this code.</p>
        </div></body></html>"""
        msg.attach(MIMEText(body, "html"))
        ctx = ssl.create_default_context()
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ctx) as s:
            s.login(EMAIL_ADDRESS, EMAIL_PASSWORD)
            s.sendmail(EMAIL_ADDRESS, to_email, msg.as_string())
        return True
    except Exception as e:
        print(f"Email error: {e}")
        return False

# ── Routes ───────────────────────────────────────────────────────────────────
@app.route("/")
def index():
    return render_template("index.html", demo_mode=DEMO_MODE)

@app.route("/admin")
def admin():
    return render_template("admin_dashboard.html")

@app.route("/register")
def register_page():
    return render_template("register.html")

@app.route("/api/send-otp", methods=["POST"])
def send_otp():
    data  = request.get_json()
    email = data.get("email", "").strip().lower()
    ip    = request.remote_addr

    if not email or "@" not in email:
        return jsonify({"success": False, "message": "Invalid email address."})

    if time.time() < lockout_until[email]:
        remaining = int(lockout_until[email] - time.time())
        return jsonify({"success": False, "message": f"Too many attempts. Try again in {remaining}s."})

    now = time.time()
    rate_limit[email] = [t for t in rate_limit[email] if now - t < 300]
    if len(rate_limit[email]) >= 5:
        lockout_until[email] = now + 300
        log_event(email, "LOCKOUT", "Rate limit exceeded", ip)
        return jsonify({"success": False, "message": "Too many requests. Locked out for 5 minutes."})

    otp = generate_otp()
    session["otp"]       = otp
    session["otp_email"] = email
    session["otp_time"]  = time.time()
    session["otp_attempts"] = 0

    rate_limit[email].append(now)

    sent = send_otp_email(email, otp)
    log_event(email, "OTP_SENT" if sent else "OTP_FAILED", "", ip)

    if sent:
        return jsonify({"success": True,  "message": "OTP sent to your email."})
    else:
        # In demo mode, return OTP directly so it still works without email config
        return jsonify({"success": True,  "message": f"[DEMO] OTP: {otp}", "demo_otp": otp})

@app.route("/api/verify-otp", methods=["POST"])
def verify_otp():
    data  = request.get_json()
    email = data.get("email", "").strip().lower()
    otp   = data.get("otp", "").strip()
    ip    = request.remote_addr

    stored_otp   = session.get("otp")
    stored_email = session.get("otp_email")
    otp_time     = session.get("otp_time", 0)
    attempts     = session.get("otp_attempts", 0)

    if attempts >= 5:
        log_event(email, "OTP_BLOCKED", "Too many attempts", ip)
        return jsonify({"success": False, "message": "Too many failed attempts. Request a new OTP."})

    if time.time() - otp_time > 300:
        log_event(email, "OTP_EXPIRED", "", ip)
        return jsonify({"success": False, "message": "OTP expired. Please request a new one."})

    if email != stored_email or otp != stored_otp:
        session["otp_attempts"] = attempts + 1
        log_event(email, "OTP_FAIL", f"Attempt {attempts+1}", ip)
        return jsonify({"success": False, "message": "Incorrect OTP. Please try again."})

    session["otp_verified"] = True
    session["verified_email"] = email
    log_event(email, "OTP_SUCCESS", "", ip)
    return jsonify({"success": True, "message": "OTP verified."})

@app.route("/api/verify-face", methods=["POST"])
def verify_face():
    """In demo mode, face scan always passes after frontend countdown."""
    email = session.get("verified_email", "")
    ip    = request.remote_addr

    if not session.get("otp_verified"):
        return jsonify({"success": False, "message": "OTP verification required first."})

    token = secrets.token_hex(32)
    active_sessions[token] = {
        "email": email, "name": email.split("@")[0].title(),
        "created": datetime.now().isoformat(), "ip": ip
    }
    session["auth_token"] = token
    session["auth_email"] = email

    log_event(email, "AUTH_GRANTED", "Demo face pass", ip)
    return jsonify({"success": True, "email": email,
                    "name": email.split("@")[0].title(),
                    "token": token, "demo": True})

@app.route("/api/logout", methods=["POST"])
def logout():
    token = session.get("auth_token")
    if token and token in active_sessions:
        del active_sessions[token]
    session.clear()
    return jsonify({"success": True})

# ── Admin API ────────────────────────────────────────────────────────────────
@app.route("/api/stats")
def stats():
    conn  = get_logs_db()
    total = conn.execute("SELECT COUNT(*) FROM auth_logs").fetchone()[0]
    today = conn.execute(
        "SELECT COUNT(*) FROM auth_logs WHERE timestamp LIKE ?",
        (datetime.now().strftime("%Y-%m-%d") + "%",)).fetchone()[0]
    granted = conn.execute(
        "SELECT COUNT(*) FROM auth_logs WHERE event='AUTH_GRANTED'").fetchone()[0]
    failed  = conn.execute(
        "SELECT COUNT(*) FROM auth_logs WHERE event IN ('OTP_FAIL','OTP_EXPIRED','LOCKOUT')").fetchone()[0]
    lockouts = conn.execute(
        "SELECT COUNT(*) FROM auth_logs WHERE event='LOCKOUT'").fetchone()[0]
    conn.close()
    return jsonify({"total": total, "today": today, "granted": granted,
                    "failed": failed, "lockouts": lockouts,
                    "active_sessions": len(active_sessions)})

@app.route("/api/admin/logs")
def admin_logs():
    limit  = int(request.args.get("limit", 100))
    offset = int(request.args.get("offset", 0))
    search = request.args.get("search", "")
    event  = request.args.get("event", "")
    conn   = get_logs_db()
    q = "SELECT * FROM auth_logs WHERE 1=1"
    params = []
    if search:
        q += " AND (email LIKE ? OR detail LIKE ?)"; params += [f"%{search}%",f"%{search}%"]
    if event:
        q += " AND event=?"; params.append(event)
    q += " ORDER BY id DESC LIMIT ? OFFSET ?"
    params += [limit, offset]
    rows = conn.execute(q, params).fetchall()
    conn.close()
    return jsonify({"logs": [dict(r) for r in rows]})

@app.route("/api/admin/sessions")
def admin_sessions():
    return jsonify({"sessions": list(active_sessions.values())})

@app.route("/api/admin/activity")
def admin_activity():
    conn = get_logs_db()
    rows = conn.execute("""
        SELECT DATE(timestamp) as day, COUNT(*) as count
        FROM auth_logs
        WHERE timestamp >= DATE('now','-6 days')
        GROUP BY day ORDER BY day""").fetchall()
    conn.close()
    return jsonify({"activity": [dict(r) for r in rows]})

if __name__ == "__main__":
    os.makedirs("flask_session", exist_ok=True)
    app.run(debug=False, host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))

from tkinter import *
from tkinter import messagebox
import random, string, os, threading, sqlite3
import smtplib, ssl
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import cv2, face_recognition
from datetime import datetime
from dotenv import load_dotenv
from face_popup import FaceIDPopup
from dashboard import AdminDashboard
from user_manager import UserManagerWindow, get_all_encodings, init_users_db

load_dotenv()
EMAIL_ADDRESS  = os.getenv("EMAIL_ADDRESS", "")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD", "")

# ── Rate limiter ─────────────────────────────────────────────────────
from collections import defaultdict
import time

class RateLimiter:
    MAX_REQUESTS = 3
    WINDOW_SECS  = 600

    def __init__(self):
        self._log = defaultdict(list)

    def is_allowed(self, email):
        now      = time.time()
        cutoff   = now - self.WINDOW_SECS
        requests = [t for t in self._log[email] if t > cutoff]
        self._log[email] = requests
        if len(requests) >= self.MAX_REQUESTS:
            wait = int(self.WINDOW_SECS - (now - requests[0]))
            return False, wait
        self._log[email].append(now)
        return True, 0

_rate_limiter = RateLimiter()

# ── Session tokens ───────────────────────────────────────────────────
import secrets

SESSION_EXPIRY_MINS = 30

def init_sessions_db():
    conn = sqlite3.connect("mfa_logs.db")
    conn.cursor().execute(
        "CREATE TABLE IF NOT EXISTS sessions ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT,"
        "token TEXT UNIQUE NOT NULL,"
        "email TEXT NOT NULL,"
        "name  TEXT,"
        "created TEXT NOT NULL,"
        "expires TEXT NOT NULL,"
        "revoked INTEGER DEFAULT 0)"
    )
    conn.commit()
    conn.close()

def create_session(email, name=""):
    from datetime import timedelta
    token   = secrets.token_hex(32)
    created = datetime.now()
    expires = created + timedelta(minutes=SESSION_EXPIRY_MINS)
    conn = sqlite3.connect("mfa_logs.db")
    conn.cursor().execute(
        "INSERT INTO sessions (token,email,name,created,expires) VALUES (?,?,?,?,?)",
        (token, email, name,
         created.strftime("%Y-%m-%d %H:%M:%S"),
         expires.strftime("%Y-%m-%d %H:%M:%S")))
    conn.commit()
    conn.close()
    return token, expires

def validate_session(token):
    conn = sqlite3.connect("mfa_logs.db")
    row  = conn.cursor().execute(
        "SELECT email,name,expires,revoked FROM sessions WHERE token=?",
        (token,)).fetchone()
    conn.close()
    if not row:
        return False, None, None
    email, name, expires_str, revoked = row
    if revoked:
        return False, None, None
    if datetime.now() > datetime.strptime(expires_str, "%Y-%m-%d %H:%M:%S"):
        return False, None, None
    return True, email, name

def revoke_session(token):
    conn = sqlite3.connect("mfa_logs.db")
    conn.cursor().execute("UPDATE sessions SET revoked=1 WHERE token=?", (token,))
    conn.commit()
    conn.close()


def send_otp_email(to_address, otp):
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = "Your MFA Verification Code"
        msg["From"]    = f"MFA System <{EMAIL_ADDRESS}>"
        msg["To"]      = to_address
        html = f"""<html><body style="font-family:Segoe UI,sans-serif;background:#0f1117;margin:0;padding:32px;"><div style="max-width:400px;margin:auto;background:#13151f;border-radius:12px;padding:32px;border:1px solid #1e2133;"><p style="color:#5a5f78;font-size:12px;margin:0 0 8px;text-transform:uppercase;letter-spacing:.08em;">MFA System</p><h2 style="color:#e8eaf6;font-size:22px;margin:0 0 24px;">Verification Code</h2><div style="background:#0f1117;border-radius:8px;padding:20px;text-align:center;margin-bottom:24px;"><span style="font-size:36px;font-weight:700;letter-spacing:8px;color:#5B8DEF;">{otp}</span></div><p style="color:#5a5f78;font-size:13px;margin:0;">Expires in <strong style="color:#e8eaf6;">60 seconds</strong>. Do not share.</p></div></body></html>"""
        msg.attach(MIMEText(html, "html"))
        ctx = ssl.create_default_context()
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ctx) as server:
            server.login(EMAIL_ADDRESS, EMAIL_PASSWORD)
            server.sendmail(EMAIL_ADDRESS, to_address, msg.as_string())
        return True, ""
    except Exception as e:
        return False, str(e)

def init_db():
    conn = sqlite3.connect("mfa_logs.db")
    conn.cursor().execute('''CREATE TABLE IF NOT EXISTS auth_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp TEXT, phone TEXT,
        otp_status TEXT, face_status TEXT, result TEXT)''')
    conn.commit(); conn.close()

def log_attempt(email, otp_status, face_status, result):
    conn = sqlite3.connect("mfa_logs.db")
    conn.cursor().execute("INSERT INTO auth_logs VALUES (NULL,?,?,?,?,?)",
        (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), email, otp_status, face_status, result))
    conn.commit(); conn.close()

class App(Tk):
    OTP_EXPIRY   = 60
    MAX_ATTEMPTS = 3

    def __init__(self):
        super().__init__()
        self.geometry("900x660+250+60")
        self.minsize(900, 660)
        self.configure(bg="#0f1117")
        self.resizable(False, False)
        self.title("MFA System")

        self.otp_value       = StringVar()
        self.phone_number    = StringVar()
        self.generated_otp   = ""
        self.otp_entered     = StringVar()
        self.timer_var       = StringVar(value="")
        self.timer_job       = None
        self.time_left       = 0
        self.failed_attempts = 0
        self.locked_out      = False

        init_db()
        init_sessions_db()
        self._build_ui()
        self.load_known_face()

    def _build_ui(self):
        BG   = "#0f1117"
        CARD = "#16181f"
        BLUE = "#5B8DEF"
        GREEN= "#34C47C"
        TEXT = "#e8eaf6"
        MUTED= "#5a5f78"
        DIM  = "#1e2133"
        FONT = "Segoe UI"

        outer = Frame(self, bg=BG)
        outer.pack(fill=BOTH, expand=True, padx=60, pady=0)

        header = Frame(outer, bg=BG)
        header.pack(fill=X, pady=(40, 0))
        Label(header, text="MFA System",
              font=(FONT, 28, "bold"),
              bg=BG, fg=TEXT, anchor=W).pack(fill=X)
        Label(header, text="Multi-factor authentication  —  OTP + Face Recognition",
              font=(FONT, 11),
              bg=BG, fg=MUTED, anchor=W).pack(fill=X, pady=(4, 16))
        Frame(outer, bg=DIM, height=1).pack(fill=X)

        # Step 1
        s1 = Frame(outer, bg=BG)
        s1.pack(fill=X, pady=(20, 0))
        Label(s1, text="STEP 1", font=(FONT, 9), bg=BG, fg=MUTED).pack(anchor=W)
        Label(s1, text="Email address", font=(FONT, 14, "bold"), bg=BG, fg=TEXT).pack(anchor=W, pady=(2,8))
        row1 = Frame(s1, bg=BG)
        row1.pack(fill=X)
        self.phone_entry = Entry(row1, textvariable=self.phone_number,
              width=22, font=(FONT, 13), bg=CARD, fg=TEXT,
              insertbackground=TEXT, relief=FLAT, bd=8)
        self.phone_entry.pack(side=LEFT)
        Button(row1, text="Send OTP", command=self.generate_otp,
               font=(FONT, 11), bg=BLUE, fg="#fff",
               relief=FLAT, cursor="hand2", padx=16, pady=7,
               activebackground="#4a7ade").pack(side=LEFT, padx=(12,0))

        otp_row = Frame(s1, bg=BG)
        otp_row.pack(fill=X, pady=(10, 0))
        self.otp_lbl = Label(otp_row, textvariable=self.otp_value,
                              font=(FONT, 12, "bold"), fg=GREEN, bg=BG)
        self.otp_lbl.pack(side=LEFT)
        self.timer_lbl = Label(otp_row, textvariable=self.timer_var,
                                font=(FONT, 11), fg=MUTED, bg=BG)
        self.timer_lbl.pack(side=LEFT, padx=(16, 0))
        self.attempts_lbl = Label(s1, text="", font=(FONT, 11), fg="#EF5B5B", bg=BG)
        self.attempts_lbl.pack(anchor=W, pady=(4,0))

        Frame(outer, bg=DIM, height=1).pack(fill=X, pady=(20,0))

        # Step 2
        s2 = Frame(outer, bg=BG)
        s2.pack(fill=X, pady=(20, 0))
        Label(s2, text="STEP 2", font=(FONT, 9), bg=BG, fg=MUTED).pack(anchor=W)
        Label(s2, text="Enter OTP", font=(FONT, 14, "bold"), bg=BG, fg=TEXT).pack(anchor=W, pady=(2,8))
        Entry(s2, textvariable=self.otp_entered, width=22,
              font=(FONT, 13), bg=CARD, fg=TEXT,
              insertbackground=TEXT, relief=FLAT, bd=8).pack(anchor=W)

        Frame(outer, bg=DIM, height=1).pack(fill=X, pady=(20,0))

        # Step 3
        s3 = Frame(outer, bg=BG)
        s3.pack(fill=X, pady=(20, 0))
        Label(s3, text="STEP 3", font=(FONT, 9), bg=BG, fg=MUTED).pack(anchor=W)
        Label(s3, text="Verify identity", font=(FONT, 14, "bold"), bg=BG, fg=TEXT).pack(anchor=W, pady=(2,12))
        btn_row = Frame(s3, bg=BG)
        btn_row.pack(anchor=W)
        Button(btn_row, text="Verify Identity",
               command=self.start_verification_thread,
               font=(FONT, 11), bg=GREEN, fg="#0a1f14",
               relief=FLAT, cursor="hand2", padx=16, pady=7,
               activebackground="#2aad6c").pack(side=LEFT)
        Button(btn_row, text="Manage Users", command=self.register_face,
               font=(FONT, 11), bg=CARD, fg=TEXT,
               relief=FLAT, cursor="hand2", padx=16, pady=7,
               activebackground=DIM).pack(side=LEFT, padx=(10,0))
        Button(btn_row, text="Dashboard", command=self.show_dashboard,
               font=(FONT, 11), bg=CARD, fg=MUTED,
               relief=FLAT, cursor="hand2", padx=16, pady=7,
               activebackground=DIM).pack(side=LEFT, padx=(10,0))
        Button(btn_row, text="View Logs", command=self.show_logs,
               font=(FONT, 11), bg=CARD, fg=MUTED,
               relief=FLAT, cursor="hand2", padx=16, pady=7,
               activebackground=DIM).pack(side=LEFT, padx=(10,0))

        Frame(outer, bg=DIM, height=1).pack(fill=X, pady=(24,0))

        footer = Frame(outer, bg=BG)
        footer.pack(fill=X, pady=(12,0))
        self.result_lbl = Label(footer, text="", font=(FONT, 13, "bold"), fg=BLUE, bg=BG)
        self.result_lbl.pack(anchor=W)
        bottom = Frame(footer, bg=BG)
        bottom.pack(fill=X, pady=(6,0))
        self.status_lbl = Label(bottom, text="Idle", font=(FONT, 10), fg=MUTED, bg=BG)
        self.status_lbl.pack(side=LEFT)
        self.face_badge = Label(bottom, text="● No face registered", font=(FONT, 10), fg=MUTED, bg=BG)
        self.face_badge.pack(side=RIGHT)

    def load_known_face(self):
        init_users_db()
        self.user_encodings = get_all_encodings()  # list of (email, name, encoding)
        if self.user_encodings:
            self.known_encoding = self.user_encodings[0][2]
            n = len(self.user_encodings)
            self.face_badge.config(text=f"● {n} user(s) registered", fg="#34C47C")
            self.set_status("Ready.")
        else:
            self.known_encoding = None
            self.face_badge.config(text="● No users registered", fg="#5a5f78")
            self.set_status("Register users via Manage Users.")

    def register_face(self):
        win = UserManagerWindow(self)
        self.wait_window(win)
        self.load_known_face()

    def generate_otp(self):
        if self.locked_out:
            self.result_lbl.config(text="Account locked.", fg="#EF5B5B")
            return
        email = self.phone_number.get().strip()
        if "@" not in email or "." not in email:
            self.otp_value.set("Enter a valid email address")
            return

        allowed, wait = _rate_limiter.is_allowed(email)
        if not allowed:
            mins = wait // 60
            secs = wait % 60
            self.otp_value.set(f"Too many requests — try again in {mins}m {secs}s")
            self.otp_lbl.config(fg="#EF5B5B")
            self.set_status("Rate limit reached.")
            return

        if self.timer_job:
            self.after_cancel(self.timer_job)
        otp = ''.join(random.choices(string.digits, k=6))
        self.generated_otp = otp
        self.otp_lbl.config(fg="#34C47C")
        self.otp_value.set(f"Sending OTP to {email}...")
        self.set_status("Sending OTP...")
        threading.Thread(target=self._send_email_otp, args=(email, otp), daemon=True).start()

    def _send_email_otp(self, email, otp):
        success, err = send_otp_email(email, otp)
        if success:
            self.otp_value.set(f"OTP sent to {email}")
            self.set_status("OTP sent. Check your inbox.")
        else:
            self.otp_value.set(f"Failed to send: {err[:60]}")
            self.set_status("Email failed. Check .env credentials.")
            self.generated_otp = ""
            return
        self.time_left = self.OTP_EXPIRY
        self.after(0, self.update_timer)

    def update_timer(self):
        if self.time_left > 0 and self.generated_otp:
            col = "#34C47C" if self.time_left > 20 else "#EF5B5B"
            self.timer_var.set(f"  ·  expires in {self.time_left}s")
            self.timer_lbl.config(fg=col)
            self.time_left -= 1
            self.timer_job = self.after(1000, self.update_timer)
        else:
            if self.generated_otp:
                self.generated_otp = ""
                self.otp_value.set("OTP expired — generate a new one")
                self.otp_lbl.config(fg="#EF5B5B")
                self.timer_var.set("")
                self.set_status("OTP expired.")

    def start_verification_thread(self):
        if self.locked_out:
            self.result_lbl.config(text="Account locked.", fg="#EF5B5B")
            return
        if not self.generated_otp:
            self.result_lbl.config(text="Generate an OTP first.", fg="#5a5f78")
            return

        email    = self.phone_number.get().strip()
        entered  = self.otp_entered.get().strip()
        expected = self.generated_otp.strip()

        if entered != expected:
            self.failed_attempts += 1
            if self.failed_attempts >= self.MAX_ATTEMPTS:
                self.locked_out = True
                self.result_lbl.config(
                    text="Account locked after 3 failed attempts.", fg="#EF5B5B")
                self.attempts_lbl.config(
                    text=f"{self.failed_attempts} of {self.MAX_ATTEMPTS} attempts used")
                log_attempt(email, "FAILED", "SKIPPED", "LOCKED OUT")
                return
            rem = self.MAX_ATTEMPTS - self.failed_attempts
            self.result_lbl.config(text=f"Incorrect OTP — {rem} attempt(s) left.", fg="#EF5B5B")
            self.attempts_lbl.config(text=f"{self.failed_attempts} of {self.MAX_ATTEMPTS} attempts used")
            log_attempt(email, "FAILED", "SKIPPED", "DENIED")
            return

        self.failed_attempts = 0
        self.attempts_lbl.config(text="")
        self.set_status("OTP verified — launching face scan...")

        if not self.user_encodings:
            self.result_lbl.config(text="No face registered.", fg="#F5A623")
            return

        if self.timer_job:
            self.after_cancel(self.timer_job)
            self.timer_job = None

        # ── FIX: pass full user_encodings list to FaceIDPopup ──────────
        popup = FaceIDPopup(self, known_encoding=None,
                            mode="verify",
                            user_encodings=self.user_encodings)
        self.wait_window(popup)

        if popup.result == "granted":
            # Use matched user's email/name for session if available
            matched_email = email
            matched_name  = ""
            if popup.matched_user:
                matched_email, matched_name = popup.matched_user

            token, expires = create_session(matched_email, matched_name)
            log_attempt(matched_email, "PASSED", "PASSED", "GRANTED")
            self._full_reset()
            name_str = f" — Welcome, {matched_name}!" if matched_name else ""
            self.result_lbl.config(text=f"Access granted{name_str}", fg="#34C47C")
            self.set_status(f"Session active until {expires.strftime('%H:%M:%S')}.")
            self.after(4000, lambda: self.result_lbl.config(text=""))
        elif popup.result == "cancelled":
            self.result_lbl.config(text="Face scan cancelled.", fg="#5a5f78")
            self.set_status("Cancelled.")
            log_attempt(email, "PASSED", "CANCELLED", "DENIED")
        elif popup.face_matched and not popup.blink_detected:
            self.result_lbl.config(text="Liveness check failed.", fg="#F5A623")
            self.set_status("Blink not detected.")
            log_attempt(email, "PASSED", "LIVENESS FAIL", "DENIED")
        else:
            self.result_lbl.config(text="Access denied — face not recognised.", fg="#EF5B5B")
            self.set_status("Face verification failed.")
            log_attempt(email, "PASSED", "FAILED", "DENIED")

    def _full_reset(self):
        if self.timer_job:
            self.after_cancel(self.timer_job)
            self.timer_job = None
        self.generated_otp   = ""
        self.failed_attempts = 0
        self.locked_out      = False
        self.time_left       = 0
        self.phone_number.set("")
        self.otp_value.set("")
        self.otp_entered.set("")
        self.timer_var.set("")
        self.otp_lbl.config(fg="#34C47C")
        self.timer_lbl.config(fg="#5a5f78")
        self.attempts_lbl.config(text="")
        self.result_lbl.config(text="")
        self.update_idletasks()

    def show_dashboard(self):
        AdminDashboard(self)

    def show_logs(self):
        win = Toplevel(self)
        win.title("Authentication Logs")
        win.geometry("820x420")
        win.configure(bg="#0f1117")
        Label(win, text="Authentication Logs",
              font=("Segoe UI", 15, "bold"),
              bg="#0f1117", fg="#e8eaf6").pack(pady=14)
        f  = Frame(win, bg="#0f1117")
        f.pack(fill=BOTH, expand=True, padx=12, pady=4)
        sb = Scrollbar(f); sb.pack(side=RIGHT, fill=Y)
        lb = Listbox(f, yscrollcommand=sb.set,
                     font=("Segoe UI", 10), bg="#13151f",
                     fg="#e8eaf6", selectbackground="#5B8DEF",
                     bd=0, relief=FLAT)
        lb.pack(fill=BOTH, expand=True)
        sb.config(command=lb.yview)
        lb.insert(END, f"{'Timestamp':<22} {'Email':<28} {'OTP':<10} {'Face':<16} {'Result'}")
        lb.insert(END, "─"*90)
        conn = sqlite3.connect("mfa_logs.db")
        rows = conn.cursor().execute(
            "SELECT timestamp,phone,otp_status,face_status,result "
            "FROM auth_logs ORDER BY id DESC").fetchall()
        conn.close()
        for r in rows:
            lb.insert(END, f"{r[0]:<22} {r[1]:<28} {r[2]:<10} {r[3]:<16} {r[4]}")
        if not rows:
            lb.insert(END, "  No logs yet.")

    def set_status(self, msg):
        self.status_lbl.config(text=msg)

if __name__ == "__main__":
    app = App()
    app.mainloop()
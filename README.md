# MFA System — Multi-Factor Authentication

A full-stack multi-factor authentication system built from scratch in Python, combining **email OTP**, **face recognition**, and **liveness detection**. Available as both a desktop app (Tkinter) and a web app (Flask).

![Python](https://img.shields.io/badge/Python-3.10+-blue) ![Flask](https://img.shields.io/badge/Flask-3.0-green) ![OpenCV](https://img.shields.io/badge/OpenCV-4.9-red) ![SQLite](https://img.shields.io/badge/SQLite-3-lightgrey) ![Render](https://img.shields.io/badge/Deployed-Render-purple)

🔗 **Live Demo:** https://mfa-system-j4q6.onrender.com  
📁 **Repo:** https://github.com/chapranaatharva/mfa-system

> ⚡ The live demo uses simulated face recognition (real OTP + webcam UI). Full face recognition runs on the desktop app.

---

## Features

- **Email OTP** — 6-digit code with 60s expiry, sent via Gmail SMTP/TLS. Rate-limited to 3 requests per 10 minutes per address
- **Face Recognition** — matches live webcam frame against registered users using `face_recognition` (dlib), tolerance-based 128-dimension encoding comparison
- **Liveness Detection** — Eye Aspect Ratio (EAR) blink detection prevents photo/video spoofing attacks
- **Session Tokens** — cryptographically secure 64-char hex tokens (secrets.token_hex), 30-min expiry, stored server-side
- **Rate Limiting** — sliding window, max 3 OTP requests per 10 minutes per email
- **Lockout** — account locked after 3 failed OTP attempts
- **Admin Dashboard** — real-time auth logs, stats, active sessions, 7-day activity chart, auto-refresh every 15s
- **Web Registration** — 3-step webcam capture at /register: enter details → countdown capture → confirm & save
- **Multi-user** — register and authenticate multiple users, face matching iterates all registered encodings
- **Dual interface** — Tkinter desktop app + Flask web app sharing the same SQLite databases

---

## Project Structure

```
mfa-system/
├── main.py                     # Tkinter desktop app (main entry point)
├── face_popup.py               # Face recognition + liveness detection popup
├── user_manager.py             # User registration UI (Tkinter)
├── dashboard.py                # Admin dashboard (Tkinter)
├── flask_app/
│   ├── app.py                  # Flask web application (full version)
│   └── templates/
│       ├── index.html          # MFA login flow
│       ├── register.html       # Web-based face registration
│       └── admin_dashboard.html
├── render_deploy/              # Render-ready demo version (no dlib)
│   ├── app.py
│   ├── requirements.txt
│   └── templates/
├── .gitignore
└── README.md
```

---

## How It Works

```
User enters email
      ↓
OTP generated → sent via Gmail SMTP (60s expiry, rate-limited)
      ↓
User enters OTP (max 3 attempts before lockout)
      ↓
Webcam activates → frame captured → converted to base64
      ↓
Flask decodes image → face_recognition extracts 128-dim encoding
      ↓
Compare against all registered users (tolerance 0.50)
      ↓
EAR blink detection (liveness check)
      ↓
Session token issued (32-byte hex, stored in DB)
      ↓
Access granted ✓
```

---

## Security Architecture

| Threat | Mitigation |
|---|---|
| OTP brute-force | 3-attempt lockout + rate limiting (3 req / 10 min) |
| Replay attacks | OTP single-use, 60s TTL |
| Photo/video spoofing | EAR blink liveness detection |
| Session hijacking | Tokens are random 32-byte hex, server-side, revocable |
| Credential exposure | Environment variables via python-dotenv, never committed |

**Known limitations:** No HTTPS enforcement, no CSRF protection on API routes, admin dashboard unprotected, pickle unsafe if DB compromised. Production hardening would require Redis for OTP store, HTTPS, CSRF tokens, and encrypted face encodings.

---

## Setup (Local)

### 1. Install dependencies
```bash
pip install flask flask-session face-recognition opencv-python python-dotenv
```

> **Note:** `dlib` (required by `face_recognition`) needs CMake and a C++ compiler.  
> On Windows: install [Visual Studio Build Tools](https://visualstudio.microsoft.com/downloads/) first.

### 2. Configure environment
Create a `.env` file in the project root:
```
EMAIL_ADDRESS=you@gmail.com
EMAIL_PASSWORD=your_16_char_app_password
```

> Use a Gmail **App Password** — not your account password.  
> Enable 2FA → Google Account → Security → App Passwords → Generate.

### 3. Run desktop app
```bash
python main.py
```

### 4. Run web app
```bash
cd flask_app
python app.py
```

Visit:
- `http://localhost:5000` — MFA login
- `http://localhost:5000/register` — Register new user
- `http://localhost:5000/admin` — Admin dashboard

---

## Tech Stack

- **Python 3.10+**
- **Tkinter** — desktop GUI
- **Flask + Flask-Session** — web framework, server-side sessions
- **OpenCV** — webcam capture, image processing, BGR→RGB conversion
- **face_recognition** (dlib) — 128-dimension face encoding and matching
- **SQLite** — auth logs, session tokens, face encodings (two separate DBs)
- **Gmail SMTP over TLS** — OTP delivery
- **Gunicorn** — production WSGI server
- **Render** — cloud deployment
- **Vanilla JS/HTML/CSS** — web frontend, webcam capture, fetch API

---

## Author

**Atharva Chaprana**  
B.Tech Computer Science, Amity University (Expected 2027)  
📧 chapranaatharva@gmail.com  
🔗 github.com/chapranaatharva

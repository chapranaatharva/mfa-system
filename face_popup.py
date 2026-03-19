from tkinter import *
import threading
import cv2
import face_recognition
import math

class FaceIDPopup(Toplevel):
    W, H = 320, 420

    STATES = {
        "align":      ("Position your face",     "Move into the frame",         "#5B8DEF", "#13151f"),
        "scanning":   ("Scanning",                "Hold still...",               "#9B72F5", "#13151f"),
        "blink":      ("Blink to confirm",        "Face recognised",             "#34C47C", "#13151f"),
        "granted":    ("Verified",                "Access granted",              "#34C47C", "#0d2018"),
        "denied":     ("Not recognised",          "Please try again",            "#EF5B5B", "#200d0d"),
        "liveness":   ("Liveness failed",         "Possible spoof detected",     "#F5A623", "#1f160a"),
        "cancelled":  ("Cancelled",               "Scan was cancelled",          "#5a5f78", "#13151f"),
        "register":   ("Register face",           "Look at the camera",          "#5B8DEF", "#13151f"),
        "capturing":  ("Hold still",              "Capturing...",                "#9B72F5", "#13151f"),
        "registered": ("Face saved",              "Registration complete",       "#34C47C", "#0d2018"),
    }

    ARC_CONFIGS = {
        "align":      [(60, 240, 1.0, 1.0)],
        "scanning":   [(80, 220, 1.6, 1.0), (40, 260, 1.6, 0.4)],
        "blink":      [(100, 200, 2.0, 1.0), (60, 240, 2.0, 0.5)],
        "capturing":  [(80, 220, 1.6, 1.0), (40, 260, 1.6, 0.4)],
        "register":   [(60, 240, 1.0, 1.0)],
    }

    def __init__(self, parent, known_encoding=None, mode="verify", user_encodings=None):
        super().__init__(parent)
        self.known_encoding = known_encoding
        # ── FIX: use full user_encodings list for multi-user matching ──
        if user_encodings:
            self.user_encodings = user_encodings
        elif known_encoding is not None:
            self.user_encodings = [(None, None, known_encoding)]
        else:
            self.user_encodings = []

        self.matched_user    = None   # (email, name) of matched user
        self.mode            = mode
        self.result          = None
        self.face_matched    = False
        self.blink_detected  = False
        self.captured_frame  = None
        self.prev_ear        = None
        self.frame_count     = 0
        self.face_locations  = []
        self.face_enc_list   = []
        self.land_list       = []
        self._angle          = 0.0
        self._state          = "align" if mode == "verify" else "register"
        self._cap            = None
        self._running        = True
        self._face_in_frame  = False
        self._capture_ticks  = 0

        self.overrideredirect(True)
        self.attributes("-topmost", True)
        px = parent.winfo_x() + parent.winfo_width()//2  - self.W//2
        py = parent.winfo_y() + parent.winfo_height()//2 - self.H//2
        self.geometry(f"{self.W}x{self.H}+{px}+{py}")
        self.configure(bg="#0f1117")
        self.grab_set()

        self._build_ui()
        self._start_camera()
        self._tick_ui()

    def _build_ui(self):
        self.canvas = Canvas(self, width=self.W, height=self.H,
                             bg="#0f1117", highlightthickness=0)
        self.canvas.pack(fill=BOTH, expand=True)
        self.canvas.bind("<Escape>", self._cancel)
        self.canvas.focus_set()

    def _tick_ui(self):
        if not self._running and self.result in (
                "granted","denied","liveness","registered","cancelled","error"):
            self._draw()
            return
        self._draw()
        self.after(16, self._tick_ui)

    def _draw(self):
        c = self.canvas
        c.delete("all")
        W, H = self.W, self.H
        s = self._state

        title, subtitle, accent, bg = self.STATES.get(s, self.STATES["align"])

        c.create_rectangle(0, 0, W, H, fill=bg, outline="")
        c.create_rectangle(1, 1, W-1, H-1, fill="", outline="#1a1d2e", width=1)

        cx, cy = W // 2, 162
        R = 72

        c.create_oval(cx-R-6, cy-R-6, cx+R+6, cy+R+6,
                      fill="", outline="#1e2133", width=1)

        arcs = self.ARC_CONFIGS.get(s, [])
        if arcs:
            speed = arcs[0][2]
            self._angle = (self._angle + speed * 3.5) % 360

            for i, (dash, gap, spd, alpha) in enumerate(arcs):
                offset  = 180 if i == 1 else 0
                a_start = (self._angle * (1 if i == 0 else -0.7) + offset) % 360
                col     = accent if i == 0 else self._dim_color(accent)
                c.create_arc(cx-R-4, cy-R-4, cx+R+4, cy+R+4,
                             start=a_start, extent=dash,
                             outline=col, width=2 if i == 0 else 1.5,
                             style=ARC)

        inner = R - 10
        c.create_oval(cx-inner, cy-inner, cx+inner, cy+inner,
                      fill="#0f1117", outline="#1e2133", width=1)

        if s in ("granted", "registered"):
            c.create_line(cx-16, cy+2,  cx-5,  cy+14, fill=accent, width=2.5,
                          capstyle=ROUND, joinstyle=ROUND)
            c.create_line(cx-5,  cy+14, cx+17, cy-12, fill=accent, width=2.5,
                          capstyle=ROUND, joinstyle=ROUND)
        elif s in ("denied", "liveness"):
            c.create_line(cx-14, cy-14, cx+14, cy+14, fill=accent, width=2.5, capstyle=ROUND)
            c.create_line(cx+14, cy-14, cx-14, cy+14, fill=accent, width=2.5, capstyle=ROUND)
        elif s == "cancelled":
            c.create_line(cx-14, cy, cx+14, cy, fill=accent, width=2.5, capstyle=ROUND)
        else:
            dot_r = 5
            c.create_oval(cx-dot_r, cy-dot_r, cx+dot_r, cy+dot_r, fill=accent, outline="")
            ring_r = 14
            c.create_oval(cx-ring_r, cy-ring_r, cx+ring_r, cy+ring_r,
                          fill="", outline=accent, width=1)

        dot_y = cy + R + 28
        steps = (["align","scanning","blink","granted"]
                 if self.mode == "verify"
                 else ["register","capturing","registered"])
        try:    cur = steps.index(s)
        except: cur = -1
        gap  = 12
        sx   = cx - (len(steps)-1) * gap // 2
        for i in range(len(steps)):
            dx   = sx + i * gap
            done = i <= cur
            r2   = 3 if done else 2
            col  = accent if done else "#22253a"
            c.create_oval(dx-r2, dot_y-r2, dx+r2, dot_y+r2, fill=col, outline="")

        if s == "capturing" and self._capture_ticks > 0:
            bw   = 180
            prog = max(0.0, 1.0 - self._capture_ticks / 30)
            c.create_rectangle(cx-bw//2, dot_y+12, cx+bw//2, dot_y+16,
                               fill="#1e2133", outline="")
            c.create_rectangle(cx-bw//2, dot_y+12,
                               cx-bw//2 + int(bw*prog), dot_y+16,
                               fill=accent, outline="")

        ty = dot_y + 32
        c.create_text(cx, ty,      text=subtitle, font=("Segoe UI", 11),
                      fill="#454869", anchor=CENTER)
        c.create_text(cx, ty + 28, text=title,    font=("Segoe UI", 16, "bold"),
                      fill="#e8eaf6", anchor=CENTER)
        c.create_text(cx, H - 16,  text="ESC  cancel", font=("Segoe UI", 9),
                      fill="#2a2d3e", anchor=CENTER)

    def _dim_color(self, hex_color):
        dim_map = {
            "#5B8DEF": "#1e3a6e",
            "#9B72F5": "#3a2070",
            "#34C47C": "#0e4a2a",
            "#EF5B5B": "#6e1e1e",
            "#F5A623": "#6e3e0e",
            "#5a5f78": "#22253a",
        }
        return dim_map.get(hex_color, "#22253a")

    def _start_camera(self):
        self._cap = cv2.VideoCapture(0)
        threading.Thread(target=self._scan_loop, daemon=True).start()

    def _scan_loop(self):
        cap = self._cap
        if not cap or not cap.isOpened():
            self.result   = "error"
            self._running = False
            return

        done_ticks = 0
        terminal   = ("granted","denied","liveness","registered","cancelled")

        while True:
            if not self._running:
                break
            ret, frame = cap.read()
            if not ret:
                continue
            frame = cv2.flip(frame, 1)
            self.frame_count += 1

            if self._state in terminal:
                done_ticks += 1
                if done_ticks > 55:
                    break
                continue

            if self.frame_count % 4 == 0:
                small  = cv2.resize(frame, (0, 0), fx=0.25, fy=0.25)
                rgb_s  = cv2.cvtColor(small, cv2.COLOR_BGR2RGB)
                locs_s = face_recognition.face_locations(rgb_s, model="hog")
                self.face_locations = [(t*4, r*4, b*4, l*4) for t, r, b, l in locs_s]
                rgb_f  = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                self.face_enc_list  = face_recognition.face_encodings(rgb_f, self.face_locations)
                self.land_list      = face_recognition.face_landmarks(rgb_f, self.face_locations)
                self._face_in_frame = len(self.face_locations) > 0

                # ── Register mode ─────────────────────────────────────
                if self.mode == "register":
                    if self._face_in_frame and self._state == "register":
                        self._state         = "capturing"
                        self._capture_ticks = 30
                    if self._state == "capturing":
                        self._capture_ticks -= 1
                        if self._capture_ticks <= 0:
                            self.captured_frame = frame.copy()
                            self._state = "registered"
                            self.result = "registered"

                # ── Verify mode ───────────────────────────────────────
                elif self.mode == "verify":
                    if self._face_in_frame and self._state == "align":
                        self._state = "scanning"

                    for i, enc in enumerate(self.face_enc_list):

                        # ── FIX: iterate all registered users ─────────
                        if not self.face_matched:
                            for u_email, u_name, u_enc in self.user_encodings:
                                m = face_recognition.compare_faces(
                                    [u_enc], enc, tolerance=0.50)
                                if m[0]:
                                    self.face_matched = True
                                    self.matched_user = (u_email, u_name)
                                    if self._state == "scanning":
                                        self._state = "blink"
                                    break

                        # ── Liveness: EAR blink detection ─────────────
                        if i < len(self.land_list):
                            lm = self.land_list[i]
                            if 'left_eye' in lm:
                                pts = lm['left_eye']
                                if len(pts) >= 6:
                                    A   = abs(pts[1][1] - pts[5][1])
                                    B   = abs(pts[2][1] - pts[4][1])
                                    C   = abs(pts[0][0] - pts[3][0])
                                    ear = (A + B) / (2.0 * C) if C != 0 else 0.3
                                    if (self.prev_ear is not None
                                            and self.prev_ear > 0.2
                                            and ear < 0.15):
                                        self.blink_detected = True
                                    self.prev_ear = ear

                    if self.face_matched and self.blink_detected:
                        self._state = "granted"
                        self.result = "granted"

        cap.release()
        self.after(0, self._finish)

    def _finish(self):
        self._running = False
        if self.result is None:
            self.result = "cancelled"
        self.grab_release()
        self.destroy()

    def _cancel(self, event=None):
        self._state   = "cancelled"
        self.result   = "cancelled"
        self._running = False
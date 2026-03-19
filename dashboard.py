from tkinter import *
from tkinter import ttk
import sqlite3
from datetime import datetime

class AdminDashboard(Toplevel):
    def __init__(self, parent):
        super().__init__(parent)
        self.title("Admin Dashboard")
        self.geometry("820x580+150+80")
        self.configure(bg="#0f1117")
        self.resizable(False, False)

        self._build_ui()
        self._load_data()
        self._auto_refresh()

    def _build_ui(self):
        BG    = "#0f1117"
        CARD  = "#13151f"
        DIM   = "#1e2133"
        TEXT  = "#e8eaf6"
        MUTED = "#5a5f78"
        FONT  = "Segoe UI"

        # ── Header ────────────────────────────────────────────────
        hdr = Frame(self, bg=BG)
        hdr.pack(fill=X, padx=40, pady=(28, 0))
        Label(hdr, text="Admin Dashboard",
              font=(FONT, 20, "bold"), bg=BG, fg=TEXT).pack(side=LEFT)
        self.refresh_lbl = Label(hdr, text="",
                                  font=(FONT, 10), bg=BG, fg=MUTED)
        self.refresh_lbl.pack(side=RIGHT, pady=6)
        Button(hdr, text="Refresh", command=self._load_data,
               font=(FONT, 10), bg=CARD, fg=MUTED,
               relief=FLAT, cursor="hand2", padx=12, pady=4).pack(side=RIGHT, padx=8)

        Frame(self, bg=DIM, height=1).pack(fill=X, padx=40, pady=(12, 0))

        # ── Stat cards ────────────────────────────────────────────
        cards_frame = Frame(self, bg=BG)
        cards_frame.pack(fill=X, padx=40, pady=(20, 0))

        self.stat_cards = {}
        stats = [
            ("total",   "Total attempts", TEXT),
            ("granted", "Granted",        "#34C47C"),
            ("denied",  "Denied",         "#EF5B5B"),
            ("rate",    "Success rate",   "#5B8DEF"),
            ("locked",  "Lockouts",       "#F5A623"),
        ]
        for key, label, color in stats:
            card = Frame(cards_frame, bg=CARD, width=138, height=80)
            card.pack(side=LEFT, padx=(0, 10))
            card.pack_propagate(False)
            Label(card, text=label, font=(FONT, 10),
                  bg=CARD, fg=MUTED).place(x=14, y=14)
            val_lbl = Label(card, text="—", font=(FONT, 20, "bold"),
                            bg=CARD, fg=color)
            val_lbl.place(x=14, y=38)
            self.stat_cards[key] = val_lbl

        Frame(self, bg=DIM, height=1).pack(fill=X, padx=40, pady=(20, 0))

        # ── Recent activity table ─────────────────────────────────
        tbl_hdr = Frame(self, bg=BG)
        tbl_hdr.pack(fill=X, padx=40, pady=(16, 8))
        Label(tbl_hdr, text="Recent activity",
              font=(FONT, 13, "bold"), bg=BG, fg=TEXT).pack(side=LEFT)
        Label(tbl_hdr, text="last 50 records",
              font=(FONT, 10), bg=BG, fg=MUTED).pack(side=LEFT, padx=(8,0), pady=2)

        # Treeview styled
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("D.Treeview",
                         background="#13151f",
                         foreground="#e8eaf6",
                         fieldbackground="#13151f",
                         rowheight=28,
                         font=("Segoe UI", 10))
        style.configure("D.Treeview.Heading",
                         background="#1e2133",
                         foreground="#5a5f78",
                         font=("Segoe UI", 10),
                         relief="flat")
        style.map("D.Treeview",
                  background=[("selected", "#1e3a6e")],
                  foreground=[("selected", "#e8eaf6")])

        tbl_frame = Frame(self, bg="#13151f")
        tbl_frame.pack(fill=BOTH, expand=True, padx=40, pady=(0, 24))

        cols = ("timestamp", "email", "otp", "face", "result")
        self.tree = ttk.Treeview(tbl_frame, columns=cols,
                                  show="headings", style="D.Treeview")

        self.tree.heading("timestamp", text="Timestamp")
        self.tree.heading("email",     text="Email")
        self.tree.heading("otp",       text="OTP")
        self.tree.heading("face",      text="Face")
        self.tree.heading("result",    text="Result")

        self.tree.column("timestamp", width=160, anchor=W)
        self.tree.column("email",     width=220, anchor=W)
        self.tree.column("otp",       width=90,  anchor=CENTER)
        self.tree.column("face",      width=120, anchor=CENTER)
        self.tree.column("result",    width=110, anchor=CENTER)

        sb = Scrollbar(tbl_frame, command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side=LEFT, fill=BOTH, expand=True)
        sb.pack(side=RIGHT, fill=Y)

        # Tag colors for result column
        self.tree.tag_configure("GRANTED",    foreground="#34C47C")
        self.tree.tag_configure("DENIED",     foreground="#EF5B5B")
        self.tree.tag_configure("LOCKED OUT", foreground="#F5A623")

    def _load_data(self):
        try:
            conn = sqlite3.connect("mfa_logs.db")
            c    = conn.cursor()

            # Stats
            total   = c.execute("SELECT COUNT(*) FROM auth_logs").fetchone()[0]
            granted = c.execute("SELECT COUNT(*) FROM auth_logs WHERE result='GRANTED'").fetchone()[0]
            denied  = c.execute("SELECT COUNT(*) FROM auth_logs WHERE result='DENIED'").fetchone()[0]
            locked  = c.execute("SELECT COUNT(*) FROM auth_logs WHERE result='LOCKED OUT'").fetchone()[0]
            rate    = f"{int(granted/total*100)}%" if total > 0 else "—"

            self.stat_cards["total"].config(text=str(total))
            self.stat_cards["granted"].config(text=str(granted))
            self.stat_cards["denied"].config(text=str(denied))
            self.stat_cards["locked"].config(text=str(locked))
            self.stat_cards["rate"].config(text=rate)

            # Table
            for row in self.tree.get_children():
                self.tree.delete(row)

            rows = c.execute(
                "SELECT timestamp, phone, otp_status, face_status, result "
                "FROM auth_logs ORDER BY id DESC LIMIT 50"
            ).fetchall()

            for r in rows:
                tag = r[4] if r[4] in ("GRANTED","DENIED","LOCKED OUT") else ""
                self.tree.insert("", END, values=r, tags=(tag,))

            # Active sessions count
            active = conn.cursor().execute(
                "SELECT COUNT(*) FROM sessions WHERE revoked=0 AND expires > datetime('now')"
            ).fetchone()[0] if "sessions" in [r[0] for r in conn.cursor().execute(
                "SELECT name FROM sqlite_master WHERE type='table'").fetchall()] else 0
            self.stat_cards["total"].config(text=str(total))

            conn.close()
            self.refresh_lbl.config(
                text=f"Last updated {datetime.now().strftime('%H:%M:%S')}  ·  {active} active session(s)")

        except Exception as e:
            self.refresh_lbl.config(text=f"Error: {e}")

    def _auto_refresh(self):
        self._load_data()
        self.after(10000, self._auto_refresh)   # refresh every 10s
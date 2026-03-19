from tkinter import *
from tkinter import messagebox, ttk
import sqlite3
import face_recognition
import cv2
import numpy as np
import pickle
import os
from face_popup import FaceIDPopup

DB = "users.db"

def init_users_db():
    conn = sqlite3.connect(DB)
    conn.cursor().execute('''CREATE TABLE IF NOT EXISTS users (
        id        INTEGER PRIMARY KEY AUTOINCREMENT,
        email     TEXT UNIQUE NOT NULL,
        name      TEXT NOT NULL,
        encoding  BLOB NOT NULL,
        created   TEXT NOT NULL
    )''')
    conn.commit()
    conn.close()

def get_all_users():
    conn = sqlite3.connect(DB)
    rows = conn.cursor().execute(
        "SELECT id, email, name, created FROM users ORDER BY id DESC"
    ).fetchall()
    conn.close()
    return rows

def get_all_encodings():
    """Returns list of (email, name, encoding) for face matching."""
    conn  = sqlite3.connect(DB)
    rows  = conn.cursor().execute(
        "SELECT email, name, encoding FROM users").fetchall()
    conn.close()
    result = []
    for email, name, blob in rows:
        enc = pickle.loads(blob)
        result.append((email, name, enc))
    return result

def add_user(email, name, encoding):
    try:
        conn = sqlite3.connect(DB)
        conn.cursor().execute(
            "INSERT INTO users (email, name, encoding, created) VALUES (?,?,?,datetime('now'))",
            (email, name, pickle.dumps(encoding)))
        conn.commit()
        conn.close()
        return True, ""
    except sqlite3.IntegrityError:
        return False, "Email already registered."
    except Exception as e:
        return False, str(e)

def delete_user(user_id):
    conn = sqlite3.connect(DB)
    conn.cursor().execute("DELETE FROM users WHERE id=?", (user_id,))
    conn.commit()
    conn.close()


class UserManagerWindow(Toplevel):
    def __init__(self, parent):
        super().__init__(parent)
        self.title("User Management")
        self.geometry("760x520+160+100")
        self.configure(bg="#0f1117")
        self.resizable(False, False)
        init_users_db()
        self._build_ui()
        self._load_users()

    def _build_ui(self):
        BG   = "#0f1117"
        CARD = "#13151f"
        DIM  = "#1e2133"
        TEXT = "#e8eaf6"
        MUTED= "#5a5f78"
        GREEN= "#34C47C"
        FONT = "Segoe UI"

        # Header
        hdr = Frame(self, bg=BG)
        hdr.pack(fill=X, padx=40, pady=(28,0))
        Label(hdr, text="User Management",
              font=(FONT, 20, "bold"), bg=BG, fg=TEXT).pack(side=LEFT)

        Frame(self, bg=DIM, height=1).pack(fill=X, padx=40, pady=(12,0))

        # Add user form
        form = Frame(self, bg=BG)
        form.pack(fill=X, padx=40, pady=(20,0))

        Label(form, text="Name", font=(FONT, 10),
              bg=BG, fg=MUTED).grid(row=0, column=0, sticky=W)
        Label(form, text="Email", font=(FONT, 10),
              bg=BG, fg=MUTED).grid(row=0, column=1, sticky=W, padx=(16,0))

        self.name_var  = StringVar()
        self.email_var = StringVar()

        Entry(form, textvariable=self.name_var, width=20,
              font=(FONT, 12), bg=CARD, fg=TEXT,
              insertbackground=TEXT, relief=FLAT, bd=6
              ).grid(row=1, column=0, sticky=W, pady=(4,0))
        Entry(form, textvariable=self.email_var, width=26,
              font=(FONT, 12), bg=CARD, fg=TEXT,
              insertbackground=TEXT, relief=FLAT, bd=6
              ).grid(row=1, column=1, sticky=W, padx=(16,0), pady=(4,0))
        Button(form, text="Register Face",
               command=self._register_new_user,
               font=(FONT, 11), bg=GREEN, fg="#0a1f14",
               relief=FLAT, cursor="hand2", padx=14, pady=6
               ).grid(row=1, column=2, padx=(16,0), pady=(4,0))

        self.form_msg = Label(form, text="",
                               font=(FONT, 10), bg=BG, fg=MUTED)
        self.form_msg.grid(row=2, column=0, columnspan=3,
                            sticky=W, pady=(6,0))

        Frame(self, bg=DIM, height=1).pack(fill=X, padx=40, pady=(16,0))

        # User table
        tbl_hdr = Frame(self, bg=BG)
        tbl_hdr.pack(fill=X, padx=40, pady=(12,8))
        Label(tbl_hdr, text="Registered users",
              font=(FONT, 13, "bold"), bg=BG, fg=TEXT).pack(side=LEFT)
        self.count_lbl = Label(tbl_hdr, text="",
                                font=(FONT, 10), bg=BG, fg=MUTED)
        self.count_lbl.pack(side=LEFT, padx=(8,0), pady=2)

        style = ttk.Style()
        style.theme_use("clam")
        style.configure("U.Treeview",
                         background="#13151f", foreground="#e8eaf6",
                         fieldbackground="#13151f", rowheight=28,
                         font=("Segoe UI", 10))
        style.configure("U.Treeview.Heading",
                         background="#1e2133", foreground="#5a5f78",
                         font=("Segoe UI", 10), relief="flat")
        style.map("U.Treeview",
                  background=[("selected","#1e3a6e")],
                  foreground=[("selected","#e8eaf6")])

        tbl_frame = Frame(self, bg="#13151f")
        tbl_frame.pack(fill=BOTH, expand=True, padx=40, pady=(0,16))

        cols = ("id","name","email","created")
        self.tree = ttk.Treeview(tbl_frame, columns=cols,
                                  show="headings", style="U.Treeview")
        self.tree.heading("id",      text="#")
        self.tree.heading("name",    text="Name")
        self.tree.heading("email",   text="Email")
        self.tree.heading("created", text="Registered")
        self.tree.column("id",      width=40,  anchor=CENTER)
        self.tree.column("name",    width=160, anchor=W)
        self.tree.column("email",   width=240, anchor=W)
        self.tree.column("created", width=160, anchor=W)

        sb = Scrollbar(tbl_frame, command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side=LEFT, fill=BOTH, expand=True)
        sb.pack(side=RIGHT, fill=Y)

        # Delete button
        btn_row = Frame(self, bg=BG)
        btn_row.pack(fill=X, padx=40, pady=(0,20))
        Button(btn_row, text="Delete selected user",
               command=self._delete_selected,
               font=(FONT, 10), bg="#2b0e0e", fg="#EF5B5B",
               relief=FLAT, cursor="hand2", padx=14, pady=5
               ).pack(side=LEFT)

    def _register_new_user(self):
        name  = self.name_var.get().strip()
        email = self.email_var.get().strip()
        if not name:
            self.form_msg.config(text="Enter a name.", fg="#EF5B5B"); return
        if "@" not in email or "." not in email:
            self.form_msg.config(text="Enter a valid email.", fg="#EF5B5B"); return

        self.form_msg.config(text="Opening camera...", fg="#5a5f78")
        self.update()

        # Use FaceIDPopup in register mode to capture face
        popup = FaceIDPopup(self, known_encoding=None, mode="register")
        self.wait_window(popup)

        if popup.result != "registered" or popup.captured_frame is None:
            self.form_msg.config(text="Registration cancelled.", fg="#5a5f78")
            return

        # Extract encoding from captured frame
        frame = popup.captured_frame
        rgb   = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        encs  = face_recognition.face_encodings(rgb)
        if not encs:
            self.form_msg.config(
                text="Could not extract face. Try again.", fg="#EF5B5B")
            return

        ok, err = add_user(email, name, encs[0])
        if ok:
            self.form_msg.config(
                text=f"✓ {name} registered successfully.", fg="#34C47C")
            self.name_var.set("")
            self.email_var.set("")
            self._load_users()
        else:
            self.form_msg.config(text=f"Error: {err}", fg="#EF5B5B")

    def _load_users(self):
        for row in self.tree.get_children():
            self.tree.delete(row)
        users = get_all_users()
        for u in users:
            self.tree.insert("", END, values=u)
        self.count_lbl.config(text=f"{len(users)} user(s)")

    def _delete_selected(self):
        sel = self.tree.selection()
        if not sel:
            return
        item   = self.tree.item(sel[0])
        uid    = item["values"][0]
        name   = item["values"][1]
        if messagebox.askyesno("Delete user",
                               f"Remove {name} from the system?"):
            delete_user(uid)
            self._load_users()
            self.form_msg.config(
                text=f"{name} removed.", fg="#5a5f78")
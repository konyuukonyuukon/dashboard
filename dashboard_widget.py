import tkinter as tk
from tkinter import simpledialog, messagebox
import json, os, threading
from datetime import datetime, date, timedelta
import calendar
import pystray
from PIL import Image, ImageDraw

DATA_FILE    = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tasks.json")
ROUTINE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "routines.json")

BG     = "#0f0f13"
BG2    = "#1a1a22"
BG3    = "#0d0d10"
TEXT   = "#e8e8ec"
DIM    = "#888888"
DONE_C = "#444444"
GREEN  = "#1D9E75"
RED    = "#E24B4A"
AMBER  = "#EF9F27"
PURPLE = "#534AB7"
BLUE   = "#3A7BD5"
W, H   = 340, 540

DAYS_JP = ["月","火","水","木","金","土","日"]

def load_json(path, default):
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return default

def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def sample_tasks():
    t = date.today()
    return [
        {"id":1,"title":"週次レポートをまとめる","date":t.isoformat(),"done":False,"memo":"先週の数字を確認"},
        {"id":2,"title":"チームMTGの議題作成","date":(t+timedelta(1)).isoformat(),"done":False,"memo":""},
    ]

def sample_routines():
    return [
        {"id":101,"title":"メール確認","pattern":"daily","done_date":""},
        {"id":102,"title":"週次レポート提出","pattern":"weekly","weekday":4,"done_date":""},
        {"id":103,"title":"月次経費精算","pattern":"monthly","day":25,"done_date":""},
        {"id":104,"title":"月末締め作業","pattern":"month_end","done_date":""},
    ]

def today_str():
    return date.today().isoformat()

def days_until(ds):
    if not ds: return None
    try:
        d = datetime.strptime(ds, "%Y-%m-%d").date()
        return (d - date.today()).days
    except:
        return None

def urgency(task):
    if task.get("done"): return "done", "完了", DONE_C
    d = days_until(task.get("date", ""))
    if d is None: return "ok", "期日未設定", DIM
    if d < 0:     return "urgent", f"{abs(d)}日超過", RED
    if d == 0:    return "urgent", "今日が期限", RED
    if d <= 2:    return "soon", f"あと{d}日", AMBER
    return "ok", f"あと{d}日", GREEN

def fmt_date(ds):
    if not ds: return ""
    try:
        d = datetime.strptime(ds, "%Y-%m-%d")
        return f"{d.month}月{d.day}日"
    except:
        return ds

def routine_active_today(r):
    t = date.today()
    p = r["pattern"]
    if p == "daily":     return True
    if p == "weekly":    return t.weekday() == r.get("weekday", 0)
    if p == "monthly":   return t.day == r.get("day", 1)
    if p == "month_end": return t.day == calendar.monthrange(t.year, t.month)[1]
    return False

def routine_done_today(r):
    return r.get("done_date", "") == today_str()

def routine_label(r):
    p = r["pattern"]
    if p == "daily":     return "毎日"
    if p == "weekly":    return f"毎週{DAYS_JP[r.get('weekday',0)]}曜"
    if p == "monthly":   return f"毎月{r.get('day',1)}日"
    if p == "month_end": return "毎月末"
    return ""

def make_tray_icon():
    img = Image.new("RGB", (64, 64), color="#0f0f13")
    draw = ImageDraw.Draw(img)
    draw.rectangle([8, 16, 56, 22], fill="#534AB7")
    draw.rectangle([8, 28, 48, 34], fill="#534AB7")
    draw.rectangle([8, 40, 40, 46], fill="#534AB7")
    return img


# ── タスク追加・編集ダイアログ ────────────────────────────────────
class TaskDialog(tk.Toplevel):
    def __init__(self, parent, task=None):
        super().__init__(parent)
        self.result = None
        self.task   = task  # Noneなら新規、あれば編集
        self.title("タスク編集" if task else "タスク追加")
        self.configure(bg=BG)
        self.resizable(False, False)
        self.grab_set()
        self.lift()
        self.focus_force()
        self._days = 0  # 今日から何日後か
        self._build()
        self.wait_window()

    def _build(self):
        pad = {"padx":14, "pady":7}

        # タスク名
        tk.Label(self, text="タスク名", bg=BG, fg=TEXT, font=("Meiryo",10)).grid(row=0, column=0, sticky="w", **pad)
        self.e_title = tk.Entry(self, bg=BG2, fg=TEXT, insertbackground=TEXT,
                                relief="flat", font=("Meiryo",11), width=22)
        self.e_title.grid(row=0, column=1, **pad, ipady=5)
        self.e_title.focus_set()
        if self.task:
            self.e_title.insert(0, self.task.get("title", ""))

        # 期日カウンター
        tk.Label(self, text="期日", bg=BG, fg=TEXT, font=("Meiryo",10)).grid(row=1, column=0, sticky="w", **pad)

        # 今日から何日後かを計算して初期値を設定
        if self.task and self.task.get("date"):
            d = days_until(self.task["date"])
            self._days = max(0, d) if d is not None else 0
        else:
            self._days = 1

        counter_f = tk.Frame(self, bg=BG)
        counter_f.grid(row=1, column=1, sticky="w", **pad)

        btn_minus = tk.Label(counter_f, text="－", bg=BG2, fg=TEXT,
                             font=("Segoe UI",14), width=2, cursor="hand2", relief="flat")
        btn_minus.pack(side="left", ipady=2)
        btn_minus.bind("<Button-1>", lambda e: self._change_days(-1))

        self.lbl_days = tk.Label(counter_f, text=self._days_text(),
                                  bg=BG3, fg=TEXT, font=("Meiryo",11), width=10)
        self.lbl_days.pack(side="left", padx=6, ipady=4)

        btn_plus = tk.Label(counter_f, text="＋", bg=BG2, fg=TEXT,
                            font=("Segoe UI",14), width=2, cursor="hand2", relief="flat")
        btn_plus.pack(side="left", ipady=2)
        btn_plus.bind("<Button-1>", lambda e: self._change_days(1))

        # 期日表示
        self.lbl_date_preview = tk.Label(self, text=self._date_preview(),
                                          bg=BG, fg=DIM, font=("Meiryo",9))
        self.lbl_date_preview.grid(row=2, column=1, sticky="w", padx=14)

        # メモ
        tk.Label(self, text="メモ", bg=BG, fg=TEXT, font=("Meiryo",10)).grid(row=3, column=0, sticky="nw", **pad)
        self.e_memo = tk.Text(self, bg=BG2, fg=TEXT, insertbackground=TEXT,
                              relief="flat", font=("Meiryo",10), width=22, height=4)
        self.e_memo.grid(row=3, column=1, **pad)
        if self.task and self.task.get("memo"):
            self.e_memo.insert("1.0", self.task["memo"])

        # ボタン
        bf = tk.Frame(self, bg=BG)
        bf.grid(row=4, column=0, columnspan=2, pady=14)
        label = "保存" if self.task else "追加"
        tk.Button(bf, text=label, bg=PURPLE, fg=TEXT, relief="flat",
                  font=("Meiryo",10), padx=18, pady=5, cursor="hand2",
                  command=self._ok).pack(side="left", padx=8)
        tk.Button(bf, text="キャンセル", bg=BG2, fg=DIM, relief="flat",
                  font=("Meiryo",10), padx=18, pady=5, cursor="hand2",
                  command=self.destroy).pack(side="left", padx=8)

        self.bind("<Return>", lambda e: self._ok())

    def _change_days(self, delta):
        self._days = max(0, self._days + delta)
        self.lbl_days.config(text=self._days_text())
        self.lbl_date_preview.config(text=self._date_preview())

    def _days_text(self):
        if self._days == 0: return "今日"
        if self._days == 1: return "明日"
        return f"{self._days}日後"

    def _date_preview(self):
        d = date.today() + timedelta(days=self._days)
        return f"📅 {d.month}月{d.day}日（{DAYS_JP[d.weekday()]}）"

    def _ok(self):
        title = self.e_title.get().strip()
        if not title:
            messagebox.showwarning("入力エラー", "タスク名を入力してください", parent=self)
            return
        ds = (date.today() + timedelta(days=self._days)).isoformat()
        memo = self.e_memo.get("1.0", "end").strip()
        if self.task:
            self.result = {**self.task, "title": title, "date": ds, "memo": memo}
        else:
            self.result = {
                "id": int(datetime.now().timestamp()*1000),
                "title": title, "date": ds, "done": False, "memo": memo
            }
        self.destroy()


# ── ルーティン追加ダイアログ ──────────────────────────────────────
class RoutineDialog(tk.Toplevel):
    def __init__(self, parent):
        super().__init__(parent)
        self.result = None
        self.title("ルーティン追加")
        self.configure(bg=BG)
        self.resizable(False, False)
        self.grab_set()
        self.lift()
        self.focus_force()
        self._build()
        self.wait_window()

    def _build(self):
        pad = {"padx":12, "pady":6}
        tk.Label(self, text="タスク名", bg=BG, fg=TEXT, font=("Meiryo",10)).grid(row=0, column=0, sticky="w", **pad)
        self.e_title = tk.Entry(self, bg=BG2, fg=TEXT, insertbackground=TEXT, relief="flat", font=("Meiryo",10), width=24)
        self.e_title.grid(row=0, column=1, **pad, ipady=4)

        tk.Label(self, text="繰り返し", bg=BG, fg=TEXT, font=("Meiryo",10)).grid(row=1, column=0, sticky="w", **pad)
        self.pattern = tk.StringVar(value="daily")
        f = tk.Frame(self, bg=BG)
        f.grid(row=1, column=1, sticky="w", **pad)
        for txt, val in [("毎日","daily"),("毎週","weekly"),("毎月","monthly"),("毎月末","month_end")]:
            tk.Radiobutton(f, text=txt, variable=self.pattern, value=val,
                           bg=BG, fg=TEXT, selectcolor=PURPLE, activebackground=BG,
                           font=("Meiryo",9), command=self._on_pattern).pack(side="left", padx=4)

        self.f_week = tk.Frame(self, bg=BG)
        self.f_week.grid(row=2, column=0, columnspan=2, sticky="w", padx=12, pady=2)
        tk.Label(self.f_week, text="曜日:", bg=BG, fg=DIM, font=("Meiryo",9)).pack(side="left")
        self.weekday = tk.IntVar(value=0)
        for i, d in enumerate(DAYS_JP):
            tk.Radiobutton(self.f_week, text=d, variable=self.weekday, value=i,
                           bg=BG, fg=TEXT, selectcolor=PURPLE, activebackground=BG,
                           font=("Meiryo",9)).pack(side="left", padx=2)

        self.f_day = tk.Frame(self, bg=BG)
        self.f_day.grid(row=3, column=0, columnspan=2, sticky="w", padx=12, pady=2)
        tk.Label(self.f_day, text="日付:", bg=BG, fg=DIM, font=("Meiryo",9)).pack(side="left")
        self.day = tk.IntVar(value=1)
        tk.Spinbox(self.f_day, from_=1, to=28, textvariable=self.day, width=4,
                   bg=BG2, fg=TEXT, insertbackground=TEXT, buttonbackground=BG2,
                   relief="flat", font=("Meiryo",10)).pack(side="left", padx=4)
        tk.Label(self.f_day, text="日", bg=BG, fg=DIM, font=("Meiryo",9)).pack(side="left")

        self._on_pattern()

        bf = tk.Frame(self, bg=BG)
        bf.grid(row=4, column=0, columnspan=2, pady=12)
        tk.Button(bf, text="追加", bg=PURPLE, fg=TEXT, relief="flat",
                  font=("Meiryo",10), padx=16, pady=4, cursor="hand2",
                  command=self._ok).pack(side="left", padx=8)
        tk.Button(bf, text="キャンセル", bg=BG2, fg=DIM, relief="flat",
                  font=("Meiryo",10), padx=16, pady=4, cursor="hand2",
                  command=self.destroy).pack(side="left", padx=8)

    def _on_pattern(self):
        p = self.pattern.get()
        for w in self.f_week.winfo_children():
            w.configure(state="normal" if p == "weekly" else "disabled")
        for w in self.f_day.winfo_children():
            w.configure(state="normal" if p == "monthly" else "disabled")

    def _ok(self):
        title = self.e_title.get().strip()
        if not title:
            messagebox.showwarning("入力エラー", "タスク名を入力してください", parent=self)
            return
        p = self.pattern.get()
        r = {"id": int(datetime.now().timestamp()*1000), "title": title, "pattern": p, "done_date": ""}
        if p == "weekly":  r["weekday"] = self.weekday.get()
        if p == "monthly": r["day"]     = self.day.get()
        self.result = r
        self.destroy()


# ── メインウィジェット ────────────────────────────────────────────
class DashboardWidget:
    def __init__(self):
        self.tasks    = load_json(DATA_FILE, None) or sample_tasks()
        self.routines = load_json(ROUTINE_FILE, None) or sample_routines()
        self.tab      = "tasks"
        self.visible  = True

        self.root = tk.Tk()
        self._setup_window()
        self._build_ui()
        self._render()
        self._check_alerts()

        threading.Thread(target=self._start_tray, daemon=True).start()
        self.root.mainloop()

    def _setup_window(self):
        r = self.root
        r.title("Dashboard")
        r.configure(bg=BG)
        r.overrideredirect(True)
        r.attributes("-topmost", True)
        r.attributes("-alpha", 0.95)
        sw, sh = r.winfo_screenwidth(), r.winfo_screenheight()
        r.geometry(f"{W}x{H}+{sw-W-16}+{sh-H-48}")
        r.bind("<ButtonPress-1>", self._drag_start)
        r.bind("<B1-Motion>",     self._drag_move)

    def _drag_start(self, e): self._dx, self._dy = e.x, e.y
    def _drag_move(self, e):
        x = self.root.winfo_x() + e.x - self._dx
        y = self.root.winfo_y() + e.y - self._dy
        self.root.geometry(f"+{x}+{y}")

    def _start_tray(self):
        icon_img = make_tray_icon()
        menu = pystray.Menu(
            pystray.MenuItem("ダッシュボードを表示/非表示", self._toggle_visible, default=True),
            pystray.MenuItem("終了", self._quit),
        )
        self.tray = pystray.Icon("dashboard", icon_img, "タスクダッシュボード", menu)
        self.tray.run()

    def _toggle_visible(self):
        if self.visible: self._hide()
        else:            self._show()

    def _show(self):
        self.visible = True
        self.root.after(0, self.root.deiconify)

    def _hide(self):
        self.visible = False
        self.root.after(0, self.root.withdraw)

    def _quit(self):
        self.tray.stop()
        self.root.after(0, self.root.destroy)

    def _build_ui(self):
        r = self.root

        hdr = tk.Frame(r, bg=BG, pady=8, padx=12)
        hdr.pack(fill="x")
        self.lbl_date = tk.Label(hdr, text="", bg=BG, fg=TEXT, font=("Meiryo",10,"bold"))
        self.lbl_date.pack(side="left")
        close_btn = tk.Label(hdr, text="×", bg=BG, fg=DIM, font=("Segoe UI",14), cursor="hand2")
        close_btn.pack(side="right")
        close_btn.bind("<Button-1>", lambda e: self._hide())

        self.alert_frame = tk.Frame(r, bg=BG, padx=10)
        self.alert_frame.pack(fill="x")

        sf = tk.Frame(r, bg=BG, padx=10, pady=2)
        sf.pack(fill="x")
        self.lbl_total  = self._stat(sf, "合計",  TEXT)
        self.lbl_done   = self._stat(sf, "完了",  GREEN)
        self.lbl_urgent = self._stat(sf, "要注意", RED)

        tab_f = tk.Frame(r, bg=BG, padx=10, pady=4)
        tab_f.pack(fill="x")
        self.btn_tasks = tk.Label(tab_f, text="📋 タスク", bg=PURPLE, fg=TEXT,
                                  font=("Meiryo",9), padx=10, pady=4, cursor="hand2")
        self.btn_tasks.pack(side="left", padx=(0,4))
        self.btn_tasks.bind("<Button-1>", lambda e: self._switch_tab("tasks"))

        self.btn_routines = tk.Label(tab_f, text="🔁 ルーティン", bg=BG2, fg=DIM,
                                     font=("Meiryo",9), padx=10, pady=4, cursor="hand2")
        self.btn_routines.pack(side="left")
        self.btn_routines.bind("<Button-1>", lambda e: self._switch_tab("routines"))

        self.btn_add = tk.Label(tab_f, text="＋ 追加", bg=BG2, fg=TEXT,
                                font=("Meiryo",9), padx=10, pady=4, cursor="hand2")
        self.btn_add.pack(side="right")
        self.btn_add.bind("<Button-1>", lambda e: self._add())

        cf = tk.Frame(r, bg=BG)
        cf.pack(fill="both", expand=True, padx=10, pady=(4,10))
        self.canvas = tk.Canvas(cf, bg=BG, highlightthickness=0, bd=0)
        sb = tk.Scrollbar(cf, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=sb.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.list_frame = tk.Frame(self.canvas, bg=BG)
        self._cw = self.canvas.create_window((0,0), window=self.list_frame, anchor="nw")
        self.list_frame.bind("<Configure>", lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfig(self._cw, width=e.width))
        self.canvas.bind("<MouseWheel>", lambda e: self.canvas.yview_scroll(-1*(e.delta//120), "units"))

    def _stat(self, parent, label, color):
        f = tk.Frame(parent, bg=BG2, width=90, height=46)
        f.pack(side="left", expand=True, fill="x", padx=3, pady=2)
        f.pack_propagate(False)
        n = tk.Label(f, text="0", bg=BG2, fg=color, font=("Segoe UI",16,"bold"))
        n.pack()
        tk.Label(f, text=label, bg=BG2, fg=DIM, font=("Meiryo",8)).pack()
        return n

    def _switch_tab(self, tab):
        self.tab = tab
        if tab == "tasks":
            self.btn_tasks.configure(bg=PURPLE, fg=TEXT)
            self.btn_routines.configure(bg=BG2, fg=DIM)
        else:
            self.btn_routines.configure(bg=PURPLE, fg=TEXT)
            self.btn_tasks.configure(bg=BG2, fg=DIM)
        self._render()

    def _render(self):
        t = date.today()
        self.lbl_date.config(text=f"{DAYS_JP[t.weekday()]}曜日　{t.month}/{t.day}")

        todo  = [x for x in self.tasks if not x["done"]]
        done  = [x for x in self.tasks if x["done"]]
        urg_n = sum(1 for x in todo if urgency(x)[0] == "urgent")
        self.lbl_total.config( text=str(len(self.tasks)))
        self.lbl_done.config(  text=str(len(done)))
        self.lbl_urgent.config(text=str(urg_n))

        for w in self.alert_frame.winfo_children(): w.destroy()
        for x in todo:
            if urgency(x)[0] == "urgent":
                f = tk.Frame(self.alert_frame, bg="#2a1515", pady=3, padx=8)
                f.pack(fill="x", pady=2)
                tk.Label(f, text=f"⚠ {x['title']}", bg="#2a1515", fg="#f09595",
                         font=("Meiryo",9), wraplength=290, justify="left").pack(anchor="w")

        for w in self.list_frame.winfo_children(): w.destroy()

        if self.tab == "tasks":
            if todo:
                self._section("未完了　（タップで編集）")
                for x in todo: self._task_card(x)
            if done:
                self._section("完了済み")
                for x in done: self._task_card(x)
            if not self.tasks:
                tk.Label(self.list_frame, text="タスクがありません\n＋追加 から追加してください",
                         bg=BG, fg=DIM, font=("Meiryo",10)).pack(pady=30)
        else:
            active   = [r for r in self.routines if routine_active_today(r)]
            inactive = [r for r in self.routines if not routine_active_today(r)]
            if active:
                self._section("今日のルーティン")
                for r in active: self._routine_card(r)
            if inactive:
                self._section("その他のルーティン")
                for r in inactive: self._routine_card(r)
            if not self.routines:
                tk.Label(self.list_frame, text="ルーティンがありません\n＋追加 から追加してください",
                         bg=BG, fg=DIM, font=("Meiryo",10)).pack(pady=30)

        self.root.after(60000, self._render)

    def _section(self, text):
        tk.Label(self.list_frame, text=text, bg=BG, fg=DIM,
                 font=("Meiryo",8), anchor="w").pack(fill="x", pady=(8,2))

    def _task_card(self, task):
        lvl, lbl, color = urgency(task)
        bc = RED if lvl=="urgent" else (AMBER if lvl=="soon" else BG2)
        card = tk.Frame(self.list_frame, bg=BG2, pady=6, padx=8,
                        highlightbackground=bc, highlightthickness=1)
        card.pack(fill="x", pady=3)

        row1 = tk.Frame(card, bg=BG2)
        row1.pack(fill="x")

        # チェックボックス
        cb = tk.Label(row1, text="✓" if task["done"] else "○", bg=BG2,
                      fg=GREEN if task["done"] else DIM, font=("Segoe UI",12), cursor="hand2", width=2)
        cb.pack(side="left")
        cb.bind("<Button-1>", lambda e, tid=task["id"]: self._toggle_task(tid))

        # タイトル（クリックで編集）
        tf = ("Meiryo",10,"overstrike") if task["done"] else ("Meiryo",10)
        tl = tk.Label(row1, text=task["title"], bg=BG2, fg=DONE_C if task["done"] else TEXT,
                      font=tf, wraplength=190, justify="left", anchor="w", cursor="hand2")
        tl.pack(side="left", fill="x", expand=True)
        if not task["done"]:
            tl.bind("<Button-1>", lambda e, t=task: self._edit_task(t))

        # 削除
        dl = tk.Label(row1, text="✕", bg=BG2, fg=DIM, font=("Segoe UI",10), cursor="hand2")
        dl.pack(side="right", padx=4)
        dl.bind("<Button-1>", lambda e, tid=task["id"]: self._del_task(tid))

        row2 = tk.Frame(card, bg=BG2)
        row2.pack(fill="x", pady=(2,0))
        tk.Label(row2, text=lbl, bg=BG2, fg=color, font=("Meiryo",8)).pack(side="left")
        if task.get("date"):
            tk.Label(row2, text=f"  📅{fmt_date(task['date'])}", bg=BG2,
                     fg=DIM, font=("Meiryo",8)).pack(side="left")

        # メモ表示
        if task.get("memo"):
            memo_short = task["memo"][:28] + "…" if len(task["memo"]) > 28 else task["memo"]
            tk.Label(card, text=f"  {memo_short}", bg=BG2, fg="#777",
                     font=("Meiryo",8), anchor="w", wraplength=280, justify="left").pack(fill="x", pady=(2,0))

    def _routine_card(self, r):
        is_active = routine_active_today(r)
        is_done   = routine_done_today(r)
        bc = GREEN if is_done else (PURPLE if is_active else BG2)
        card = tk.Frame(self.list_frame, bg=BG2, pady=6, padx=8,
                        highlightbackground=bc, highlightthickness=1)
        card.pack(fill="x", pady=3)

        row1 = tk.Frame(card, bg=BG2)
        row1.pack(fill="x")
        cb_t  = "✓" if is_done else ("○" if is_active else "–")
        cb_fg = GREEN if is_done else (TEXT if is_active else DIM)
        cb = tk.Label(row1, text=cb_t, bg=BG2, fg=cb_fg, font=("Segoe UI",12),
                      cursor="hand2" if is_active else "arrow", width=2)
        cb.pack(side="left")
        if is_active:
            cb.bind("<Button-1>", lambda e, rid=r["id"]: self._toggle_routine(rid))

        tf = ("Meiryo",10,"overstrike") if is_done else ("Meiryo",10)
        tk.Label(row1, text=r["title"], bg=BG2, fg=DONE_C if is_done else TEXT,
                 font=tf, wraplength=210, justify="left", anchor="w").pack(side="left", fill="x", expand=True)

        dl = tk.Label(row1, text="✕", bg=BG2, fg=DIM, font=("Segoe UI",10), cursor="hand2")
        dl.pack(side="right", padx=4)
        dl.bind("<Button-1>", lambda e, rid=r["id"]: self._del_routine(rid))

        row2 = tk.Frame(card, bg=BG2)
        row2.pack(fill="x", pady=(2,0))
        lbl_color = GREEN if is_done else (BLUE if is_active else DIM)
        tk.Label(row2, text=f"🔁 {routine_label(r)}", bg=BG2, fg=lbl_color,
                 font=("Meiryo",8)).pack(side="left")
        if is_active and not is_done:
            tk.Label(row2, text="  今日対象", bg=BG2, fg=PURPLE,
                     font=("Meiryo",8)).pack(side="left")

    def _add(self):
        if self.tab == "tasks":
            dlg = TaskDialog(self.root)
            if dlg.result:
                self.tasks.insert(0, dlg.result)
                save_json(DATA_FILE, self.tasks)
                self._render()
        else:
            dlg = RoutineDialog(self.root)
            if dlg.result:
                self.routines.append(dlg.result)
                save_json(ROUTINE_FILE, self.routines)
                self._render()

    def _edit_task(self, task):
        dlg = TaskDialog(self.root, task=task)
        if dlg.result:
            for i, t in enumerate(self.tasks):
                if t["id"] == task["id"]:
                    self.tasks[i] = dlg.result
            save_json(DATA_FILE, self.tasks)
            self._render()

    def _toggle_task(self, tid):
        for t in self.tasks:
            if t["id"] == tid: t["done"] = not t["done"]
        save_json(DATA_FILE, self.tasks)
        self._render()

    def _del_task(self, tid):
        self.tasks = [t for t in self.tasks if t["id"] != tid]
        save_json(DATA_FILE, self.tasks)
        self._render()

    def _toggle_routine(self, rid):
        for r in self.routines:
            if r["id"] == rid:
                r["done_date"] = "" if routine_done_today(r) else today_str()
        save_json(ROUTINE_FILE, self.routines)
        self._render()

    def _del_routine(self, rid):
        self.routines = [r for r in self.routines if r["id"] != rid]
        save_json(ROUTINE_FILE, self.routines)
        self._render()

    def _check_alerts(self):
        urgent = [t for t in self.tasks if not t["done"] and urgency(t)[0] == "urgent"]
        if urgent:
            msg = "期日が迫っているタスクがあります:\n\n"
            for t in urgent[:5]:
                msg += f"・{t['title']}　({fmt_date(t.get('date',''))})\n"
            messagebox.showwarning("⚠ タスクアラート", msg, master=self.root)


if __name__ == "__main__":
    DashboardWidget()
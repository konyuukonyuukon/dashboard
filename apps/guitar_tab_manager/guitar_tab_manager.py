#!/usr/bin/env python3
"""
Guitar TAB Manager
写真・スクショからTAB譜をスキャン・管理・印刷するデスクトップアプリ
"""

import tkinter as tk
from tkinter import ttk, filedialog, messagebox, simpledialog
import json, os, base64, uuid, re as _re
from datetime import datetime
from pathlib import Path

try:
    import anthropic
    HAS_ANTHROPIC = True
except ImportError:
    HAS_ANTHROPIC = False

try:
    from reportlab.pdfgen import canvas as pdfcanvas
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    HAS_REPORTLAB = True
except ImportError:
    HAS_REPORTLAB = False

# ─── 音楽理論ユーティリティ ─────────────────────────────────
CHROMATIC  = ['C','C#','D','D#','E','F','F#','G','G#','A','A#','B']
NOTE_IDX   = {n: i for i, n in enumerate(CHROMATIC)}

# チューニング別の開放弦ノート（low E→high e の順）
TUNING_OPEN = {
    "Standard (EADGBe)":                 [4,9,2,7,11,4],
    "Drop D (DADGBe)":                   [2,9,2,7,11,4],
    "Open G (DGDGBd)":                   [2,7,2,7,11,2],
    "Open D (DADf#Ad)":                  [2,9,2,6, 9,2],
    "DADGAD":                            [2,9,2,7, 9,2],
    "Half Step Down (Eb Ab Db Gb Bb eb)":[3,8,1,6,10,3],
    "Full Step Down (DGCFAd)":           [2,7,0,5, 9,2],
    "Open E (EBE G#Be)":                 [4,11,4,8,11,4],
    "Open A (EAE AC#E)":                 [4,9,4,9, 1,4],
}

# Krumhansl-Schmuckler キープロファイル
_KS_MAJ = [6.35,2.23,3.48,2.33,4.38,4.09,2.52,5.19,2.39,3.66,2.29,2.88]
_KS_MIN = [6.33,2.68,3.52,5.38,2.60,3.53,2.54,4.75,3.98,2.69,3.34,3.17]

# スケール: {名前: (インターバルリスト, 説明)}
SCALES = {
    "ペンタトニック・マイナー": ([0,3,5,7,10],    "ロック・ブルース・J-POPで最頻出。まずはこれ"),
    "ブルース":                  ([0,3,5,6,7,10],  "ペンタ+b5。泥臭さ・哀愁の定番"),
    "ナチュラルマイナー":        ([0,2,3,5,7,8,10],"哀愁・ヘヴィサウンドに"),
    "ドリアン":                  ([0,2,3,5,7,9,10],"マイナーだが6度が明るい。フュージョン向き"),
    "ミクソリディアン":          ([0,2,4,5,7,9,10],"メジャーだがb7。ロック・ファンク向き"),
    "ペンタトニック・メジャー":  ([0,2,4,7,9],     "カントリー・ポップ。明るい"),
    "メジャー（イオニアン）":    ([0,2,4,5,7,9,11],"明るく開放的"),
    "ハーモニックマイナー":      ([0,2,3,5,7,8,11],"クラシカル・メタルの緊張感"),
    "フリジアン":                ([0,1,3,5,7,8,10],"b2が特徴。フラメンコ・メタル"),
    "リディアン":                ([0,2,4,6,7,9,11],"#4が浮遊感。映画音楽・プログ"),
    "ホールトーン":              ([0,2,4,6,8,10],  "全音のみ。ジャズ・不思議な雰囲気"),
    "ディミニッシュ":            ([0,1,3,4,6,7,9,10],"8音。ジャズテンション"),
}

def _pearson(x, y):
    n = len(x); mx = sum(x)/n; my = sum(y)/n
    num = sum((x[i]-mx)*(y[i]-my) for i in range(n))
    dx = sum((v-mx)**2 for v in x)**0.5
    dy = sum((v-my)**2 for v in y)**0.5
    return num/(dx*dy) if dx*dy else 0.0

def parse_tab_notes(tab_text: str, tuning_str: str) -> list:
    """TABテキストから音名インデックス(0-11)のリストを抽出"""
    open_notes = TUNING_OPEN.get(tuning_str, TUNING_OPEN["Standard (EADGBe)"])
    str_map = {'E':0,'A':1,'D':2,'G':3,'B':4,'e':5}
    notes = []
    for line in tab_text.split('\n'):
        s = line.strip()
        if len(s) < 2 or s[1] != '|':
            continue
        ch = s[0]
        if ch not in str_map:
            continue
        on = open_notes[str_map[ch]]
        for f in _re.findall(r'\d+', s[2:]):
            fret = int(f)
            if fret <= 24:
                notes.append((on + fret) % 12)
    return notes

def detect_key(notes: list) -> list:
    """Krumhansl-Schmucklerでキー推定。[(key, mode, score)...] 降順"""
    if not notes:
        return []
    freq = [0]*12
    for n in notes:
        freq[n] += 1
    results = []
    for root in range(12):
        for mode, profile in [("メジャー",_KS_MAJ),("マイナー",_KS_MIN)]:
            rotated = [profile[(i-root)%12] for i in range(12)]
            results.append((CHROMATIC[root], mode, _pearson(freq, rotated)))
    results.sort(key=lambda x: -x[2])
    return results

def scale_notes(root: str, intervals: list) -> list:
    r = NOTE_IDX[root]
    return [CHROMATIC[(r+i)%12] for i in intervals]

def fretboard_text(root: str, intervals: list, tuning_str: str, max_fret=15):
    """フレットボードの行リスト [(text, tag), ...] を返す"""
    open_notes = TUNING_OPEN.get(tuning_str, TUNING_OPEN["Standard (EADGBe)"])
    ri = NOTE_IDX[root] % 12
    scale_set = set((ri+i)%12 for i in intervals)
    rows = []
    header = "      " + "".join(f"{f:4}" for f in range(max_fret+1))
    rows.append((header+"\n", "fret_num"))
    rows.append(("      " + "─"*((max_fret+1)*4) + "\n", "sep"))
    for disp_idx, (s_name, arr_idx) in enumerate(
        zip(['e','B','G','D','A','E'],[5,4,3,2,1,0])
    ):
        on = open_notes[arr_idx]
        rows.append((f"  {s_name}  │", "str_name"))
        for fret in range(max_fret+1):
            note = (on+fret)%12
            nn = CHROMATIC[note]
            cell = nn.ljust(2)
            if note == ri:
                rows.append((f"[{cell}]", "root"))
            elif note in scale_set:
                rows.append((f" {cell} ", "scale_note"))
            else:
                rows.append((" ·· ", "empty"))
        rows.append(("│\n", "str_name"))
    return rows

# ─── テーマ ─────────────────────────────────────────────────
BG     = "#0f0f13"
BG2    = "#1a1a22"
BG3    = "#131318"
BORDER = "#2a2a35"
TEXT   = "#e8e8ec"
DIM    = "#666680"
GREEN  = "#1D9E75"
RED    = "#E24B4A"
AMBER  = "#EF9F27"
BLUE   = "#3A7BD5"
ACCENT = "#534AB7"
TAB_BG = "#0d1117"

STRING_COLORS = {
    "e": "#FF6B6B", "B": "#FFB347", "G": "#FFEAA7",
    "D": "#A8E6CF", "A": "#74B9FF", "E": "#A29BFE",
}

TUNINGS = [
    "Standard (EADGBe)",
    "Drop D (DADGBe)",
    "Open G (DGDGBd)",
    "Open D (DADf#Ad)",
    "DADGAD",
    "Half Step Down (Eb Ab Db Gb Bb eb)",
    "Full Step Down (DGCFAd)",
    "Open E (EBE G#Be)",
    "Open A (EAE AC#E)",
    "カスタム",
]

EMPTY_TAB = (
    "e|----------------------------|\n"
    "B|----------------------------|\n"
    "G|----------------------------|\n"
    "D|----------------------------|\n"
    "A|----------------------------|\n"
    "E|----------------------------|"
)

# ─── データ層 ────────────────────────────────────────────────
DATA_DIR   = Path(__file__).parent / "data"
SONGS_FILE = DATA_DIR / "songs.json"


def load_songs():
    DATA_DIR.mkdir(exist_ok=True)
    if SONGS_FILE.exists():
        try:
            return json.loads(SONGS_FILE.read_text(encoding="utf-8"))
        except Exception:
            return []
    return []


def save_songs(songs):
    DATA_DIR.mkdir(exist_ok=True)
    SONGS_FILE.write_text(
        json.dumps(songs, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def new_song(title="新しい曲", artist=""):
    return {
        "id": str(uuid.uuid4()),
        "title": title,
        "artist": artist,
        "tuning": "Standard (EADGBe)",
        "bpm": "",
        "capo": "0",
        "sections": [{"name": "イントロ", "tab": EMPTY_TAB}],
        "notes": "",
        "created": datetime.now().strftime("%Y-%m-%d"),
        "modified": datetime.now().strftime("%Y-%m-%d"),
    }


def new_section(name="セクション"):
    return {"name": name, "tab": EMPTY_TAB}


# ─── キー分析ウィンドウ ──────────────────────────────────────
class KeyAnalysisWindow(tk.Toplevel):
    def __init__(self, parent, tab_text, tuning, song_title):
        super().__init__(parent)
        self.title(f"🎵 キー分析 — {song_title}")
        self.geometry("1000x680")
        self.configure(bg=BG)
        self.resizable(True, True)

        self.tab_text   = tab_text
        self.tuning     = tuning
        self.song_title = song_title

        self.notes       = parse_tab_notes(tab_text, tuning)
        self.key_results = detect_key(self.notes)

        top = self.key_results[0] if self.key_results else ("A","マイナー",0)
        self.sel_key   = tk.StringVar(value=top[0])
        self.sel_mode  = tk.StringVar(value=top[1])
        self.sel_scale = tk.StringVar(value="ペンタトニック・マイナー")

        self._build()
        self._update_scales()
        self._update_fretboard()
        self.grab_set()

    # ── レイアウト ──────────────────────────────────────────
    def _build(self):
        paned = tk.PanedWindow(self, orient="horizontal", bg=BORDER, sashwidth=3)
        paned.pack(fill="both", expand=True, padx=4, pady=4)
        left  = tk.Frame(paned, bg=BG2)
        right = tk.Frame(paned, bg=BG)
        paned.add(left,  minsize=290)
        paned.add(right, minsize=500)
        self._build_left(left)
        self._build_right(right)
        self.after(80, lambda: paned.sash_place(0, 300, 0))

    def _lbl(self, p, text, **kw):
        return tk.Label(p, text=text, bg=p["bg"], fg=DIM,
                        font=("Yu Gothic UI",9), **kw)

    def _build_left(self, p):
        self._lbl(p,"検出ノート").pack(anchor="w", padx=10, pady=(10,2))
        note_set = sorted(set(self.notes))
        names = "  ".join(CHROMATIC[n] for n in note_set)
        tk.Label(p, text=f"{names}\n({len(self.notes)} 音使用)",
                 bg=BG2, fg=AMBER, font=("Consolas",11,"bold"),
                 justify="left").pack(anchor="w", padx=10, pady=(0,8))

        self._lbl(p,"推定キー TOP5").pack(anchor="w", padx=10, pady=(4,2))
        for i,(key,mode,score) in enumerate(self.key_results[:5]):
            color = GREEN if i==0 else (TEXT if i<3 else DIM)
            star  = " ★" if i==0 else ""
            row = tk.Frame(p, bg=BG2, cursor="hand2")
            row.pack(fill="x", padx=8, pady=1)
            def click(k=key,m=mode):
                self.sel_key.set(k); self.sel_mode.set(m)
                self._update_scales(); self._update_fretboard()
            for w in (row,):
                tk.Label(row, text=f"{i+1}. {key} {mode}{star}",
                         bg=BG2, fg=color,
                         font=("Yu Gothic UI",10,"bold" if i==0 else "normal"),
                         width=20, anchor="w").pack(side="left")
                tk.Label(row, text=f"{score:.3f}", bg=BG2, fg=DIM,
                         font=("Consolas",9)).pack(side="left")
                row.bind("<Button-1>", lambda e,f=click: f())

        tk.Frame(p, bg=BORDER, height=1).pack(fill="x", padx=8, pady=10)

        self._lbl(p,"キー選択").pack(anchor="w", padx=10)
        kf = tk.Frame(p, bg=BG2)
        kf.pack(fill="x", padx=10, pady=4)
        ttk.Combobox(kf, textvariable=self.sel_key,
                     values=CHROMATIC, width=5,
                     font=("Consolas",12)).pack(side="left", padx=(0,6))
        ttk.Combobox(kf, textvariable=self.sel_mode,
                     values=["メジャー","マイナー"], width=10,
                     font=("Yu Gothic UI",10)).pack(side="left")
        self.sel_key.trace("w",  lambda *_: (self._update_scales(), self._update_fretboard()))
        self.sel_mode.trace("w", lambda *_: (self._update_scales(), self._update_fretboard()))

        tk.Frame(p, bg=BORDER, height=1).pack(fill="x", padx=8, pady=8)
        tk.Button(p, text="🤖  Claude AI で詳細分析",
                  bg=ACCENT, fg=TEXT, relief="flat", bd=0,
                  font=("Yu Gothic UI",10,"bold"), pady=8, cursor="hand2",
                  command=self._ai_analyze).pack(fill="x", padx=10, pady=(0,4))
        tk.Button(p, text="📋  結果をコピー",
                  bg=BG3, fg=TEXT, relief="flat", bd=0,
                  font=("Yu Gothic UI",9), pady=6, cursor="hand2",
                  command=self._copy_result).pack(fill="x", padx=10)

    def _build_right(self, p):
        hdr = tk.Frame(p, bg=BG3, pady=6)
        hdr.pack(fill="x")
        tk.Label(hdr, text="アドリブ用スケール", bg=BG3, fg=ACCENT,
                 font=("Yu Gothic UI",11,"bold")).pack(side="left", padx=10)
        self.key_label = tk.Label(hdr, text="", bg=BG3, fg=AMBER,
                                   font=("Consolas",12,"bold"))
        self.key_label.pack(side="left")

        self.scale_frame = tk.Frame(p, bg=BG)
        self.scale_frame.pack(fill="x", padx=4, pady=2)

        tk.Frame(p, bg=BORDER, height=1).pack(fill="x", padx=4, pady=4)

        fb_hdr = tk.Frame(p, bg=BG2, pady=4)
        fb_hdr.pack(fill="x")
        tk.Label(fb_hdr, text="フレットボード表示:", bg=BG2, fg=DIM,
                 font=("Yu Gothic UI",9)).pack(side="left", padx=10)
        ttk.Combobox(fb_hdr, textvariable=self.sel_scale,
                     values=list(SCALES.keys()), width=24,
                     font=("Yu Gothic UI",9)).pack(side="left", padx=4)
        self.sel_scale.trace("w", lambda *_: self._update_fretboard())

        fb_frame = tk.Frame(p, bg=BG)
        fb_frame.pack(fill="both", expand=True, padx=4, pady=2)

        self.fb_text = tk.Text(
            fb_frame, bg=TAB_BG, fg="#c0caf5",
            font=("Consolas",12), relief="flat", bd=0,
            padx=10, pady=8, state="disabled", wrap="none", height=11,
        )
        self.fb_text.tag_config("root",       foreground="#FF6B6B", font=("Consolas",12,"bold"))
        self.fb_text.tag_config("scale_note", foreground=GREEN)
        self.fb_text.tag_config("empty",      foreground="#25253a")
        self.fb_text.tag_config("fret_num",   foreground=DIM)
        self.fb_text.tag_config("sep",        foreground="#25253a")
        self.fb_text.tag_config("str_name",   foreground=DIM)
        self.fb_text.tag_config("hint",       foreground=AMBER,
                                font=("Yu Gothic UI",9,"italic"))

        hsc = tk.Scrollbar(fb_frame, orient="horizontal",
                           command=self.fb_text.xview,
                           bg=BG, troughcolor=BG, relief="flat", bd=0)
        self.fb_text.configure(xscrollcommand=hsc.set)
        hsc.pack(side="bottom", fill="x")
        self.fb_text.pack(fill="both", expand=True)

    # ── データ更新 ──────────────────────────────────────────
    def _update_scales(self):
        for w in self.scale_frame.winfo_children():
            w.destroy()
        key  = self.sel_key.get()
        mode = self.sel_mode.get()
        self.key_label.config(text=f"  {key} {mode}")

        if mode == "マイナー":
            priority = ["ペンタトニック・マイナー","ブルース","ナチュラルマイナー",
                        "ドリアン","ハーモニックマイナー","フリジアン","ディミニッシュ"]
        else:
            priority = ["ペンタトニック・メジャー","メジャー（イオニアン）",
                        "ミクソリディアン","リディアン","ホールトーン","ドリアン"]

        order = priority + [k for k in SCALES if k not in priority]
        for i, sname in enumerate(order):
            intervals, hint = SCALES[sname]
            notes = scale_notes(key, intervals)
            bg_row = "#1a1a2e" if i < len(priority) else BG
            color  = GREEN if i < 3 else (TEXT if i < len(priority) else DIM)
            star   = "★ " if i < 3 else "  "

            row = tk.Frame(self.scale_frame, bg=bg_row, pady=2, padx=8)
            row.pack(fill="x", pady=(1,0))

            tk.Label(row, text=f"{star}{sname}", bg=bg_row, fg=color,
                     font=("Yu Gothic UI",9,"bold" if i<3 else "normal"),
                     width=24, anchor="w").pack(side="left")
            tk.Label(row, text="  ".join(notes), bg=bg_row, fg=AMBER,
                     font=("Consolas",10)).pack(side="left", padx=(6,0))
            tk.Label(row, text=f"  {hint}", bg=bg_row, fg=DIM,
                     font=("Yu Gothic UI",8)).pack(side="left", padx=(4,0))

            def on_click(sn=sname):
                self.sel_scale.set(sn)
                self._update_fretboard()
            row.bind("<Button-1>", lambda e,f=on_click: f())
            for child in row.winfo_children():
                child.bind("<Button-1>", lambda e,f=on_click: f())

    def _update_fretboard(self):
        scale = self.sel_scale.get()
        if scale not in SCALES:
            return
        intervals, hint = SCALES[scale]
        key = self.sel_key.get()

        self.fb_text.config(state="normal")
        self.fb_text.delete("1.0","end")
        for text, tag in fretboard_text(key, intervals, self.tuning):
            self.fb_text.insert("end", text, tag)
        self.fb_text.insert("end", f"\n  💡 {hint}\n", "hint")
        self.fb_text.config(state="disabled")

    # ── AI分析 ──────────────────────────────────────────────
    def _ai_analyze(self):
        if not HAS_ANTHROPIC:
            messagebox.showerror("エラー","pip install anthropic が必要です。",parent=self)
            return
        api_key = os.environ.get("ANTHROPIC_API_KEY","")
        if not api_key:
            api_key = simpledialog.askstring("API Key","ANTHROPIC_API_KEY:",
                                              parent=self, show="*")
            if not api_key: return
            os.environ["ANTHROPIC_API_KEY"] = api_key

        wait = tk.Toplevel(self)
        wait.title("分析中")
        wait.geometry("360x80")
        wait.configure(bg=BG)
        tk.Label(wait, text="🤖  Claude が分析中です...",
                 bg=BG, fg=TEXT, font=("Yu Gothic UI",12)).pack(expand=True)
        self.update()

        try:
            key  = self.sel_key.get()
            mode = self.sel_mode.get()
            used = " ".join(sorted(set(CHROMATIC[n] for n in self.notes)))
            top5 = "\n".join(f"  {i+1}. {k} {m}  ({s:.3f})"
                             for i,(k,m,s) in enumerate(self.key_results[:5]))

            client = anthropic.Anthropic()
            msg = client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=1800,
                messages=[{"role":"user","content":
                f"""ギタリスト向けのアドリブ・スケールアドバイスをください。

曲名: {self.song_title}
使用ノート: {used}
推定キー: {key} {mode}
キー候補TOP5:
{top5}

TAB譜（冒頭）:
{self.tab_text[:600]}

以下を日本語で実践的に答えてください:
1. このキーの雰囲気・特徴（2〜3行）
2. おすすめスケールTOP3と、各スケールの使いどころ・注意点
3. このキーで有名なギタリスト・曲の例（2〜3例）
4. 具体的なアドリブの入り方（どのポジション・音から始めるか）
5. ちょっと上級な使えるテクニック（1つ）

ギタリスト目線で簡潔かつ実践的に。"""}]
            )
            wait.destroy()

            rw = tk.Toplevel(self)
            rw.title("🤖 AI分析結果")
            rw.geometry("700x540")
            rw.configure(bg=BG)
            tk.Label(rw, text=f"  🎸 {self.song_title}  /  {key} {mode}",
                     bg=BG2, fg=ACCENT, font=("Yu Gothic UI",11,"bold"),
                     pady=8, anchor="w").pack(fill="x")
            rt = tk.Text(rw, bg=BG2, fg=TEXT,
                         font=("Yu Gothic UI",10), relief="flat",
                         bd=0, padx=16, pady=12, wrap="word")
            sc = tk.Scrollbar(rw, command=rt.yview, bg=BG, troughcolor=BG,
                              relief="flat", bd=0)
            rt.configure(yscrollcommand=sc.set)
            sc.pack(side="right", fill="y")
            rt.pack(fill="both", expand=True)
            rt.insert("end", msg.content[0].text)
            rt.config(state="disabled")

        except Exception as e:
            wait.destroy()
            messagebox.showerror("エラー",f"AI分析失敗:\n{e}",parent=self)

    def _copy_result(self):
        key = self.sel_key.get(); mode = self.sel_mode.get()
        lines = [f"# キー分析: {self.song_title}",
                 f"推定キー: {key} {mode}", ""]
        for sname,(intervals,_) in SCALES.items():
            lines.append(f"{sname}: {'  '.join(scale_notes(key,intervals))}")
        self.clipboard_clear()
        self.clipboard_append("\n".join(lines))
        messagebox.showinfo("コピー完了","クリップボードにコピーしました。",parent=self)


# ─── アプリ本体 ──────────────────────────────────────────────
class GuitarTabApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("🎸 Guitar TAB Manager")
        self.geometry("1280x780")
        self.minsize(900, 600)
        self.configure(bg=BG)

        self.songs: list = load_songs()
        self.current_song_idx: int | None = None
        self.current_section_idx: int = 0
        self._dirty: bool = False
        self._filtered_indices: list = []
        self._section_buttons: list = []

        self._apply_theme()
        self._build_ui()
        self._refresh_song_list()

        if self.songs:
            self._select_song(0)

        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.bind("<Control-s>", lambda e: self._save_current())
        self.bind("<Control-n>", lambda e: self._new_song())
        self.bind("<Control-p>", lambda e: self._print_pdf())
        self.bind("<Control-z>", lambda e: self.tab_editor.edit_undo())

    # ─── テーマ ─────────────────────────────────────────────
    def _apply_theme(self):
        s = ttk.Style(self)
        s.theme_use("clam")
        s.configure(".", background=BG, foreground=TEXT,
                    bordercolor=BORDER, troughcolor=BG2,
                    fieldbackground=BG2, insertcolor=TEXT)
        s.configure("TFrame", background=BG)
        s.configure("TLabel", background=BG, foreground=TEXT)
        s.configure("TEntry", fieldbackground=BG2, foreground=TEXT,
                    insertcolor=TEXT, bordercolor=BORDER)
        s.configure("TCombobox", fieldbackground=BG2, foreground=TEXT,
                    selectbackground=ACCENT, selectforeground=TEXT)
        s.map("TCombobox", fieldbackground=[("readonly", BG2)])
        s.configure("TSpinbox", fieldbackground=BG2, foreground=TEXT,
                    insertcolor=TEXT, bordercolor=BORDER)
        s.configure("TScrollbar", background=BG2, troughcolor=BG,
                    bordercolor=BORDER, arrowcolor=DIM)

    # ─── UI 構築 ─────────────────────────────────────────────
    def _build_ui(self):
        self._build_toolbar()

        paned = tk.PanedWindow(self, orient="horizontal", bg=BORDER,
                               sashwidth=3, sashrelief="flat")
        paned.pack(fill="both", expand=True)

        left = tk.Frame(paned, bg=BG3, width=230)
        paned.add(left, minsize=170)
        self._build_sidebar(left)

        right = tk.Frame(paned, bg=BG)
        paned.add(right, minsize=640)
        self._build_editor(right)

        self.after(100, lambda: paned.sash_place(0, 230, 0))

    # ── ツールバー ──────────────────────────────────────────
    def _build_toolbar(self):
        bar = tk.Frame(self, bg=BG2, height=52)
        bar.pack(fill="x")
        bar.pack_propagate(False)

        def btn(parent, text, cmd, bg=BG3, **kw):
            b = tk.Button(parent, text=text, command=cmd, bg=bg, fg=TEXT,
                          relief="flat", bd=0, padx=12, pady=7,
                          cursor="hand2", font=("Yu Gothic UI", 9, "bold"), **kw)
            b.pack(side="left", padx=(2, 0), pady=8)
            b.bind("<Enter>", lambda e: b.config(bg=_lighten(bg)))
            b.bind("<Leave>", lambda e: b.config(bg=bg))
            return b

        def _lighten(color):
            r = min(255, int(color[1:3], 16) + 20)
            g = min(255, int(color[3:5], 16) + 20)
            b = min(255, int(color[5:7], 16) + 20)
            return f"#{r:02x}{g:02x}{b:02x}"

        btn(bar, "＋ 新しい曲",     self._new_song,    bg=ACCENT)
        btn(bar, "🖼  画像から読込", self._import_image)
        btn(bar, "💾 保存 Ctrl+S",  self._save_current)
        btn(bar, "🖨  印刷 / PDF",  self._print_pdf)
        btn(bar, "✏  セクション名変更", self._rename_section)
        btn(bar, "🎵  キー分析",      self._analyze_key,  bg="#1a2a1a")
        btn(bar, "🗑  削除",         self._delete_song,  bg="#3a1212")

        # 検索
        tk.Label(bar, text="🔍", bg=BG2, fg=DIM, font=("Segoe UI Emoji", 12)).pack(
            side="right", padx=(4, 0), pady=8)
        self.search_var = tk.StringVar()
        self.search_var.trace("w", lambda *_: self._refresh_song_list())
        tk.Entry(bar, textvariable=self.search_var, bg=BG3, fg=TEXT,
                 insertbackground=TEXT, relief="flat", bd=5,
                 font=("Yu Gothic UI", 10), width=16).pack(
            side="right", padx=(4, 10), pady=8)
        tk.Label(bar, text="検索:", bg=BG2, fg=DIM,
                 font=("Yu Gothic UI", 9)).pack(side="right", pady=8)

    # ── サイドバー（曲一覧）───────────────────────────────
    def _build_sidebar(self, parent):
        tk.Label(parent, text="🎸  曲ライブラリ", bg=BG3, fg=ACCENT,
                 font=("Yu Gothic UI", 11, "bold")).pack(
            pady=(14, 4), padx=12, anchor="w")

        frame = tk.Frame(parent, bg=BG3)
        frame.pack(fill="both", expand=True, padx=6, pady=(0, 6))

        self.song_listbox = tk.Listbox(
            frame, bg=BG3, fg=TEXT,
            selectbackground=ACCENT, selectforeground=TEXT,
            relief="flat", bd=0, activestyle="none",
            font=("Yu Gothic UI", 10), highlightthickness=0,
            exportselection=False,
        )
        sb = tk.Scrollbar(frame, orient="vertical", command=self.song_listbox.yview,
                          bg=BG3, troughcolor=BG3, relief="flat", bd=0)
        self.song_listbox.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.song_listbox.pack(fill="both", expand=True)
        self.song_listbox.bind("<<ListboxSelect>>", self._on_song_select)
        self.song_listbox.bind("<Double-Button-1>", lambda e: self._rename_song())

        self.count_label = tk.Label(parent, text="0 曲", bg=BG3, fg=DIM,
                                    font=("Yu Gothic UI", 8))
        self.count_label.pack(anchor="w", padx=12, pady=(0, 6))

    # ── エディタ ────────────────────────────────────────────
    def _build_editor(self, parent):
        # 曲情報フォーム
        info = tk.Frame(parent, bg=BG2, padx=14, pady=10)
        info.pack(fill="x")

        row1 = tk.Frame(info, bg=BG2)
        row1.pack(fill="x", pady=(0, 6))

        self._label(row1, "曲名").grid(row=0, column=0, sticky="w", padx=(0, 4))
        self.title_var = self._traced_var()
        tk.Entry(row1, textvariable=self.title_var, bg=BG3, fg=TEXT,
                 insertbackground=TEXT, relief="flat", bd=5,
                 font=("Yu Gothic UI", 14, "bold"), width=26).grid(
            row=0, column=1, sticky="ew", padx=(0, 18))

        self._label(row1, "アーティスト").grid(row=0, column=2, sticky="w", padx=(0, 4))
        self.artist_var = self._traced_var()
        tk.Entry(row1, textvariable=self.artist_var, bg=BG3, fg=TEXT,
                 insertbackground=TEXT, relief="flat", bd=5,
                 font=("Yu Gothic UI", 11), width=22).grid(
            row=0, column=3, sticky="ew")
        row1.columnconfigure(1, weight=2)
        row1.columnconfigure(3, weight=1)

        row2 = tk.Frame(info, bg=BG2)
        row2.pack(fill="x")

        self._label(row2, "チューニング").pack(side="left", padx=(0, 4))
        self.tuning_var = self._traced_var(TUNINGS[0])
        ttk.Combobox(row2, textvariable=self.tuning_var, values=TUNINGS,
                     width=28, state="normal",
                     font=("Yu Gothic UI", 9)).pack(side="left", padx=(0, 16))

        self._label(row2, "BPM").pack(side="left", padx=(0, 4))
        self.bpm_var = self._traced_var()
        tk.Entry(row2, textvariable=self.bpm_var, bg=BG3, fg=TEXT,
                 insertbackground=TEXT, relief="flat", bd=5,
                 font=("Yu Gothic UI", 10), width=6).pack(side="left", padx=(0, 16))

        self._label(row2, "カポ").pack(side="left", padx=(0, 4))
        self.capo_var = self._traced_var("0")
        ttk.Spinbox(row2, textvariable=self.capo_var, from_=0, to=12,
                    width=4, font=("Yu Gothic UI", 10)).pack(side="left")

        # セクションタブバー
        sec_bar = tk.Frame(parent, bg="#0a0a10")
        sec_bar.pack(fill="x", pady=(1, 0))
        self._sec_inner = tk.Frame(sec_bar, bg="#0a0a10")
        self._sec_inner.pack(side="left", fill="x", expand=True)
        tk.Button(sec_bar, text="＋ セクション追加",
                  bg="#0a0a10", fg=DIM, relief="flat", bd=0,
                  font=("Yu Gothic UI", 9), cursor="hand2",
                  command=self._add_section, pady=6, padx=10).pack(side="right")

        # テンプレート挿入バー
        tpl = tk.Frame(parent, bg=BG3, pady=3)
        tpl.pack(fill="x")
        tk.Label(tpl, text="挿入:", bg=BG3, fg=DIM,
                 font=("Yu Gothic UI", 8)).pack(side="left", padx=(8, 4))
        templates = [
            ("6弦枠", EMPTY_TAB),
            ("コード", "[ Chord: ___ ]\n"),
            ("区切り", "\n" + "─" * 42 + "\n"),
            ("ハーフバレー", "e|--X--|\nB|--X--|\nG|--X--|\nD|--X--|\nA|--X--|\nE|--X--|"),
        ]
        for label, text in templates:
            tk.Button(tpl, text=label, bg=BG2, fg=TEXT, relief="flat",
                      bd=0, font=("Yu Gothic UI", 8), cursor="hand2", padx=8,
                      command=lambda t=text: self._insert_template(t)).pack(
                side="left", padx=2, pady=2)

        # TABエディタ
        ed_frame = tk.Frame(parent, bg=BG)
        ed_frame.pack(fill="both", expand=True)

        self.tab_editor = tk.Text(
            ed_frame,
            bg=TAB_BG, fg="#c0caf5",
            insertbackground="#c0caf5",
            selectbackground=ACCENT, selectforeground=TEXT,
            relief="flat", bd=0, padx=18, pady=14,
            font=("Consolas", 13),
            undo=True, maxundo=100,
            spacing1=2, spacing3=2,
            wrap="none",
        )
        for name, color in STRING_COLORS.items():
            self.tab_editor.tag_config(f"str_{name}", foreground=color)
        self.tab_editor.tag_config("bracket", foreground=AMBER)
        self.tab_editor.tag_config("chord_label", foreground=GREEN,
                                   font=("Consolas", 13, "italic"))
        self.tab_editor.tag_config("separator", foreground=BORDER)

        vsc = tk.Scrollbar(ed_frame, orient="vertical",
                           command=self.tab_editor.yview,
                           bg=BG, troughcolor=BG, relief="flat", bd=0)
        hsc = tk.Scrollbar(ed_frame, orient="horizontal",
                           command=self.tab_editor.xview,
                           bg=BG, troughcolor=BG, relief="flat", bd=0)
        self.tab_editor.configure(yscrollcommand=vsc.set, xscrollcommand=hsc.set)
        vsc.pack(side="right", fill="y")
        hsc.pack(side="bottom", fill="x")
        self.tab_editor.pack(fill="both", expand=True)
        self.tab_editor.bind("<<Modified>>", self._on_tab_modified)
        self.tab_editor.bind("<KeyRelease>", lambda _: self._highlight())

        # メモ欄
        notes_f = tk.Frame(parent, bg=BG2, padx=14, pady=6)
        notes_f.pack(fill="x")
        tk.Label(notes_f, text="メモ・コード表:", bg=BG2, fg=DIM,
                 font=("Yu Gothic UI", 9)).pack(anchor="w")
        self.notes_text = tk.Text(
            notes_f, bg=BG3, fg=TEXT, insertbackground=TEXT,
            relief="flat", bd=4, font=("Yu Gothic UI", 9),
            height=3, wrap="word",
        )
        self.notes_text.pack(fill="x", pady=(2, 0))
        self.notes_text.bind("<<Modified>>", lambda _: self._mark_dirty())

        # ステータスバー
        self.status_var = tk.StringVar(value="準備完了")
        tk.Label(parent, textvariable=self.status_var, bg=BG3, fg=DIM,
                 font=("Yu Gothic UI", 9), anchor="w", padx=12).pack(
            fill="x", pady=(1, 0))

    def _label(self, parent, text):
        return tk.Label(parent, text=text, bg=parent["bg"], fg=DIM,
                        font=("Yu Gothic UI", 9))

    def _traced_var(self, default=""):
        v = tk.StringVar(value=default)
        v.trace("w", lambda *_: self._mark_dirty())
        return v

    # ─── 曲一覧 ─────────────────────────────────────────────
    def _refresh_song_list(self):
        q = self.search_var.get().strip().lower()
        self.song_listbox.delete(0, "end")
        self._filtered_indices = []
        for i, s in enumerate(self.songs):
            label = f"{s.get('title','無題')}  {s.get('artist','')}".lower()
            if not q or q in label:
                display = s.get("title", "無題")
                if s.get("artist"):
                    display = f"{display}  —  {s['artist']}"
                self.song_listbox.insert("end", f"  {display}")
                self._filtered_indices.append(i)
        self.count_label.config(text=f"{len(self._filtered_indices)} 曲")

    def _on_song_select(self, _=None):
        sel = self.song_listbox.curselection()
        if not sel:
            return
        idx = self._filtered_indices[sel[0]]
        if idx == self.current_song_idx:
            return
        if self._dirty:
            self._save_current(silent=True)
        self._select_song(idx)

    def _select_song(self, idx: int):
        self.current_song_idx = idx
        self.current_section_idx = 0
        song = self.songs[idx]

        for i, fi in enumerate(self._filtered_indices):
            if fi == idx:
                self.song_listbox.selection_clear(0, "end")
                self.song_listbox.selection_set(i)
                self.song_listbox.see(i)
                break

        # フォーム更新（traceを一時無効にするため直接set）
        for var, key in [
            (self.title_var,  "title"),
            (self.artist_var, "artist"),
            (self.tuning_var, "tuning"),
            (self.bpm_var,    "bpm"),
            (self.capo_var,   "capo"),
        ]:
            var.set(song.get(key, ""))

        self.notes_text.delete("1.0", "end")
        self.notes_text.insert("end", song.get("notes", ""))
        self.notes_text.edit_modified(False)

        self._rebuild_sections()
        self._dirty = False
        self._update_status()

    def _rebuild_sections(self):
        for w in self._sec_inner.winfo_children():
            w.destroy()
        self._section_buttons.clear()

        if self.current_song_idx is None:
            return
        song = self.songs[self.current_song_idx]
        for i, sec in enumerate(song["sections"]):
            active = i == self.current_section_idx
            b = tk.Button(
                self._sec_inner, text=sec["name"],
                bg=ACCENT if active else "#0a0a10",
                fg=TEXT, relief="flat", bd=0,
                font=("Yu Gothic UI", 9, "bold" if active else "normal"),
                cursor="hand2", padx=12, pady=6,
                command=lambda i=i: self._switch_section(i),
            )
            b.pack(side="left", padx=(1, 0))
            self._section_buttons.append(b)

        self._load_section(self.current_section_idx)

    def _switch_section(self, idx: int):
        if self.current_song_idx is None:
            return
        self._flush_tab()
        self.current_section_idx = idx
        self._rebuild_sections()

    def _load_section(self, idx: int):
        song = self.songs[self.current_song_idx]
        idx = min(idx, len(song["sections"]) - 1)
        self.tab_editor.delete("1.0", "end")
        self.tab_editor.insert("end", song["sections"][idx].get("tab", EMPTY_TAB))
        self.tab_editor.edit_modified(False)
        self._highlight()

    def _flush_tab(self):
        if self.current_song_idx is None:
            return
        song = self.songs[self.current_song_idx]
        if self.current_section_idx < len(song["sections"]):
            song["sections"][self.current_section_idx]["tab"] = \
                self.tab_editor.get("1.0", "end-1c")

    # ─── エディタイベント ────────────────────────────────────
    def _on_tab_modified(self, _=None):
        if self.tab_editor.edit_modified():
            self._mark_dirty()
            self.tab_editor.edit_modified(False)

    def _mark_dirty(self):
        self._dirty = True
        self._update_status()

    def _update_status(self):
        if self.current_song_idx is None:
            self.status_var.set("曲を選択してください")
            return
        s = self.songs[self.current_song_idx]
        dot = "  ●未保存" if self._dirty else ""
        self.status_var.set(
            f"{s['title']}  |  {s.get('tuning','')}  |  更新: {s['modified']}{dot}"
        )

    def _highlight(self):
        editor = self.tab_editor
        for tag in ["str_e","str_B","str_G","str_D","str_A","str_E",
                    "bracket","chord_label","separator"]:
            editor.tag_remove(tag, "1.0", "end")

        for i, line in enumerate(editor.get("1.0","end").split("\n")):
            row = i + 1
            stripped = line.strip()
            # 弦名のカラー
            for name in ("e","B","G","D","A","E"):
                if stripped.startswith(f"{name}|"):
                    col = line.index(name)
                    editor.tag_add(f"str_{name}", f"{row}.{col}", f"{row}.{col+1}")
                    break
            # [ ] カッコ
            for j, ch in enumerate(line):
                if ch in "[]":
                    editor.tag_add("bracket", f"{row}.{j}", f"{row}.{j+1}")
            # セクション区切り
            if stripped.startswith("─") or stripped.startswith("-" * 5):
                editor.tag_add("separator", f"{row}.0", f"{row}.end")
            # Chord: ラベル
            if stripped.startswith("[ Chord"):
                editor.tag_add("chord_label", f"{row}.0", f"{row}.end")

    def _insert_template(self, text: str):
        if self.current_song_idx is None:
            return
        pos = self.tab_editor.index("insert")
        self.tab_editor.insert(pos, "\n" + text + "\n")
        self._mark_dirty()

    # ─── CRUD ────────────────────────────────────────────────
    def _new_song(self, _=None):
        if self._dirty:
            self._save_current(silent=True)
        title = simpledialog.askstring("新しい曲", "曲名を入力してください:",
                                        parent=self, initialvalue="新しい曲")
        if not title:
            return
        artist = simpledialog.askstring("アーティスト", "アーティスト名 (省略可):",
                                         parent=self, initialvalue="")
        s = new_song(title, artist or "")
        self.songs.append(s)
        save_songs(self.songs)
        self._refresh_song_list()
        self._select_song(len(self.songs) - 1)

    def _analyze_key(self):
        if self.current_song_idx is None:
            messagebox.showinfo("曲を選択","先に曲を選択してください。")
            return
        self._flush_tab()
        song = self.songs[self.current_song_idx]
        tab  = song["sections"][self.current_section_idx].get("tab","")
        all_tabs = "\n".join(s.get("tab","") for s in song["sections"])
        KeyAnalysisWindow(self, all_tabs, song.get("tuning","Standard (EADGBe)"),
                          song.get("title",""))

    def _delete_song(self):
        if self.current_song_idx is None:
            return
        title = self.songs[self.current_song_idx]["title"]
        if not messagebox.askyesno("削除確認",
                                    f"「{title}」を削除しますか？\nこの操作は元に戻せません。"):
            return
        self.songs.pop(self.current_song_idx)
        save_songs(self.songs)
        self.current_song_idx = None
        self._dirty = False
        self.tab_editor.delete("1.0", "end")
        self.notes_text.delete("1.0", "end")
        for w in self._sec_inner.winfo_children():
            w.destroy()
        self._refresh_song_list()
        self.status_var.set("削除しました")

    def _save_current(self, _=None, silent=False):
        if self.current_song_idx is None:
            return
        self._flush_tab()
        s = self.songs[self.current_song_idx]
        s["title"]    = self.title_var.get() or "無題"
        s["artist"]   = self.artist_var.get()
        s["tuning"]   = self.tuning_var.get()
        s["bpm"]      = self.bpm_var.get()
        s["capo"]     = self.capo_var.get()
        s["notes"]    = self.notes_text.get("1.0", "end-1c")
        s["modified"] = datetime.now().strftime("%Y-%m-%d")
        save_songs(self.songs)
        self._dirty = False
        self._refresh_song_list()

        for i, fi in enumerate(self._filtered_indices):
            if fi == self.current_song_idx:
                self.song_listbox.selection_set(i)
                break

        if not silent:
            self.status_var.set(f"💾 保存しました — {s['title']}")

    def _rename_song(self):
        if self.current_song_idx is None:
            return
        old = self.songs[self.current_song_idx]["title"]
        new = simpledialog.askstring("曲名変更", "新しい曲名:", parent=self,
                                      initialvalue=old)
        if new and new != old:
            self.title_var.set(new)
            self._save_current(silent=True)
            self._refresh_song_list()

    # ─── セクション管理 ─────────────────────────────────────
    def _add_section(self):
        if self.current_song_idx is None:
            return
        song = self.songs[self.current_song_idx]
        default = f"セクション {len(song['sections'])+1}"
        name = simpledialog.askstring("セクション追加", "セクション名:",
                                       parent=self, initialvalue=default)
        if not name:
            return
        self._flush_tab()
        song["sections"].append(new_section(name))
        self.current_section_idx = len(song["sections"]) - 1
        self._rebuild_sections()
        self._mark_dirty()

    def _rename_section(self):
        if self.current_song_idx is None:
            return
        song = self.songs[self.current_song_idx]
        old = song["sections"][self.current_section_idx]["name"]
        new = simpledialog.askstring("セクション名変更", "新しい名前:",
                                      parent=self, initialvalue=old)
        if new and new != old:
            song["sections"][self.current_section_idx]["name"] = new
            self._rebuild_sections()
            self._mark_dirty()

    # ─── 画像インポート（Claude API）───────────────────────
    def _import_image(self):
        if self.current_song_idx is None:
            messagebox.showinfo("曲を選択", "先に曲を選択または作成してください。")
            return

        path = filedialog.askopenfilename(
            title="TAB譜の写真・スクリーンショットを選択",
            filetypes=[
                ("画像ファイル", "*.png *.jpg *.jpeg *.bmp *.gif *.webp"),
                ("すべてのファイル", "*.*"),
            ],
        )
        if not path:
            return

        if not HAS_ANTHROPIC:
            messagebox.showerror(
                "ライブラリ不足",
                "anthropicライブラリがインストールされていません。\n\n"
                "ターミナルで実行してください:\n  pip install anthropic",
            )
            return

        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not api_key:
            api_key = simpledialog.askstring(
                "API Key", "ANTHROPIC_API_KEY を入力してください:\n"
                "(設定後は環境変数に保存されます)",
                parent=self, show="*",
            )
            if not api_key:
                return
            os.environ["ANTHROPIC_API_KEY"] = api_key

        self.status_var.set("⏳  AI がTAB譜を読み取り中… しばらくお待ちください")
        self.update()

        try:
            with open(path, "rb") as f:
                img_b64 = base64.standard_b64encode(f.read()).decode()

            ext_map = {".png": "image/png", ".jpg": "image/jpeg",
                       ".jpeg": "image/jpeg", ".gif": "image/gif",
                       ".webp": "image/webp", ".bmp": "image/png"}
            media = ext_map.get(Path(path).suffix.lower(), "image/jpeg")

            client = anthropic.Anthropic()
            msg = client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=4096,
                messages=[{
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": media,
                                "data": img_b64,
                            },
                        },
                        {
                            "type": "text",
                            "text": (
                                "この画像にギターのTAB譜が含まれています。\n"
                                "TAB譜をテキスト形式で正確に書き起こしてください。\n\n"
                                "ルール:\n"
                                "- 6弦TAB形式で出力: e|, B|, G|, D|, A|, E| で始まる6行1セット\n"
                                "- フレット番号はそのまま数字で\n"
                                "- ハンマリング: h、プリング: p、スライド: /、ベンド: b、"
                                "  チョーキング: b、ビブラート: ~\n"
                                "- 読み取れない部分は -?- と表記\n"
                                "- コード名があれば [ Chord: xxx ] 形式で直前の行に記載\n"
                                "- 複数フレーズは空行で区切る\n"
                                "- TAB譜のみを出力（説明文不要）\n\n"
                                "TAB譜:"
                            ),
                        },
                    ],
                }]
            )

            result = msg.content[0].text.strip()

            choice = messagebox.askyesnocancel(
                "読み取り完了 ✅",
                "TAB譜の読み取りが完了しました。\n\n"
                "「はい」→ 現在のセクションに追記\n"
                "「いいえ」→ 現在のセクションを置き換え\n"
                "「キャンセル」→ 新しいセクションとして追加",
            )
            if choice is True:
                self.tab_editor.insert("end", "\n\n" + result)
            elif choice is False:
                self.tab_editor.delete("1.0", "end")
                self.tab_editor.insert("end", result)
            else:
                name = simpledialog.askstring(
                    "セクション名", "新しいセクション名:",
                    parent=self, initialvalue="スキャン",
                )
                if name:
                    self._flush_tab()
                    self.songs[self.current_song_idx]["sections"].append(
                        {"name": name, "tab": result}
                    )
                    self.current_section_idx = \
                        len(self.songs[self.current_song_idx]["sections"]) - 1
                    self._rebuild_sections()

            self._mark_dirty()
            self._highlight()
            self.status_var.set("✅  TAB譜の読み取りが完了しました")

        except Exception as e:
            messagebox.showerror("読み取りエラー", f"読み取りに失敗しました:\n{e}")
            self.status_var.set("❌  エラーが発生しました")

    # ─── PDF出力 / 印刷 ─────────────────────────────────────
    def _print_pdf(self, _=None):
        if self.current_song_idx is None:
            messagebox.showinfo("曲を選択", "先に曲を選択してください。")
            return
        if not HAS_REPORTLAB:
            messagebox.showwarning(
                "reportlab未インストール",
                "PDFを生成するには reportlab が必要です。\n\n"
                "  pip install reportlab\n\n"
                "代わりにテキストファイルとして出力します。",
            )
            self._export_text()
            return

        self._save_current(silent=True)
        song = self.songs[self.current_song_idx]
        safe = "".join(c for c in song["title"] if c.isalnum() or c in " _-")
        out = filedialog.asksaveasfilename(
            defaultextension=".pdf",
            filetypes=[("PDF ファイル", "*.pdf")],
            initialfile=f"{safe}.pdf",
            title="PDFとして保存",
        )
        if not out:
            return
        try:
            _generate_pdf(song, out)
            self.status_var.set(f"📄 PDF保存完了: {out}")
            if messagebox.askyesno("PDF保存完了", f"PDFを保存しました。\n開きますか?\n{out}"):
                os.startfile(out)
        except Exception as e:
            messagebox.showerror("PDF エラー", f"PDF生成に失敗しました:\n{e}")

    def _export_text(self):
        song = self.songs[self.current_song_idx]
        safe = "".join(c for c in song["title"] if c.isalnum() or c in " _-")
        out = filedialog.asksaveasfilename(
            defaultextension=".txt",
            filetypes=[("テキスト", "*.txt")],
            initialfile=f"{safe}.txt",
        )
        if not out:
            return
        lines = [
            "=" * 52,
            f"  {song['title']}",
            f"  {song.get('artist','')}",
            f"  Tuning: {song.get('tuning','')}  "
            f"BPM: {song.get('bpm','')}  Capo: {song.get('capo','0')}",
            "=" * 52, "",
        ]
        for sec in song["sections"]:
            lines += [f"─── {sec['name']} ───", "", sec.get("tab",""), ""]
        if song.get("notes"):
            lines += ["─── メモ ───", song["notes"]]
        Path(out).write_text("\n".join(lines), encoding="utf-8")
        self.status_var.set(f"テキスト保存完了: {out}")
        if messagebox.askyesno("保存完了", "テキストファイルを保存しました。開きますか?"):
            os.startfile(out)

    # ─── 終了処理 ────────────────────────────────────────────
    def _on_close(self):
        if self._dirty:
            r = messagebox.askyesnocancel(
                "保存確認",
                "保存されていない変更があります。\n保存してから閉じますか？",
            )
            if r is True:
                self._save_current()
            elif r is None:
                return
        self.destroy()


# ─── PDF生成（reportlab） ────────────────────────────────────
def _generate_pdf(song: dict, path: str):
    from reportlab.pdfgen import canvas as C
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont

    # 日本語フォント登録（利用可能な場合）
    jp_font = "Helvetica"
    try:
        pdfmetrics.registerFont(UnicodeCIDFont("HeiseiMin-W3"))
        jp_font = "HeiseiMin-W3"
    except Exception:
        pass

    W, H = A4
    margin = 20 * mm
    c = C.Canvas(path, pagesize=A4)

    def footer():
        c.setFont("Helvetica", 8)
        c.setFillColorRGB(0.5, 0.5, 0.5)
        c.drawString(margin, 10 * mm, f"{song['title']}  —  {song.get('artist','')}")
        c.drawRightString(W - margin, 10 * mm, "Guitar TAB Manager")

    # 表紙ヘッダー
    c.setFillColorRGB(0.06, 0.06, 0.09)
    c.rect(0, H - 42 * mm, W, 42 * mm, fill=1, stroke=0)

    c.setFillColorRGB(1, 1, 1)
    c.setFont("Helvetica-Bold", 24)
    c.drawString(margin, H - 22 * mm, song["title"])

    c.setFillColorRGB(0.75, 0.75, 0.85)
    c.setFont(jp_font, 13)
    c.drawString(margin, H - 33 * mm, song.get("artist", ""))

    info_parts = []
    if song.get("tuning"):  info_parts.append(f"Tuning: {song['tuning']}")
    if song.get("bpm"):     info_parts.append(f"BPM: {song['bpm']}")
    if song.get("capo") and song["capo"] != "0":
        info_parts.append(f"Capo: {song['capo']}")
    c.setFont("Helvetica", 10)
    c.setFillColorRGB(0.6, 0.6, 0.65)
    c.drawString(margin, H - 41 * mm, "  |  ".join(info_parts))

    y = H - 52 * mm
    footer()

    STRING_RGB = {
        "e": (0.95, 0.42, 0.42), "B": (1.0, 0.70, 0.28),
        "G": (1.0, 0.93, 0.65), "D": (0.66, 0.90, 0.81),
        "A": (0.45, 0.73, 1.0), "E": (0.64, 0.61, 1.0),
    }

    for sec in song["sections"]:
        if y < 55 * mm:
            c.showPage()
            y = H - margin
            footer()

        # セクションヘッダー背景
        c.setFillColorRGB(0.20, 0.18, 0.45)
        c.rect(margin, y - 6 * mm, W - 2 * margin, 7.5 * mm, fill=1, stroke=0)
        c.setFillColorRGB(1, 1, 1)
        c.setFont("Helvetica-Bold", 11)
        c.drawString(margin + 3 * mm, y - 4 * mm, sec["name"])
        y -= 13 * mm

        for line in sec.get("tab", "").split("\n"):
            if y < 25 * mm:
                c.showPage()
                y = H - margin
                footer()

            stripped = line.strip()
            x = margin
            matched = False
            for name, rgb in STRING_RGB.items():
                if stripped.startswith(f"{name}|"):
                    col_pos = line.index(name)
                    # 弦名を色付きで
                    c.setFillColorRGB(*rgb)
                    c.setFont("Courier-Bold", 10)
                    c.drawString(x + col_pos * 6.02, y, name)
                    # 残りをグレーで
                    c.setFillColorRGB(0.75, 0.75, 0.80)
                    c.setFont("Courier", 10)
                    rest_start = col_pos + 1
                    c.drawString(x + rest_start * 6.02, y, line[rest_start:])
                    matched = True
                    break
            if not matched:
                c.setFillColorRGB(0.35, 0.35, 0.45)
                c.setFont("Courier", 10)
                c.drawString(x, y, line)

            y -= 5 * mm

        y -= 5 * mm

    if song.get("notes"):
        if y < 40 * mm:
            c.showPage()
            y = H - margin
            footer()
        c.setStrokeColorRGB(0.3, 0.3, 0.4)
        c.line(margin, y, W - margin, y)
        y -= 7 * mm
        c.setFillColorRGB(0.45, 0.45, 0.55)
        c.setFont(jp_font, 10)
        c.drawString(margin, y, f"メモ: {song['notes']}")

    c.save()


if __name__ == "__main__":
    app = GuitarTabApp()
    app.mainloop()

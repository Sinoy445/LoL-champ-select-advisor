"""
Simple tkinter overlay for LoL Champ Select Advisor.
Shows recommendations in a small always-on-top window.

Note: it can't draw over League in exclusive fullscreen - use Windowed or Borderless.
"""
import os
import queue
import sys
import threading
import traceback
from pathlib import Path
import tkinter as tk

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent))
from advisor import Advisor
from engine import create_engine
from lcu import lcu

POLL_SECONDS = 1.5
DEBUG = os.environ.get("ADVISOR_DEBUG") == "1"

BG, FG, ACCENT, MUTED = "#0d0d0d", "#e0e0e0", "#00d4ff", "#666666"
RED, GREEN, AMBER, GREY = "#ff4444", "#44ff44", "#ffaa00", "#888888"
GOOD_SCORE, OK_SCORE = 53.0, 50.0  # score >= GOOD green, >= OK amber, else red
WIDTH, HEIGHT = 300, 300
ROLE_BUTTONS = [("TOP", "TOP"), ("JG", "JUNGLE"), ("MID", "MID"), ("ADC", "ADC"), ("SUP", "SUPPORT")]


class PollThread(threading.Thread):
    """Polls the League client off the UI thread so a slow/closed client never freezes the overlay."""

    def __init__(self, advisor, updates):
        super().__init__(daemon=True)
        self.advisor = advisor
        self.updates = updates
        self.role_override = None
        self._stop_event = threading.Event()
        self._wake = threading.Event()

    def request_refresh(self):
        self._wake.set()

    def stop(self):
        self._stop_event.set()
        self._wake.set()

    def run(self):
        last = None
        while not self._stop_event.is_set():
            try:
                payload = self.advisor.poll(self.role_override)
            except Exception as exc:  # keep the thread alive no matter what
                traceback.print_exc()
                payload = {"status": "error", "message": f"Error: {exc}"}
            if payload != last:  # only touch the UI / console when something changed
                last = payload
                self.updates.put(payload)
                if DEBUG:
                    print(f"[advisor] {payload}")
            self._wake.wait(POLL_SECONDS)
            self._wake.clear()


class ChampSelectOverlay:
    def __init__(self):
        self.engine = create_engine()
        for warning in self.engine.pool_warnings:
            print(f"[pool] {warning}")
        self.updates = queue.Queue()
        self.poller = PollThread(Advisor(self.engine, lcu), self.updates)

        self.root = tk.Tk()
        self.setup_window()
        self.setup_ui()
        self.poller.start()
        self.root.after(200, self.drain_updates)

    # ------------------------------------------------------------------ window
    def setup_window(self):
        self.root.title("LoL Advisor")
        self.root.geometry(f"{WIDTH}x{HEIGHT}+100+100")
        self.root.attributes("-topmost", True)
        self.root.overrideredirect(True)
        self.root.configure(bg=BG)
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        # Child widgets share the toplevel's bindtags, so these cover the whole window.
        self.root.bind("<Button-1>", self.start_move)
        self.root.bind("<B1-Motion>", self.on_move)

    def _label(self, text="", fg=FG, font=("Consolas", 9), **kw):
        return tk.Label(self.root, text=text, fg=fg, bg=BG, font=font, **kw)

    def setup_ui(self):
        wrap = WIDTH - 24
        self._label("LoL Champ Select Advisor", ACCENT, ("Helvetica", 10, "bold")).pack(pady=(8, 0))

        close = tk.Label(self.root, text="✕", fg=GREY, bg=BG, font=("Helvetica", 10), cursor="hand2")
        close.place(relx=1.0, x=-8, y=4, anchor="ne")
        close.bind("<Button-1>", self.on_close_click)

        self.role_label = self._label("Role: -", wraplength=wrap)
        self.role_label.pack(pady=2)
        self.lane_label = self._label("Lane Opponent: -", wraplength=wrap)
        self.lane_label.pack(pady=2)

        rec_frame = tk.Frame(self.root, bg=BG)
        rec_frame.pack(pady=4, fill=tk.X, padx=10)
        self.rec_labels = []
        for i in range(3):
            lbl = tk.Label(rec_frame, text=f"{i + 1}. -", fg=FG, bg=BG, font=("Consolas", 8),
                           anchor="w", justify=tk.LEFT, wraplength=wrap)
            lbl.pack(fill=tk.X, pady=2)
            self.rec_labels.append(lbl)

        self.note_label = self._label("", GREY, ("Helvetica", 8), wraplength=wrap)
        self.note_label.pack()

        row = tk.Frame(self.root, bg=BG)
        row.pack(pady=(4, 0))
        self.role_buttons = {}
        for text, role in ROLE_BUTTONS:
            b = tk.Label(row, text=text, fg=MUTED, bg=BG, font=("Consolas", 8, "bold"),
                         padx=4, cursor="hand2")
            b.pack(side=tk.LEFT)
            b.bind("<Button-1>", lambda e, r=role: self.on_role_click(r))
            self.role_buttons[role] = b

        self._label("(drag to move | ✕ to exit | click a role to override)", MUTED,
                    ("Helvetica", 7)).pack(pady=(2, 4))

    # ---------------------------------------------------------------- handlers
    def on_close(self):
        self.poller.stop()
        self.root.destroy()

    def on_close_click(self, _event):
        self.on_close()
        return "break"

    def on_role_click(self, role):
        self.poller.role_override = None if self.poller.role_override == role else role
        self.poller.request_refresh()
        return "break"

    def start_move(self, event):
        self._drag_dx = event.x_root - self.root.winfo_x()
        self._drag_dy = event.y_root - self.root.winfo_y()

    def on_move(self, event):
        self.root.geometry(f"+{event.x_root - self._drag_dx}+{event.y_root - self._drag_dy}")

    # ---------------------------------------------------------------- updating
    def drain_updates(self):
        payload = None
        try:
            while True:
                payload = self.updates.get_nowait()
        except queue.Empty:
            pass
        if payload is not None:
            try:
                self.render(payload)
            except Exception as exc:
                print(f"Overlay render error: {exc}")
        self.root.after(200, self.drain_updates)

    def _clear_recs(self):
        for i, lbl in enumerate(self.rec_labels):
            lbl.config(text=f"{i + 1}. -", fg=MUTED)

    def _highlight_role(self, role):
        for r, b in self.role_buttons.items():
            b.config(fg=ACCENT if r == role else MUTED)

    def render(self, p):
        status = p.get("status")
        if status in ("no_client", "error", "idle"):
            self.role_label.config(text=p.get("message", ""), fg=RED if status != "idle" else GREY)
            self.lane_label.config(text="Lane Opponent: -", fg=GREY)
            self.note_label.config(text="")
            self._highlight_role(None)
            self._clear_recs()
            return

        role = p.get("role")
        if role:
            phase = f" • {p['phase']}" if p.get("phase") else ""
            self.role_label.config(text=f"Role: {role} ({p['role_source']}){phase}", fg=ACCENT)
        else:
            self.role_label.config(text="Role: ?", fg=AMBER)
        self._highlight_role(role)

        if p.get("lane_opponent"):
            self.lane_label.config(text=f"Lane Opponent: {p['lane_opponent']}", fg=RED)
        elif p.get("lane_candidates"):
            self.lane_label.config(text=f"Lane Opponent: ? ({' / '.join(p['lane_candidates'])})", fg=AMBER)
        else:
            self.lane_label.config(text="Lane Opponent: -", fg=GREY)

        self.note_label.config(text=p.get("note", ""))

        recs = p.get("recommendations", [])
        for i, lbl in enumerate(self.rec_labels):
            if i >= len(recs):
                lbl.config(text=f"{i + 1}. -", fg=MUTED)
                continue
            rec = recs[i]
            score = float(rec["score"])
            color = GREEN if score >= GOOD_SCORE else AMBER if score >= OK_SCORE else RED
            mark = "▶ " if rec.get("current") else ""
            reasons = " • ".join(rec.get("reasons", [])[:2])
            text = f"{i + 1}. {mark}{rec['name']}  {score:.1f}"
            if reasons:
                text += f"\n    {reasons}"
            lbl.config(text=text, fg=color)

    def run(self):
        self.root.mainloop()


def run_overlay():
    ChampSelectOverlay().run()


if __name__ == "__main__":
    run_overlay()
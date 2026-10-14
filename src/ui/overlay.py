"""
Enhanced tkinter overlay for LoL Champ Select Advisor.
Shows recommendations in a polished, always-on-top window with LoL-inspired design.
"""
import os
import queue
import sys
import threading
import traceback
from pathlib import Path
import tkinter as tk
from tkinter import font as tkfont

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent))
from advisor import Advisor
from engine import create_engine
from lcu import lcu

POLL_SECONDS = 1.5
DEBUG = os.environ.get("ADVISOR_DEBUG") == "1"

# Refined Color Palette - Deeper blacks with LoL-inspired accents
BG_PRIMARY = "#0a0a0a"      # Nearly black background
BG_SECONDARY = "#111111"    # Slightly lighter for depth
BG_TERTIARY = "#1a1a1a"     # For interactive elements
FG_PRIMARY = "#e6e6e6"      # Soft white text
FG_SECONDARY = "#b0b0b0"    # Muted text
ACCENT = "#00d4ff"          # LoL signature blue
ACCENT_GLOW = "#33e0ff"     # Lighter blue for highlights
SUCCESS = "#4ecdc4"         # Teal green (LoL health/mana feel)
WARNING = "#ff9f43"         # Warm orange
ERROR = "#ff6b6b"           # Soft red
MUTED = "#555555"

# Score thresholds (keeping your logic)
GOOD_SCORE, OK_SCORE = 50.5, 49.5

# Layout constants
WIDTH = 320                   # Slightly wider for better proportions
CORNER_RADIUS = 12            # For visual softness (simulated via padding)
PADDING = 16
ITEM_SPACING = 10
LINE_HEIGHT = 22
HEADER_HEIGHT = 40

# =============================================================================
# POLL THREAD CLASS (MUST BE DEFINED FIRST)
# =============================================================================
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


# =============================================================================
# SCORE INDICATOR WIDGET
# =============================================================================
class ScoreIndicator(tk.Canvas):
    """Visual score indicator with circular progress"""
    def __init__(self, parent, size=60, **kwargs):
        super().__init__(parent, width=size, height=size, 
                        bg=BG_PRIMARY, highlightthickness=0, **kwargs)
        self.size = size
        self.score = 50.0
        self.draw_background()
        self.draw_score()

    def draw_background(self):
        # Outer ring
        self.create_oval(2, 2, self.size-2, self.size-2, 
                        outline=BG_SECONDARY, width=2)
        # Inner circle
        self.create_oval(8, 8, self.size-8, self.size-8, 
                        fill=BG_SECONDARY, outline="")

    def draw_score(self):
        self.delete("score_arc")
        self.delete("score_text")

        # Convert score to angle (0-100 -> 0-360 degrees, starting from top)
        angle = 360 * (self.score / 100.0)

        # Determine color based on score
        if self.score >= GOOD_SCORE:
            color = SUCCESS
        elif self.score >= OK_SCORE:
            color = WARNING
        else:
            color = ERROR

        # Draw arc (tkinter arcs start from 3 o'clock, so we adjust)
        self.create_arc(8, 8, self.size-8, self.size-8,
                       start=90, extent=-angle,  # Start at top, go clockwise
                       outline=color, width=4, style="arc",
                       tags="score_arc")

        # Center text
        self.create_text(self.size/2, self.size/2,
                        text=f"{self.score:.1f}",
                        fill=FG_PRIMARY, font=("Segoe UI", 10, "bold"),
                        tags="score_text")

    def set_score(self, score):
        self.score = max(0, min(100, score))
        self.draw_score()

# =============================================================================
# MODERN BUTTON CLASS (HELPER WIDGET)
# =============================================================================
class ModernButton(tk.Label):
    """Custom button-like label with hover effects"""
    def __init__(self, parent, text="", command=None, **kwargs):
        super().__init__(parent, text=text, **kwargs)
        self.command = command
        self.bind("<Enter>", self.on_enter)
        self.bind("<Leave>", self.on_leave)
        self.bind("<Button-1>", self.on_click)
        self.configure(cursor="hand2")

    def on_enter(self, event):
        self.configure(bg=BG_TERTIARY, fg=ACCENT_GLOW)

    def on_leave(self, event):
        self.configure(bg=BG_SECONDARY, fg=FG_PRIMARY)

    def on_click(self, event):
        if self.command:
            self.command()
            
# =============================================================================
# MAIN OVERLAY CLASS
# =============================================================================
class ChampSelectOverlay:
    def __init__(self):
        self.engine = create_engine()
        for warning in self.engine.pool_warnings:
            print(f"[pool] {warning}")
        self.updates = queue.Queue()
        self.poller = PollThread(Advisor(self.engine, lcu), self.updates)  # PollThread is now defined!

        self.root = tk.Tk()
        self.setup_fonts()
        self.setup_window()
        self.setup_ui()
        self.poller.start()
        self.root.after(200, self.drain_updates)

    def setup_fonts(self):
        """Configure custom fonts for better typography"""
        self.font_normal = tkfont.Font(family="Segoe UI", size=9)
        self.font_bold = tkfont.Font(family="Segoe UI", size=9, weight="bold")
        self.font_title = tkfont.Font(family="Segoe UI", size=10, weight="bold")
        self.font_score = tkfont.Font(family="Segoe UI", size=16, weight="bold")
        self.font_small = tkfont.Font(family="Segoe UI", size=8)

    # ------------------------------------------------------------------ window
    def setup_window(self):
        self.root.title("LoL Advisor")
        self.root.geometry(f"{WIDTH}x200+100+100")  # Taller for better layout
        self.root.attributes("-topmost", True)
        self.root.overrideredirect(True)
        self.root.configure(bg=BG_PRIMARY)

        # Add subtle drop shadow effect via multiple windows (simplified)
        self.root.configure(highlightbackground="#000000", highlightthickness=2)

        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        # Enable dragging
        self.root.bind("<Button-1>", self.start_move)
        self.root.bind("<B1-Motion>", self.on_move)

    def _create_frame(self, parent, bg=BG_SECONDARY, **kwargs):
        """Helper to create styled frames"""
        frame = tk.Frame(parent, bg=bg, **kwargs)
        frame.configure(highlightbackground=BG_TERTIARY, highlightthickness=1)
        return frame

    def setup_ui(self):
        # Main container with padding
        main_frame = self._create_frame(self.root, bg=BG_PRIMARY, padx=PADDING, pady=PADDING)
        main_frame.pack(fill=tk.BOTH, expand=True)

        # Header with LoL branding
        header_frame = self._create_frame(main_frame, bg=BG_PRIMARY, height=HEADER_HEIGHT)
        header_frame.pack(fill=tk.X, pady=(0, ITEM_SPACING))
        header_frame.pack_propagate(False)

        # Logo/text area
        logo_frame = tk.Frame(header_frame, bg=BG_PRIMARY)
        logo_frame.pack(side=tk.LEFT, fill=tk.Y)

        # LoL-inspired emblem (simple geometric)
        emblem_canvas = tk.Canvas(logo_frame, width=24, height=24, 
                                bg=BG_PRIMARY, highlightthickness=0)
        emblem_canvas.pack(side=tk.LEFT, padx=(0, 8))
        # Draw simple shield shape
        emblem_canvas.create_polygon(12, 2, 22, 12, 12, 22, 2, 12,
                                   fill="", outline=ACCENT, width=2)
        emblem_canvas.create_line(12, 2, 12, 22, fill=ACCENT, width=1)

        # Text
        text_frame = tk.Frame(logo_frame, bg=BG_PRIMARY)
        text_frame.pack(side=tk.LEFT, fill=tk.Y, expand=True)
        tk.Label(text_frame, text="LoL Advisor", 
                font=self.font_title, fg=ACCENT, bg=BG_PRIMARY).pack(anchor="w")
        tk.Label(text_frame, text="Champ Select Helper", 
                font=self.font_small, fg=FG_SECONDARY, bg=BG_PRIMARY).pack(anchor="w")

        # Close button (modern style)
        close_btn = ModernButton(header_frame, text="✕", 
                                command=self.on_close,
                                bg=BG_PRIMARY, fg=FG_SECONDARY,
                                font=self.font_bold)
        close_btn.pack(side=tk.RIGHT, padx=(8, 0))

        # Status section
        status_frame = self._create_frame(main_frame, bg=BG_SECONDARY)
        status_frame.pack(fill=tk.X, pady=(0, ITEM_SPACING))
        status_frame.configure(padx=PADDING//2, pady=PADDING//2)

        # Role display
        role_frame = tk.Frame(status_frame, bg=BG_SECONDARY)
        role_frame.pack(fill=tk.X, pady=2)
        tk.Label(role_frame, text="Role:", font=self.font_normal,
                fg=FG_SECONDARY, bg=BG_SECONDARY).pack(side=tk.LEFT)
        self.role_label = tk.Label(role_frame, text="-", 
                                 font=self.font_bold, fg=ACCENT, 
                                 bg=BG_SECONDARY)
        self.role_label.pack(side=tk.LEFT, padx=(4, 0))

        # Lane opponent display
        lane_frame = tk.Frame(status_frame, bg=BG_SECONDARY)
        lane_frame.pack(fill=tk.X, pady=2)
        tk.Label(lane_frame, text="Lane:", font=self.font_normal,
                fg=FG_SECONDARY, bg=BG_SECONDARY).pack(side=tk.LEFT)
        self.lane_label = tk.Label(lane_frame, text="-", 
                                 font=self.font_bold, fg=WARNING,
                                 bg=BG_SECONDARY)
        self.lane_label.pack(side=tk.LEFT, padx=(4, 0))

        # Score display (prominent)
        score_frame = self._create_frame(main_frame, bg=BG_TERTIARY)
        score_frame.pack(fill=tk.X, pady=(0, ITEM_SPACING))
        score_frame.configure(padx=PADDING, pady=PADDING)

        tk.Label(score_frame, text="Confidence:", 
                font=self.font_normal, fg=FG_SECONDARY, 
                bg=BG_TERTIARY).pack(anchor="w")

        score_container = tk.Frame(score_frame, bg=BG_TERTIARY)
        score_container.pack(fill=tk.X, pady=(4, 0))

        self.score_indicator = ScoreIndicator(score_container, size=50)
        self.score_indicator.pack(side=tk.LEFT, padx=(0, 12))

        self.score_label = tk.Label(score_container, text="50.0",
                                  font=self.font_score, fg=FG_PRIMARY,
                                  bg=BG_TERTIARY)
        self.score_label.pack(side=tk.LEFT)

        # Recommendations container
        rec_frame = self._create_frame(main_frame, bg=BG_PRIMARY)
        rec_frame.pack(fill=tk.BOTH, expand=True, pady=(0, ITEM_SPACING))
        rec_frame.configure(padx=PADDING//2, pady=PADDING//2)

        tk.Label(rec_frame, text="Top Picks", 
                font=self.font_bold, fg=ACCENT, 
                bg=BG_PRIMARY).pack(anchor="w", pady=(0, 4))

        self.rec_container = tk.Frame(rec_frame, bg=BG_PRIMARY)
        self.rec_container.pack(fill=tk.BOTH, expand=True)

        # Note/status bar at bottom
        self.note_label = tk.Label(main_frame, text="", 
                                 font=self.font_small, fg=FG_SECONDARY,
                                 bg=BG_PRIMARY, wraplength=WIDTH-2*PADDING)
        self.note_label.pack(fill=tk.X, pady=(4, 0))

        # Initialize recommendation rows
        self.rec_rows = []
        for i in range(3):
            row_widgets = self.create_recommendation_row(self.rec_container, i)
            row_widgets['frame'].pack(fill=tk.X, pady=2)  # ✅ FIXED: Pack the frame, not the dict
            self.rec_rows.append(row_widgets)

    def create_recommendation_row(self, parent, index):
        """Create a styled recommendation row"""
        frame = self._create_frame(parent, bg=BG_PRIMARY)

        # Rank badge
        rank_badge = tk.Label(frame, text=f"{index+1}.", 
                            font=self.font_bold, width=3,
                            fg=ACCENT, bg=BG_SECONDARY)
        rank_badge.pack(side=tk.LEFT, padx=(0, 8))

        # Champion info container
        info_frame = tk.Frame(frame, bg=BG_PRIMARY)
        info_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # Champion name
        name_label = tk.Label(info_frame, text="", 
                            font=self.font_bold, fg=FG_PRIMARY,
                            bg=BG_PRIMARY, anchor="w")
        name_label.pack(fill=tk.X, pady=(0, 2))

        # Score and reasons container
        details_frame = tk.Frame(info_frame, bg=BG_PRIMARY)
        details_frame.pack(fill=tk.X)

        # Score badge
        score_badge = tk.Label(details_frame, text="", 
                             font=self.font_small, width=5,
                             fg=FG_PRIMARY, bg=BG_TERTIARY)
        score_badge.pack(side=tk.LEFT, padx=(0, 8))

        # Reasons text
        reasons_label = tk.Label(details_frame, text="", 
                               font=self.font_small, fg=FG_SECONDARY,
                               bg=BG_PRIMARY, anchor="w", justify=tk.LEFT)
        reasons_label.pack(side=tk.LEFT, fill=tk.X, expand=True)

        return {
            'frame': frame,
            'rank': rank_badge,
            'name': name_label,
            'score': score_badge,
            'reasons': reasons_label
        }

    # ---------------------------------------------------------------- handlers
    def on_close(self):
        self.poller.stop()
        self.root.destroy()

    def start_move(self, event):
        self._drag_x = event.x
        self._drag_y = event.y

    def on_move(self, event):
        x = self.root.winfo_x() + event.x - self._drag_x
        y = self.root.winfo_y() + event.y - self._drag_y
        self.root.geometry(f"+{x}+{y}")

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
                import traceback
                traceback.print_exc()
        self.root.after(200, self.drain_updates)

    def render(self, p):
        status = p.get("status")

        # Update role and lane
        if status in ("no_client", "error", "idle", "no_role"):
            self.role_label.config(text="?", fg=FG_SECONDARY)
            self.lane_label.config(text="-", fg=FG_SECONDARY)
            self.score_indicator.set_score(50.0)
            self.score_label.config(text="50.0", fg=FG_PRIMARY)
            self.clear_recommendations()
            self.show_status_message(p.get("message", ""), is_error=status != "idle")
            return

        # Role display
        role = p.get("role", "?")
        self.role_label.config(text=role, fg=ACCENT)

        # Lane opponent
        lane_opp = p.get("lane_opponent")
        if lane_opp:
            self.lane_label.config(text=lane_opp, fg=WARNING)
        else:
            self.lane_label.config(text="-", fg=FG_SECONDARY)

        # Main score
        if p.get("locked"):
            score = p["locked"].get("score", 50.0)
        elif p.get("recommendations"):
            score = p["recommendations"][0].get("score", 50.0) if p["recommendations"] else 50.0
        else:
            score = 50.0

        self.score_indicator.set_score(score)
        self.score_label.config(text=f"{score:.1f}", 
                              fg=self.get_score_color(score))

        # Recommendations
        self.update_recommendations(p)

        # Status message
        note = p.get("note", "")
        data_note = p.get("data_note", "")
        full_note = f"{note} {data_note}".strip()
        self.show_status_message(full_note, is_error="error" in p.get("status", ""))

    def get_score_color(self, score):
        """Get color based on score"""
        if score >= GOOD_SCORE:
            return SUCCESS
        elif score >= OK_SCORE:
            return WARNING
        else:
            return ERROR

    def clear_recommendations(self):
        for row in self.rec_rows:
            row['name'].config(text="-", fg=FG_SECONDARY)
            row['score'].config(text="", bg=BG_PRIMARY)
            row['reasons'].config(text="")
            row['rank'].config(fg=FG_SECONDARY)

    def update_recommendations(self, p):
        self.clear_recommendations()

        locked = p.get("locked")
        if locked:
            # Show locked champion prominently
            row = self.rec_rows[0]
            name = locked.get("name", "Unknown")
            score = locked.get("score", 50.0)
            reasons = locked.get("reads", [])[:2]

            row['name'].config(text=name, fg=self.get_score_color(score))
            row['score'].config(text=f"{score:.1f}", 
                              bg=self.get_score_color(score), fg=BG_PRIMARY)
            row['reasons'].config(text=" • ".join(reasons) if reasons else "")
            row['rank'].config(fg=self.get_score_color(score))

            # Hide other rows
            for row in self.rec_rows[1:]:
                row['frame'].pack_forget()
            return

        # Show regular recommendations
        recs = p.get("recommendations", [])
        for i, (row, rec) in enumerate(zip(self.rec_rows, recs)):
            if i >= len(recs):
                row['frame'].pack_forget()
                continue

            name = rec.get("name", "Unknown")
            score = rec.get("score", 50.0)
            reasons = rec.get("reasons", [])
            is_current = rec.get("current", False)

            # Name with current indicator
            prefix = "▶ " if is_current else ""
            row['name'].config(text=f"{prefix}{name}", 
                             fg=ACCENT if is_current else self.get_score_color(score))

            # Score badge
            score_text = f"{score:.1f}"
            row['score'].config(text=score_text,
                              bg=self.get_score_color(score), fg=BG_PRIMARY)

            # Reasons
            reasons_text = " • ".join(reasons[:2]) if reasons else ""
            row['reasons'].config(text=reasons_text)

            # Rank badge color
            row['rank'].config(fg=self.get_score_color(score))

            row['frame'].pack(fill=tk.X, pady=2)

        # Hide any unused rows
        for row in self.rec_rows[len(recs):]:
            row['frame'].pack_forget()

    def show_status_message(self, message, is_error=False):
        """Show temporary status message"""
        if not message:
            self.note_label.config(text="")
            return

        color = ERROR if is_error else FG_SECONDARY
        self.note_label.config(text=message, fg=color)

    def run(self):
        self.root.mainloop()





def run_overlay():
    ChampSelectOverlay().run()


if __name__ == "__main__":
    run_overlay()

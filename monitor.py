#!/usr/bin/env python3
import json
import os
import re
import socket
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path

import customtkinter as ctk

# Single instance guard via local socket
_LOCK_SOCKET = None
_LOCK_PORT = 59271

def _acquire_instance_lock() -> bool:
    global _LOCK_SOCKET
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        s.bind(("127.0.0.1", _LOCK_PORT))
        s.listen(1)
        _LOCK_SOCKET = s
        return True
    except OSError:
        return False  # Port already in use → another instance is running

CONFIG_PATH = Path(__file__).parent / "config.json"
CLAUDE_BIN = os.path.expanduser("~/.nvm/versions/node/v22.18.0/bin/claude")
TMUX_SESSION = "claude_usage_monitor_probe"

UNSET_VARS = [
    "ANTHROPIC_BEDROCK_BASE_URL", "ANTHROPIC_MODEL", "ANTHROPIC_SMALL_FAST_MODEL",
    "AWS_REGION", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN",
    "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC", "DISABLE_NON_ESSENTIAL_MODEL_CALLS",
    "API_TIMEOUT_MS", "CLAUDE_CODE_USE_BEDROCK", "CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT",
]


def load_config():
    defaults = {
        "refresh_interval_sec": 60,
    }
    try:
        with open(CONFIG_PATH) as f:
            cfg = json.load(f)
        defaults.update(cfg)
    except Exception:
        pass
    return defaults


def _run(cmd: str) -> str:
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    return r.stdout


def fetch_usage() -> dict | None:
    """Launch claude-p in a tmux pane, execute /usage, capture and parse output."""
    unset_prefix = " ".join(f"unset {v};" for v in UNSET_VARS)
    launch_cmd = (
        f"{unset_prefix} "
        f"CLAUDE_CONFIG_DIR=$HOME/.claude-personal "
        f"{CLAUDE_BIN}"
    )

    # Kill any leftover session
    _run(f"tmux kill-session -t {TMUX_SESSION} 2>/dev/null")
    _run(f"tmux new-session -d -s {TMUX_SESSION} -x 160 -y 50")
    _run(f'tmux send-keys -t {TMUX_SESSION} "{launch_cmd}" Enter')

    # Wait for claude to fully initialize (welcome screen appears)
    time.sleep(7)

    # Type /usage without Enter, then press Enter separately
    _run(f'tmux send-keys -t {TMUX_SESSION} "/usage" ""')
    time.sleep(1)
    _run(f'tmux send-keys -t {TMUX_SESSION} "" Enter')

    # Wait for dialog to render
    time.sleep(5)

    pane = _run(f"tmux capture-pane -t {TMUX_SESSION} -p")

    # Send ESC to dismiss, then kill session
    _run(f'tmux send-keys -t {TMUX_SESSION} "" Escape')
    time.sleep(0.5)
    _run(f"tmux kill-session -t {TMUX_SESSION} 2>/dev/null")

    return _parse_usage(pane)


def _parse_usage(text: str) -> dict | None:
    """Parse /usage dialog text into structured data."""
    # Session: "50% used" after "Current session"
    session_pct = None
    session_reset = None
    week_pct = None
    week_reset = None

    lines = text.splitlines()

    i = 0
    while i < len(lines):
        line = lines[i]

        if "Current session" in line:
            # Next non-empty line with % used
            for j in range(i + 1, min(i + 4, len(lines))):
                m = re.search(r"(\d+)%\s*used", lines[j])
                if m:
                    session_pct = int(m.group(1))
                    break
            for j in range(i + 1, min(i + 5, len(lines))):
                m = re.search(r"Resets\s+(.+)", lines[j])
                if m:
                    session_reset = m.group(1).strip()
                    break

        elif "Current week" in line:
            for j in range(i + 1, min(i + 4, len(lines))):
                m = re.search(r"(\d+)%\s*used", lines[j])
                if m:
                    week_pct = int(m.group(1))
                    break
            for j in range(i + 1, min(i + 5, len(lines))):
                m = re.search(r"Resets\s+(.+)", lines[j])
                if m:
                    week_reset = m.group(1).strip()
                    break

        i += 1

    if session_pct is None and week_pct is None:
        return None

    return {
        "session_pct": session_pct,
        "session_reset": session_reset,
        "week_pct": week_pct,
        "week_reset": week_reset,
    }


class UsageBar(ctk.CTkFrame):
    def __init__(self, master, label: str, **kwargs):
        super().__init__(master, fg_color="transparent", **kwargs)

        row = ctk.CTkFrame(self, fg_color="transparent")
        row.pack(fill="x", pady=(0, 2))

        self.label_text = ctk.CTkLabel(row, text=label,
                                       font=ctk.CTkFont(size=11, weight="bold"),
                                       text_color="#999999", anchor="w")
        self.label_text.pack(side="left")

        self.pct_label = ctk.CTkLabel(row, text="–%",
                                      font=ctk.CTkFont(size=11, weight="bold"),
                                      text_color="#555555", anchor="e")
        self.pct_label.pack(side="right")

        self.reset_label = ctk.CTkLabel(row, text="",
                                        font=ctk.CTkFont(size=10),
                                        text_color="#7a7a8a", anchor="e")
        self.reset_label.pack(side="right", padx=(0, 6))

        self.progress = ctk.CTkProgressBar(self, height=10, corner_radius=4)
        self.progress.set(0)
        self.progress.pack(fill="x")

    def update(self, pct: int | None, reset_str: str | None = None):
        if pct is None:
            self.progress.set(0)
            self.pct_label.configure(text="–%", text_color="#555555")
            return

        ratio = min(pct / 100.0, 1.0)
        self.progress.set(ratio)

        if pct < 60:
            color = "#2fa572"
        elif pct < 85:
            color = "#e8a020"
        else:
            color = "#e05050"
        self.progress.configure(progress_color=color)
        self.pct_label.configure(text=f"{pct}%", text_color=color)

        if reset_str:
            self.reset_label.configure(text=f"↺ {reset_str}")


class MonitorApp(ctk.CTk):
    def __init__(self):
        super().__init__()

        self.config_data = load_config()
        self._refresh_job = None
        self._fetching = False

        self.title("Claude Usage")
        self.geometry("280x180")
        self.resizable(False, False)
        self.attributes("-topmost", True)

        self.update_idletasks()
        sw = self.winfo_screenwidth()
        self.geometry(f"280x180+{sw - 296}+40")

        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")

        self._build_ui()
        self.refresh()

    def _build_ui(self):
        # Title bar
        title_frame = ctk.CTkFrame(self, fg_color=("#1a1a2e", "#1a1a2e"),
                                   corner_radius=0, height=32)
        title_frame.pack(fill="x")
        title_frame.pack_propagate(False)

        self.updated_title = ctk.CTkLabel(title_frame, text="",
                     font=ctk.CTkFont(size=10),
                     text_color="#555566")
        self.updated_title.pack(side="left", padx=10)

        ctk.CTkButton(title_frame, text="✕", width=24, height=24,
                      fg_color="transparent", hover_color="#550000",
                      font=ctk.CTkFont(size=12), command=self.destroy).pack(side="right", padx=3)

        self.refresh_btn = ctk.CTkButton(title_frame, text="↻", width=24, height=24,
                                         fg_color="transparent", hover_color="#1a3a5e",
                                         font=ctk.CTkFont(size=14), command=self.refresh)
        self.refresh_btn.pack(side="right", padx=1)

        # Content
        content = ctk.CTkFrame(self, fg_color="transparent")
        content.pack(fill="both", expand=True, padx=12, pady=10)

        self.bar_session = UsageBar(content, "Session")
        self.bar_session.pack(fill="x", pady=(0, 10))

        self.bar_week = UsageBar(content, "Weekly")
        self.bar_week.pack(fill="x")

        # Footer
        footer = ctk.CTkFrame(self, fg_color=("#111122", "#111122"),
                              corner_radius=0, height=24)
        footer.pack(fill="x", side="bottom")
        footer.pack_propagate(False)

        self.status_label = ctk.CTkLabel(footer, text="Fetching…",
                                         font=ctk.CTkFont(size=9),
                                         text_color="#444444")
        self.status_label.pack(side="left", padx=8)

        interval = self.config_data.get("refresh_interval_sec", 60)
        ctk.CTkLabel(footer, text=f"⟳ {interval}s",
                     font=ctk.CTkFont(size=9),
                     text_color="#444444").pack(side="right", padx=8)

    def refresh(self):
        if self._fetching:
            return
        if self._refresh_job:
            self.after_cancel(self._refresh_job)
            self._refresh_job = None

        self._fetching = True
        self.refresh_btn.configure(state="disabled", text="…")
        self.status_label.configure(text="Fetching…")
        threading.Thread(target=self._fetch_and_update, daemon=True).start()

    def _fetch_and_update(self):
        data = fetch_usage()
        self.after(0, lambda: self._apply(data))

    def _apply(self, data: dict | None):
        self._fetching = False
        self.refresh_btn.configure(state="normal", text="↻")

        if data:
            self.bar_session.update(data.get("session_pct"), data.get("session_reset"))
            self.bar_week.update(data.get("week_pct"), data.get("week_reset"))
            now_str = datetime.now().strftime("%H:%M")
            self.updated_title.configure(text=f"updated {now_str}")
            self.status_label.configure(text=f"Updated {datetime.now().strftime('%H:%M:%S')}")
        else:
            self.updated_title.configure(text="failed")
            self.status_label.configure(text="Failed to fetch")

        interval_ms = int(self.config_data.get("refresh_interval_sec", 60)) * 1000
        self._refresh_job = self.after(interval_ms, self.refresh)


if __name__ == "__main__":
    if not _acquire_instance_lock():
        raise SystemExit(0)  # Already running
    app = MonitorApp()
    app.mainloop()

"""
YouTube Transcript Downloader Pro — 字幕小幫手
========================================================
功能：
  - YouTube 字幕擷取（多語言、時間軸選項、即時預覽）
  - 影片下載（強制 MP4 合併、即時進度、取消）
  - 音訊下載（MP3 / M4A）
  - 字幕 + 影片同步下載
  - 環境自動偵測（yt-dlp / ffmpeg）

作者：YLong
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import threading
import tkinter as tk
import tkinter.scrolledtext as scrolledtext
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, ttk
from typing import Callable, Optional

from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api._errors import NoTranscriptFound, TranscriptsDisabled

import whisper_core as wc

# ══════════════════════════════════════════════════════════
# 設計常數
# ══════════════════════════════════════════════════════════
COLORS = {
    "bg":            "#fdf6f0",
    "surface":       "#ffffff",
    "surface2":      "#fef0f5",
    "surface3":      "#faeaf0",
    "border":        "#f0c4d4",
    "border_soft":   "#f8dde5",
    "accent":        "#e8749a",
    "accent_hover":  "#d45c84",
    "accent_soft":   "#f5b8ce",
    "success":       "#6dbf8e",
    "success_soft":  "#c3e4cf",
    "error":         "#e07070",
    "warning":       "#e8a84a",
    "text_primary":  "#3d2b35",
    "text_secondary": "#9a7080",
    "text_muted":    "#c4a8b4",
    "btn_dl":        "#8b9fe8",
    "btn_dl_hover":  "#7088d4",
    "btn_cancel":    "#d89ba8",
    "btn_cancel_hover": "#c4808e",
}

FONTS = {
    "title":    ("SF Pro Display", 18, "bold"),
    "heading":  ("SF Pro Display", 11, "bold"),
    "body":     ("SF Pro Text",    10),
    "mono":     ("Menlo",          10),
    "small":    ("SF Pro Text",     9),
    "tiny":     ("SF Pro Text",     8),
    "btn":      ("SF Pro Text",    10, "bold"),
}

# ══════════════════════════════════════════════════════════
# 語言與下載格式
# ══════════════════════════════════════════════════════════
LANG_MAP: dict[str, Optional[list[str]]] = {
    "自動偵測": None,
    "繁體中文": ["zh-TW", "zh-Hant"],
    "簡體中文": ["zh-CN", "zh-Hans", "zh"],
    "英文":    ["en"],
    "日文":    ["ja"],
    "韓文":    ["ko"],
}

# 優先原生 mp4 流 → 不需重編碼；若無 mp4 則 remux（僅換殼，極快）
VIDEO_FORMATS: dict[str, dict] = {
    "MP4 最佳畫質": {
        "kind": "video",
        "ext": "mp4",
        "args": [
            "-f", "bv*[ext=mp4]+ba[ext=m4a]/b[ext=mp4]/bv*+ba/b",
            "--merge-output-format", "mp4",
            "--remux-video", "mp4",
        ],
    },
    "MP4 1080p": {
        "kind": "video",
        "ext": "mp4",
        "args": [
            "-f", "bv*[height<=1080][ext=mp4]+ba[ext=m4a]/b[height<=1080][ext=mp4]/bv*[height<=1080]+ba/b[height<=1080]",
            "--merge-output-format", "mp4",
            "--remux-video", "mp4",
        ],
    },
    "MP4 720p": {
        "kind": "video",
        "ext": "mp4",
        "args": [
            "-f", "bv*[height<=720][ext=mp4]+ba[ext=m4a]/b[height<=720][ext=mp4]/bv*[height<=720]+ba/b[height<=720]",
            "--merge-output-format", "mp4",
            "--remux-video", "mp4",
        ],
    },
    "MP4 480p": {
        "kind": "video",
        "ext": "mp4",
        "args": [
            "-f", "bv*[height<=480][ext=mp4]+ba[ext=m4a]/b[height<=480][ext=mp4]/bv*[height<=480]+ba/b[height<=480]",
            "--merge-output-format", "mp4",
            "--remux-video", "mp4",
        ],
    },
    "MP3 僅音訊": {
        "kind": "audio",
        "ext": "mp3",
        "args": ["-x", "--audio-format", "mp3", "--audio-quality", "0"],
    },
    "M4A 僅音訊": {
        "kind": "audio",
        "ext": "m4a",
        "args": ["-x", "--audio-format", "m4a", "--audio-quality", "0"],
    },
}

VIDEO_ID_PATTERNS = [
    r"(?:https?://)?(?:www\.)?youtube\.com/watch\?v=([a-zA-Z0-9_-]{11})",
    r"(?:https?://)?(?:www\.)?youtu\.be/([a-zA-Z0-9_-]{11})",
    r"(?:https?://)?(?:www\.)?youtube\.com/embed/([a-zA-Z0-9_-]{11})",
    r"(?:https?://)?(?:www\.)?youtube\.com/shorts/([a-zA-Z0-9_-]{11})",
    r"(?:https?://)?(?:www\.)?youtube\.com/v/([a-zA-Z0-9_-]{11})",
]

COMMON_BIN_PATHS = [
    "/opt/homebrew/bin",
    "/usr/local/bin",
    "/usr/bin",
    "/bin",
]


# ══════════════════════════════════════════════════════════
# 環境偵測
# ══════════════════════════════════════════════════════════
@dataclass
class Environment:
    yt_dlp: Optional[str] = None
    ffmpeg: Optional[str] = None
    ffprobe: Optional[str] = None
    whisper: Optional[str] = None

    @classmethod
    def detect(cls) -> "Environment":
        script_dir = Path(__file__).resolve().parent
        venv_bin = script_dir / ".venv" / "bin"

        def find(name: str) -> Optional[str]:
            # 1) 同目錄 .venv/bin 優先
            candidate = venv_bin / name
            if candidate.exists():
                return str(candidate)
            # 2) PATH
            found = shutil.which(name)
            if found:
                return found
            # 3) 常見 Homebrew / 系統位置
            for d in COMMON_BIN_PATHS:
                p = Path(d) / name
                if p.exists():
                    return str(p)
            return None

        return cls(
            yt_dlp=find("yt-dlp"),
            ffmpeg=find("ffmpeg"),
            ffprobe=find("ffprobe"),
            whisper=wc.find_whisper_bin(),
        )

    @property
    def ready_video(self) -> bool:
        return bool(self.yt_dlp and self.ffmpeg)

    @property
    def ready_transcript(self) -> bool:
        return True  # youtube_transcript_api 是 Python 套件，此處必然存在

    @property
    def ready_whisper(self) -> bool:
        return bool(self.whisper and self.yt_dlp and self.ffmpeg)

    def subprocess_env(self) -> dict:
        """合併系統 PATH 與 ffmpeg 所在目錄，避免子程序找不到 ffmpeg。"""
        env = os.environ.copy()
        extras = []
        for tool in (self.ffmpeg, self.yt_dlp):
            if tool:
                extras.append(str(Path(tool).parent))
        if extras:
            current = env.get("PATH", "")
            paths = extras + [p for p in current.split(os.pathsep) if p]
            env["PATH"] = os.pathsep.join(dict.fromkeys(paths))
        return env


# ══════════════════════════════════════════════════════════
# 純函式工具
# ══════════════════════════════════════════════════════════
def get_video_id(url: str) -> Optional[str]:
    if not url:
        return None
    for pattern in VIDEO_ID_PATTERNS:
        m = re.search(pattern, url)
        if m:
            return m.group(1)
    return None


def format_timestamp(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    return f"{h:02d}:{m:02d}:{s:02d}" if h > 0 else f"{m:02d}:{s:02d}"


def format_duration(seconds: Optional[float]) -> str:
    if seconds is None:
        return "—"
    try:
        return format_timestamp(float(seconds))
    except (TypeError, ValueError):
        return "—"


def format_transcript(items: list, with_ts: bool) -> str:
    if with_ts:
        return "\n".join(f"[{format_timestamp(i['start'])}] {i['text']}" for i in items)
    return "\n".join(i["text"] for i in items)


def fetch_transcript(video_id: str, lang_codes: Optional[list]) -> list:
    try:
        if lang_codes:
            return YouTubeTranscriptApi.get_transcript(video_id, languages=lang_codes)
        try:
            return YouTubeTranscriptApi.get_transcript(
                video_id, languages=["zh-TW", "zh-Hant", "zh-Hans", "zh", "en"]
            )
        except Exception:
            return YouTubeTranscriptApi.get_transcript(video_id)
    except TranscriptsDisabled:
        raise RuntimeError("此影片已停用字幕功能")
    except NoTranscriptFound:
        raise RuntimeError("找不到所選語言的字幕，請嘗試其他語言")
    except Exception as e:
        raise RuntimeError(f"獲取字幕時發生錯誤：{e}")


def safe_filename(name: str, max_len: int = 80) -> str:
    name = re.sub(r'[\\/:*?"<>|\r\n\t]+', "_", name)
    name = name.strip(" .")
    return name[:max_len] if name else "untitled"


# ══════════════════════════════════════════════════════════
# 影片資訊預取
# ══════════════════════════════════════════════════════════
@dataclass
class VideoInfo:
    title: str = ""
    duration: Optional[float] = None
    uploader: str = ""


def probe_video_info(env: Environment, url: str, timeout: int = 20) -> VideoInfo:
    if not env.yt_dlp:
        return VideoInfo()
    cmd = [
        env.yt_dlp,
        "--skip-download",
        "--no-warnings",
        "--print", "%(title)s\t%(duration)s\t%(uploader)s",
        url,
    ]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env.subprocess_env(),
        )
        if result.returncode != 0:
            return VideoInfo()
        line = result.stdout.strip().splitlines()[0] if result.stdout.strip() else ""
        parts = line.split("\t")
        title = parts[0] if len(parts) > 0 else ""
        duration_raw = parts[1] if len(parts) > 1 else ""
        uploader = parts[2] if len(parts) > 2 else ""
        try:
            duration = float(duration_raw) if duration_raw and duration_raw != "NA" else None
        except ValueError:
            duration = None
        return VideoInfo(title=title, duration=duration, uploader=uploader)
    except Exception:
        return VideoInfo()


# ══════════════════════════════════════════════════════════
# 下載進度解析
# ══════════════════════════════════════════════════════════
@dataclass
class DownloadProgress:
    percent: float = 0.0
    speed: str = ""
    eta: str = ""
    phase: str = ""        # download / merge / finished
    filename: str = ""


class ProgressParser:
    """解析 yt-dlp 的 --newline 輸出。"""
    # [download]  13.4% of   45.21MiB at 2.30MiB/s ETA 00:15
    RX_DL = re.compile(
        r"\[download\]\s+(?P<pct>[\d.]+)%\s+of\s+~?\s*(?P<size>[\d.]+\w+)"
        r"(?:\s+at\s+(?P<speed>[\d.]+\w+/s))?"
        r"(?:\s+ETA\s+(?P<eta>[\d:]+))?"
    )
    RX_DEST = re.compile(r"\[download\]\s+Destination:\s+(?P<path>.+)")
    RX_MERGE = re.compile(r"\[Merger\]|\[ExtractAudio\]|\[VideoRemuxer\]|\[VideoConvertor\]")
    RX_FINISHED = re.compile(r"\[download\]\s+100(?:\.0+)?%")

    def parse(self, line: str, state: DownloadProgress) -> DownloadProgress:
        line = line.strip()
        if not line:
            return state
        m = self.RX_DEST.search(line)
        if m:
            state.filename = Path(m.group("path")).name
            state.phase = "download"
            return state
        if self.RX_MERGE.search(line):
            state.phase = "merge"
            state.percent = 99.0
            return state
        m = self.RX_DL.search(line)
        if m:
            try:
                state.percent = float(m.group("pct"))
            except ValueError:
                pass
            state.speed = m.group("speed") or state.speed
            state.eta = m.group("eta") or ""
            state.phase = "download" if state.percent < 100 else "merge"
            return state
        return state


# ══════════════════════════════════════════════════════════
# 下載任務
# ══════════════════════════════════════════════════════════
class DownloadTask:
    def __init__(
        self,
        env: Environment,
        url: str,
        fmt: dict,
        folder: str,
        on_progress: Callable[[DownloadProgress], None],
        on_done: Callable[[bool, str], None],
        mirror: bool = False,
    ):
        self.env = env
        self.url = url
        self.fmt = fmt
        self.folder = folder
        self.on_progress = on_progress
        self.on_done = on_done
        self.mirror = mirror
        self._proc: Optional[subprocess.Popen] = None
        self._mirror_proc: Optional[subprocess.Popen] = None
        self._cancelled = False
        self._parser = ProgressParser()

    def build_cmd(self) -> list[str]:
        cmd: list[str] = [self.env.yt_dlp or "yt-dlp"]
        cmd += self.fmt["args"]
        # 鏡像模式需要穩定檔名追蹤 → 用 print-to-file 輸出實際檔案路徑
        filename_tmpl = "%(title).80s.%(ext)s"
        if self.mirror:
            filename_tmpl = "%(title).80s__raw.%(ext)s"
        cmd += [
            "-o", os.path.join(self.folder, filename_tmpl),
            "--newline",
            "--no-warnings",
            "--progress",
            "--no-part",
            "--print", "after_move:filepath",
        ]
        if self.env.ffmpeg:
            cmd += ["--ffmpeg-location", self.env.ffmpeg]
        cmd.append(self.url)
        return cmd

    def start(self) -> None:
        threading.Thread(target=self._run, daemon=True).start()

    def cancel(self) -> None:
        self._cancelled = True
        for p in (self._proc, self._mirror_proc):
            if p and p.poll() is None:
                try:
                    p.terminate()
                except Exception:
                    pass

    def _run(self) -> None:
        state = DownloadProgress()
        output_path = ""
        try:
            self._proc = subprocess.Popen(
                self.build_cmd(),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                env=self.env.subprocess_env(),
            )
            assert self._proc.stdout is not None
            last_err = ""
            for line in self._proc.stdout:
                if self._cancelled:
                    break
                stripped = line.strip()
                # after_move:filepath 會直接列印絕對路徑
                if stripped and os.path.isabs(stripped) and os.path.exists(stripped):
                    output_path = stripped
                state = self._parser.parse(line, state)
                self.on_progress(state)
                if "ERROR:" in line or "error" in line.lower()[:20]:
                    last_err = line.strip()
            code = self._proc.wait()
            if self._cancelled:
                self.on_done(False, "已取消下載")
                return
            if code != 0:
                self.on_done(False, last_err or f"yt-dlp 結束碼 {code}")
                return

            # 鏡像處理
            if self.mirror and self.fmt["kind"] == "video":
                if not output_path:
                    self.on_done(False, "下載成功但找不到檔案路徑，無法鏡像")
                    return
                ok, msg = self._apply_mirror(output_path, state)
                if not ok:
                    self.on_done(False, msg)
                    return

            state.percent = 100.0
            state.phase = "finished"
            self.on_progress(state)
            self.on_done(True, "下載完成")
        except FileNotFoundError:
            self.on_done(False, "找不到 yt-dlp，請先安裝")
        except Exception as e:
            self.on_done(False, f"錯誤：{e}")

    def _apply_mirror(self, src: str, state: DownloadProgress) -> tuple[bool, str]:
        """對下載完的影片做水平翻轉 → 原檔移除。音訊串流直接複製、影片重新編碼。"""
        if not self.env.ffmpeg:
            return False, "鏡像需要 ffmpeg"
        src_path = Path(src)
        # 去除 __raw 後綴
        final_name = src_path.name.replace("__raw.", "__mirror.")
        dst = src_path.with_name(final_name)

        state.phase = "mirror"
        state.percent = 99.0
        state.filename = dst.name
        self.on_progress(state)

        cmd = [
            self.env.ffmpeg,
            "-y", "-hide_banner", "-loglevel", "error",
            "-i", str(src_path),
            "-vf", "hflip",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
            "-c:a", "copy",
            "-movflags", "+faststart",
            str(dst),
        ]
        try:
            self._mirror_proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                env=self.env.subprocess_env(),
            )
            assert self._mirror_proc.stdout is not None
            err_tail = ""
            for line in self._mirror_proc.stdout:
                if self._cancelled:
                    break
                if line.strip():
                    err_tail = line.strip()
            code = self._mirror_proc.wait()
            if self._cancelled:
                try:
                    dst.unlink(missing_ok=True)
                except Exception:
                    pass
                return False, "已取消鏡像"
            if code != 0:
                return False, f"鏡像失敗：{err_tail or code}"
            # 成功 → 刪除原始檔
            try:
                src_path.unlink(missing_ok=True)
            except Exception:
                pass
            return True, "鏡像完成"
        except Exception as e:
            return False, f"鏡像錯誤：{e}"


# ══════════════════════════════════════════════════════════
# 自訂元件
# ══════════════════════════════════════════════════════════
class HoverButton(tk.Button):
    def __init__(self, master, normal_bg, hover_bg, **kw):
        super().__init__(
            master, bg=normal_bg, activebackground=hover_bg,
            relief="flat", bd=0, cursor="hand2", **kw,
        )
        self._normal = normal_bg
        self._hover = hover_bg
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)

    def _on_enter(self, _):
        if self["state"] != tk.DISABLED:
            self.config(bg=self._hover)

    def _on_leave(self, _):
        if self["state"] != tk.DISABLED:
            self.config(bg=self._normal)

    def set_colors(self, normal_bg: str, hover_bg: str) -> None:
        self._normal = normal_bg
        self._hover = hover_bg
        self.config(bg=normal_bg, activebackground=hover_bg)


class ToastNotification:
    def __init__(self, root: tk.Tk):
        self.root = root
        self._win: Optional[tk.Toplevel] = None

    def show(self, msg: str, kind: str = "success") -> None:
        if self._win:
            try:
                self._win.destroy()
            except Exception:
                pass
        color = {
            "success": COLORS["success"],
            "error":   COLORS["error"],
            "warning": COLORS["warning"],
        }.get(kind, COLORS["success"])
        win = tk.Toplevel(self.root)
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        win.attributes("-alpha", 0.0)
        tk.Label(
            win, text=msg, bg=color, fg=COLORS["bg"],
            font=FONTS["body"], padx=16, pady=10,
        ).pack()
        win.update_idletasks()
        rw, rh = self.root.winfo_width(), self.root.winfo_height()
        rx, ry = self.root.winfo_rootx(), self.root.winfo_rooty()
        ww, wh = win.winfo_width(), win.winfo_height()
        win.geometry(f"+{rx + rw - ww - 20}+{ry + rh - wh - 20}")
        self._win = win
        self._fade_in(win, 0.0)

    def _fade_in(self, win, alpha):
        if alpha < 0.95:
            try:
                win.attributes("-alpha", alpha + 0.08)
                self.root.after(20, self._fade_in, win, alpha + 0.08)
            except Exception:
                pass
        else:
            self.root.after(2200, self._fade_out, win, 0.95)

    def _fade_out(self, win, alpha):
        if alpha > 0.05:
            try:
                win.attributes("-alpha", alpha - 0.08)
                self.root.after(20, self._fade_out, win, alpha - 0.08)
            except Exception:
                pass
        else:
            try:
                win.destroy()
            except Exception:
                pass


# ══════════════════════════════════════════════════════════
# 主應用程式
# ══════════════════════════════════════════════════════════
class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.env = Environment.detect()
        self.current_task: Optional[DownloadTask] = None
        self.current_whisper_task: Optional[wc.WhisperTask] = None
        self.current_info: VideoInfo = VideoInfo()

        self.root.title("YouTube 字幕小幫手")
        self.root.geometry("820x760")
        self.root.minsize(720, 640)
        self.root.configure(bg=COLORS["bg"])

        self.toast = ToastNotification(root)
        self._build_ui()
        self._update_env_indicator()

    # ── UI 建構 ─────────────────────────────────────────
    def _build_ui(self) -> None:
        self._style_ttk()
        outer = tk.Frame(self.root, bg=COLORS["bg"])
        outer.pack(fill=tk.BOTH, expand=True, padx=24, pady=20)

        self._build_header(outer)
        self._build_url_row(outer)
        self._build_info_row(outer)
        self._build_options_row(outer)
        self._build_video_row(outer)
        self._build_whisper_row(outer)
        self._build_action_row(outer)
        self._build_progress_row(outer)
        self._build_preview(outer)

    def _style_ttk(self) -> None:
        s = ttk.Style()
        s.theme_use("clam")
        s.configure(
            "TCombobox",
            fieldbackground=COLORS["surface2"],
            background=COLORS["surface2"],
            foreground=COLORS["text_primary"],
            selectbackground=COLORS["accent"],
            bordercolor=COLORS["border"],
            arrowcolor=COLORS["text_secondary"],
            padding=6,
        )
        s.map("TCombobox", fieldbackground=[("readonly", COLORS["surface2"])])
        s.configure(
            "Accent.Horizontal.TProgressbar",
            troughcolor=COLORS["surface2"],
            background=COLORS["accent"],
            bordercolor=COLORS["surface2"],
            lightcolor=COLORS["accent"],
            darkcolor=COLORS["accent"],
            thickness=10,
        )
        s.configure(
            "Check.TCheckbutton",
            background=COLORS["surface"],
            foreground=COLORS["text_secondary"],
            font=FONTS["small"],
        )
        s.map(
            "Check.TCheckbutton",
            background=[("active", COLORS["surface"])],
            foreground=[("active", COLORS["accent"])],
        )

    def _build_header(self, parent: tk.Frame) -> None:
        hdr = tk.Frame(parent, bg=COLORS["bg"])
        hdr.pack(fill=tk.X, pady=(0, 16))

        left = tk.Frame(hdr, bg=COLORS["bg"])
        left.pack(side=tk.LEFT)

        title_row = tk.Frame(left, bg=COLORS["bg"])
        title_row.pack(anchor=tk.W)
        tk.Label(
            title_row, text="✦ ", bg=COLORS["bg"],
            fg=COLORS["accent"], font=FONTS["title"],
        ).pack(side=tk.LEFT)
        tk.Label(
            title_row, text="字幕小幫手",
            bg=COLORS["bg"], fg=COLORS["text_primary"],
            font=FONTS["title"],
        ).pack(side=tk.LEFT)
        tk.Label(
            title_row, text=" ✦", bg=COLORS["bg"],
            fg=COLORS["accent"], font=FONTS["title"],
        ).pack(side=tk.LEFT)

        tk.Label(
            left, text="貼上 YouTube 連結，一鍵取得字幕與影片 ♡",
            bg=COLORS["bg"], fg=COLORS["text_muted"],
            font=FONTS["small"],
        ).pack(anchor=tk.W, pady=(2, 0))

        # 環境狀態指示
        env_frame = tk.Frame(hdr, bg=COLORS["bg"])
        env_frame.pack(side=tk.RIGHT)
        self.env_dot_ytdlp = tk.Label(
            env_frame, text="●", bg=COLORS["bg"],
            fg=COLORS["text_muted"], font=FONTS["body"],
        )
        self.env_dot_ytdlp.pack(side=tk.LEFT)
        self.env_label_ytdlp = tk.Label(
            env_frame, text="yt-dlp", bg=COLORS["bg"],
            fg=COLORS["text_muted"], font=FONTS["tiny"],
        )
        self.env_label_ytdlp.pack(side=tk.LEFT, padx=(2, 10))
        self.env_dot_ffmpeg = tk.Label(
            env_frame, text="●", bg=COLORS["bg"],
            fg=COLORS["text_muted"], font=FONTS["body"],
        )
        self.env_dot_ffmpeg.pack(side=tk.LEFT)
        self.env_label_ffmpeg = tk.Label(
            env_frame, text="ffmpeg", bg=COLORS["bg"],
            fg=COLORS["text_muted"], font=FONTS["tiny"],
        )
        self.env_label_ffmpeg.pack(side=tk.LEFT, padx=(2, 10))
        self.env_dot_whisper = tk.Label(
            env_frame, text="●", bg=COLORS["bg"],
            fg=COLORS["text_muted"], font=FONTS["body"],
        )
        self.env_dot_whisper.pack(side=tk.LEFT)
        self.env_label_whisper = tk.Label(
            env_frame, text="whisper", bg=COLORS["bg"],
            fg=COLORS["text_muted"], font=FONTS["tiny"],
        )
        self.env_label_whisper.pack(side=tk.LEFT, padx=(2, 0))

    def _build_url_row(self, parent: tk.Frame) -> None:
        card = self._card(parent)
        tk.Label(
            card, text="♪  貼上影片連結", bg=COLORS["surface"],
            fg=COLORS["text_secondary"], font=FONTS["small"],
        ).pack(anchor=tk.W, pady=(0, 6))
        row = tk.Frame(card, bg=COLORS["surface"])
        row.pack(fill=tk.X)

        entry_bg = tk.Frame(row, bg=COLORS["border"], padx=1, pady=1)
        entry_bg.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.url_entry = tk.Entry(
            entry_bg, font=FONTS["body"],
            bg=COLORS["surface2"], fg=COLORS["text_primary"],
            insertbackground=COLORS["accent"],
            relief="flat", bd=0,
        )
        self.url_entry.pack(fill=tk.X, ipady=9, padx=10)
        self.url_entry.bind("<Return>", lambda _: self.preview_transcript())
        self.url_entry.bind("<KeyRelease>", self._on_url_change)
        self.url_entry.bind("<<Paste>>", lambda e: self.root.after(50, self._on_url_change, e))

        HoverButton(
            row, COLORS["surface2"], COLORS["border"],
            text="✕ 清空", fg=COLORS["text_muted"],
            font=FONTS["small"], command=self.clear_fields,
            padx=12, pady=9,
        ).pack(side=tk.LEFT, padx=(8, 0))

    def _build_info_row(self, parent: tk.Frame) -> None:
        self.info_card = tk.Frame(
            parent, bg=COLORS["surface3"], padx=14, pady=10,
            highlightbackground=COLORS["border_soft"], highlightthickness=1,
        )
        self.info_title_label = tk.Label(
            self.info_card, text="—", bg=COLORS["surface3"],
            fg=COLORS["text_primary"], font=FONTS["heading"],
            anchor="w", justify="left",
        )
        self.info_title_label.pack(fill=tk.X, anchor=tk.W)
        meta_row = tk.Frame(self.info_card, bg=COLORS["surface3"])
        meta_row.pack(fill=tk.X, pady=(2, 0))
        self.info_uploader_label = tk.Label(
            meta_row, text="", bg=COLORS["surface3"],
            fg=COLORS["text_secondary"], font=FONTS["small"],
        )
        self.info_uploader_label.pack(side=tk.LEFT)
        self.info_duration_label = tk.Label(
            meta_row, text="", bg=COLORS["surface3"],
            fg=COLORS["text_muted"], font=FONTS["small"],
        )
        self.info_duration_label.pack(side=tk.RIGHT)
        # 預設隱藏，有連結才顯示
        self._info_visible = False

    def _show_info(self, visible: bool) -> None:
        if visible and not self._info_visible:
            self.info_card.pack(fill=tk.X, pady=(0, 12), before=self.options_card)
            self._info_visible = True
        elif not visible and self._info_visible:
            self.info_card.pack_forget()
            self._info_visible = False

    def _build_options_row(self, parent: tk.Frame) -> None:
        self.options_card = self._card(parent)
        row = tk.Frame(self.options_card, bg=COLORS["surface"])
        row.pack(fill=tk.X)

        tk.Label(
            row, text="✦ 語言", bg=COLORS["surface"],
            fg=COLORS["text_secondary"], font=FONTS["small"],
        ).pack(side=tk.LEFT)
        self.lang_var = tk.StringVar(value="自動偵測")
        combo = ttk.Combobox(
            row, textvariable=self.lang_var,
            values=list(LANG_MAP.keys()), state="readonly", width=12,
        )
        combo.pack(side=tk.LEFT, padx=(8, 24))

        self.ts_var = tk.BooleanVar(value=False)
        self._toggle_btn = HoverButton(
            row, COLORS["surface2"], COLORS["border"],
            text="⏱ 時間軸  OFF", fg=COLORS["text_muted"],
            font=FONTS["small"], command=self._toggle_ts,
            padx=12, pady=6,
        )
        self._toggle_btn.pack(side=tk.LEFT)

    def _toggle_ts(self) -> None:
        self.ts_var.set(not self.ts_var.get())
        if self.ts_var.get():
            self._toggle_btn.config(text="⏱ 時間軸  ON ", fg=COLORS["accent"])
        else:
            self._toggle_btn.config(text="⏱ 時間軸  OFF", fg=COLORS["text_muted"])

    def _build_video_row(self, parent: tk.Frame) -> None:
        card = self._card(parent)
        row = tk.Frame(card, bg=COLORS["surface"])
        row.pack(fill=tk.X)

        tk.Label(
            row, text="▶  下載影片 / 音訊", bg=COLORS["surface"],
            fg=COLORS["text_secondary"], font=FONTS["small"],
        ).pack(side=tk.LEFT)

        self.vfmt_var = tk.StringVar(value="MP4 最佳畫質")
        combo = ttk.Combobox(
            row, textvariable=self.vfmt_var,
            values=list(VIDEO_FORMATS.keys()),
            state="readonly", width=18,
        )
        combo.pack(side=tk.LEFT, padx=(10, 16))

        self.also_subs_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            row, text="同時下載字幕",
            variable=self.also_subs_var,
            style="Check.TCheckbutton",
        ).pack(side=tk.LEFT, padx=(0, 8))

        self.mirror_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            row, text="⇋ 鏡像翻轉",
            variable=self.mirror_var,
            style="Check.TCheckbutton",
        ).pack(side=tk.LEFT, padx=(0, 12))

        self.video_dl_btn = HoverButton(
            row, COLORS["accent"], COLORS["accent_hover"],
            text="↓ 下載", fg="white", font=FONTS["btn"],
            command=self.download_video, padx=18, pady=6,
        )
        self.video_dl_btn.pack(side=tk.LEFT)

        self.cancel_btn = HoverButton(
            row, COLORS["btn_cancel"], COLORS["btn_cancel_hover"],
            text="✕ 取消", fg="white", font=FONTS["btn"],
            command=self.cancel_download, padx=12, pady=6,
        )
        self.cancel_btn.pack(side=tk.LEFT, padx=(8, 0))
        self.cancel_btn.pack_forget()

    def _build_whisper_row(self, parent: tk.Frame) -> None:
        card = self._card(parent)
        row = tk.Frame(card, bg=COLORS["surface"])
        row.pack(fill=tk.X)

        tk.Label(
            row, text="🎙  Whisper 語音轉錄", bg=COLORS["surface"],
            fg=COLORS["text_secondary"], font=FONTS["small"],
        ).pack(side=tk.LEFT)

        # 模型選擇（以實際已下載者為預設）
        default_model = wc.resolve_available_model() or "large-v3"
        self.whisper_model_var = tk.StringVar(value=default_model)
        combo = ttk.Combobox(
            row, textvariable=self.whisper_model_var,
            values=list(wc.WHISPER_MODELS.keys()),
            state="readonly", width=10,
        )
        combo.pack(side=tk.LEFT, padx=(10, 8))

        # 語言選擇
        self.whisper_lang_var = tk.StringVar(value="繁體中文")
        lang_combo = ttk.Combobox(
            row, textvariable=self.whisper_lang_var,
            values=list(wc.WHISPER_LANG_MAP.keys()),
            state="readonly", width=10,
        )
        lang_combo.pack(side=tk.LEFT, padx=(0, 8))

        # VAD 開關
        self.whisper_vad_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            row, text="VAD 降噪",
            variable=self.whisper_vad_var,
            style="Check.TCheckbutton",
        ).pack(side=tk.LEFT, padx=(0, 12))

        self.whisper_btn = HoverButton(
            row, COLORS["accent"], COLORS["accent_hover"],
            text="🎙 開始轉錄", fg="white", font=FONTS["btn"],
            command=self.transcribe_with_whisper, padx=18, pady=6,
        )
        self.whisper_btn.pack(side=tk.LEFT)

    def _build_action_row(self, parent: tk.Frame) -> None:
        row = tk.Frame(parent, bg=COLORS["bg"])
        row.pack(fill=tk.X, pady=(0, 10))
        self.preview_btn = HoverButton(
            row, COLORS["accent"], COLORS["accent_hover"],
            text="✦ 預覽字幕", fg="white", font=FONTS["btn"],
            command=self.preview_transcript,
            padx=0, pady=13,
        )
        self.preview_btn.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 8))

        self.download_btn = HoverButton(
            row, COLORS["btn_dl"], COLORS["btn_dl_hover"],
            text="↓ 下載字幕", fg="white", font=FONTS["btn"],
            command=self.download_transcript,
            padx=0, pady=13,
        )
        self.download_btn.pack(side=tk.LEFT, fill=tk.X, expand=True)

    def _build_progress_row(self, parent: tk.Frame) -> None:
        row = tk.Frame(parent, bg=COLORS["bg"])
        row.pack(fill=tk.X, pady=(0, 12))

        self.status_label = tk.Label(
            row, text="就緒", bg=COLORS["bg"],
            fg=COLORS["text_muted"], font=FONTS["small"],
        )
        self.status_label.pack(side=tk.LEFT)

        self.meta_label = tk.Label(
            row, text="", bg=COLORS["bg"],
            fg=COLORS["text_muted"], font=FONTS["small"],
        )
        self.meta_label.pack(side=tk.LEFT, padx=(12, 0))

        self.progress = ttk.Progressbar(
            row, mode="determinate", length=200, maximum=100,
            style="Accent.Horizontal.TProgressbar",
        )
        self.progress.pack(side=tk.RIGHT)

    def _build_preview(self, parent: tk.Frame) -> None:
        card = self._card(parent, expand=True)
        hdr = tk.Frame(card, bg=COLORS["surface"])
        hdr.pack(fill=tk.X, pady=(0, 8))
        tk.Label(
            hdr, text="♪  字幕預覽", bg=COLORS["surface"],
            fg=COLORS["text_secondary"], font=FONTS["small"],
        ).pack(side=tk.LEFT)
        self.word_count_label = tk.Label(
            hdr, text="", bg=COLORS["surface"],
            fg=COLORS["text_muted"], font=FONTS["small"],
        )
        self.word_count_label.pack(side=tk.LEFT, padx=(12, 0))
        HoverButton(
            hdr, COLORS["surface"], COLORS["surface2"],
            text="複製全部 ✦", fg=COLORS["text_secondary"],
            font=FONTS["small"], command=self.copy_all,
            padx=10, pady=3,
        ).pack(side=tk.RIGHT)

        txt_frame = tk.Frame(card, bg=COLORS["border"], padx=1, pady=1)
        txt_frame.pack(fill=tk.BOTH, expand=True)
        self.preview_text = tk.Text(
            txt_frame, font=FONTS["mono"],
            bg=COLORS["surface2"], fg=COLORS["text_primary"],
            insertbackground=COLORS["accent"],
            selectbackground=COLORS["accent"],
            selectforeground="white",
            relief="flat", bd=0, wrap=tk.WORD,
            padx=12, pady=10,
        )
        sb = ttk.Scrollbar(
            txt_frame, orient=tk.VERTICAL,
            command=self.preview_text.yview,
        )
        self.preview_text.configure(yscrollcommand=sb.set)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        self.preview_text.pack(fill=tk.BOTH, expand=True)

    def _card(self, parent: tk.Frame, expand: bool = False) -> tk.Frame:
        f = tk.Frame(
            parent, bg=COLORS["surface"], padx=16, pady=14,
            highlightbackground=COLORS["border"], highlightthickness=1,
        )
        f.pack(fill=tk.BOTH, expand=expand, pady=(0, 12))
        return f

    # ── 環境顯示 ─────────────────────────────────────────
    def _update_env_indicator(self) -> None:
        ok_color = COLORS["success"]
        bad_color = COLORS["error"]
        muted = COLORS["text_muted"]

        self.env_dot_ytdlp.config(fg=ok_color if self.env.yt_dlp else bad_color)
        self.env_label_ytdlp.config(fg=COLORS["text_secondary"] if self.env.yt_dlp else bad_color)
        self.env_dot_ffmpeg.config(fg=ok_color if self.env.ffmpeg else bad_color)
        self.env_label_ffmpeg.config(fg=COLORS["text_secondary"] if self.env.ffmpeg else bad_color)
        # whisper 是可選項目，未安裝顯示灰色（非錯誤）
        self.env_dot_whisper.config(fg=ok_color if self.env.whisper else muted)
        self.env_label_whisper.config(fg=COLORS["text_secondary"] if self.env.whisper else muted)

        if not self.env.yt_dlp:
            self.toast.show("找不到 yt-dlp，請執行：pip install yt-dlp", "error")
        elif not self.env.ffmpeg:
            self.toast.show("找不到 ffmpeg（影片下載需要），請執行：brew install ffmpeg", "warning")

    # ── URL 變動偵測 ─────────────────────────────────────
    def _on_url_change(self, _event=None) -> None:
        url = self.url_entry.get().strip()
        vid = get_video_id(url)
        if not vid:
            self._show_info(False)
            return
        # 預取影片資訊（debounce）
        if hasattr(self, "_probe_job") and self._probe_job:
            try:
                self.root.after_cancel(self._probe_job)
            except Exception:
                pass
        self._probe_job = self.root.after(400, self._probe_async, url)

    def _probe_async(self, url: str) -> None:
        def worker():
            info = probe_video_info(self.env, url)
            self.root.after(0, self._apply_info, info)
        threading.Thread(target=worker, daemon=True).start()

    def _apply_info(self, info: VideoInfo) -> None:
        self.current_info = info
        if not info.title:
            self._show_info(False)
            return
        title = info.title[:100] + ("…" if len(info.title) > 100 else "")
        self.info_title_label.config(text=f"♡  {title}")
        self.info_uploader_label.config(text=info.uploader or "—")
        self.info_duration_label.config(text=f"⏱ {format_duration(info.duration)}")
        self._show_info(True)

    # ── 業務邏輯：字幕 ─────────────────────────────────────
    def preview_transcript(self) -> None:
        url = self.url_entry.get().strip()
        vid = self._require_vid(url)
        if not vid:
            return
        threading.Thread(target=self._do_preview, args=(vid,), daemon=True).start()

    def _do_preview(self, vid: str) -> None:
        self._set_busy("正在獲取字幕…")
        try:
            items = fetch_transcript(vid, LANG_MAP.get(self.lang_var.get()))
            text = format_transcript(items, self.ts_var.get())
            self.root.after(0, self._update_preview, text, len(items))
        except RuntimeError as e:
            self.root.after(0, self.toast.show, str(e), "error")
            self.root.after(0, self._set_idle, "預覽失敗", COLORS["error"])
        finally:
            self.root.after(0, self._enable_buttons)

    def _update_preview(self, text: str, count: int) -> None:
        self.preview_text.delete("1.0", tk.END)
        self.preview_text.insert("1.0", text)
        chars = len(text)
        self.word_count_label.config(text=f"{count} 條字幕 · {chars:,} 字元")
        self._set_idle(f"預覽完成，共 {count} 條字幕", COLORS["success"])
        self.toast.show(f"成功載入 {count} 條字幕", "success")

    def download_transcript(self) -> None:
        url = self.url_entry.get().strip()
        vid = self._require_vid(url)
        if not vid:
            return
        today = datetime.now().strftime("%Y-%m-%d")
        default_name = safe_filename(self.current_info.title) if self.current_info.title else vid
        path = filedialog.asksaveasfilename(
            defaultextension=".txt",
            initialfile=f"{today}-{default_name}.txt",
            filetypes=[("文字檔案", "*.txt"), ("所有檔案", "*.*")],
        )
        if not path:
            return
        threading.Thread(target=self._do_download_transcript, args=(vid, path), daemon=True).start()

    def _do_download_transcript(self, vid: str, path: str) -> None:
        self._set_busy("正在下載字幕…")
        try:
            items = fetch_transcript(vid, LANG_MAP.get(self.lang_var.get()))
            text = format_transcript(items, self.ts_var.get())
            with open(path, "w", encoding="utf-8") as f:
                f.write(text)
            self.root.after(0, self._set_idle, "字幕下載完成", COLORS["success"])
            self.root.after(0, self.toast.show, f"已儲存至 {os.path.basename(path)}", "success")
        except RuntimeError as e:
            self.root.after(0, self.toast.show, str(e), "error")
            self.root.after(0, self._set_idle, "下載失敗", COLORS["error"])
        finally:
            self.root.after(0, self._enable_buttons)

    # ── 業務邏輯：影片 ─────────────────────────────────────
    def download_video(self) -> None:
        url = self.url_entry.get().strip()
        vid = self._require_vid(url)
        if not vid:
            return
        fmt_name = self.vfmt_var.get()
        fmt = VIDEO_FORMATS[fmt_name]
        mirror = self.mirror_var.get() and fmt["kind"] == "video"

        if fmt["kind"] == "video" and not self.env.ffmpeg:
            self.toast.show("下載影片需要 ffmpeg，請先安裝：brew install ffmpeg", "error")
            return
        if mirror and not self.env.ffmpeg:
            self.toast.show("鏡像功能需要 ffmpeg", "error")
            return
        if not self.env.yt_dlp:
            self.toast.show("找不到 yt-dlp，請先安裝：pip install yt-dlp", "error")
            return

        folder = filedialog.askdirectory(title="選擇儲存資料夾")
        if not folder:
            return

        prep_msg = f"準備下載 {fmt_name}"
        if mirror:
            prep_msg += "（鏡像翻轉）"
        self._set_progress(0, prep_msg + "…")
        self._toggle_cancel(True)
        self._disable_buttons()

        self.current_task = DownloadTask(
            env=self.env,
            url=url,
            fmt=fmt,
            folder=folder,
            on_progress=self._on_download_progress,
            on_done=self._on_download_done,
            mirror=mirror,
        )
        self.current_task.start()

        # 同時下載字幕
        if self.also_subs_var.get():
            threading.Thread(
                target=self._do_side_subtitle,
                args=(vid, folder),
                daemon=True,
            ).start()

    def _do_side_subtitle(self, vid: str, folder: str) -> None:
        try:
            items = fetch_transcript(vid, LANG_MAP.get(self.lang_var.get()))
            text = format_transcript(items, self.ts_var.get())
            name = safe_filename(self.current_info.title) if self.current_info.title else vid
            path = os.path.join(folder, f"{name}.txt")
            with open(path, "w", encoding="utf-8") as f:
                f.write(text)
            self.root.after(0, self.toast.show, "字幕已同步儲存", "success")
        except RuntimeError as e:
            self.root.after(0, self.toast.show, f"字幕失敗：{e}", "warning")

    def cancel_download(self) -> None:
        if self.current_task:
            self.current_task.cancel()
            self._set_idle("取消中…", COLORS["warning"])
        if self.current_whisper_task:
            self.current_whisper_task.cancel()
            self._set_idle("取消中…", COLORS["warning"])

    # ── 業務邏輯：Whisper 語音轉錄 ─────────────────────────
    def transcribe_with_whisper(self) -> None:
        url = self.url_entry.get().strip()
        vid = self._require_vid(url)
        if not vid:
            return
        if not self.env.yt_dlp:
            self.toast.show("找不到 yt-dlp，請安裝：pip install yt-dlp", "error")
            return
        if not self.env.ffmpeg:
            self.toast.show("找不到 ffmpeg，請安裝：brew install ffmpeg", "error")
            return
        if not self.env.whisper:
            self.toast.show(
                "找不到 whisper-cli，請安裝：brew install whisper-cpp",
                "error",
            )
            return

        model_key = self.whisper_model_var.get()
        m_path = wc.model_path(model_key)
        if not m_path.exists():
            size_mb = wc.WHISPER_MODELS[model_key]["size_mb"]
            self.toast.show(
                f"模型 {model_key}（{size_mb}MB）尚未下載，將背景下載",
                "warning",
            )
            self._download_whisper_model(model_key, then_transcribe=True)
            return

        self._start_whisper_task(url, model_key)

    def _download_whisper_model(self, model_key: str, then_transcribe: bool) -> None:
        meta = wc.WHISPER_MODELS[model_key]
        dst = wc.model_path(model_key)

        self._set_progress(0, f"下載模型 {model_key}…")
        self._disable_buttons()

        def worker():
            try:
                def on_p(pct: float):
                    self.root.after(
                        0, self._set_progress, pct, f"下載模型 {model_key} {pct:.1f}%",
                    )
                wc.download_model(meta["url"], dst, on_progress=on_p)
                self.root.after(
                    0, self.toast.show, f"模型 {model_key} 下載完成", "success",
                )
                self.root.after(0, self._set_idle, "模型下載完成", COLORS["success"])
                self.root.after(0, self._enable_buttons)
                if then_transcribe:
                    self.root.after(200, self._start_whisper_task,
                                    self.url_entry.get().strip(), model_key)
            except Exception as e:
                self.root.after(0, self.toast.show, f"模型下載失敗：{e}", "error")
                self.root.after(0, self._set_idle, "模型下載失敗", COLORS["error"])
                self.root.after(0, self._enable_buttons)

        threading.Thread(target=worker, daemon=True).start()

    def _start_whisper_task(self, url: str, model_key: str) -> None:
        if not url:
            return
        lang_label = self.whisper_lang_var.get()
        lang = wc.WHISPER_LANG_MAP.get(lang_label, "auto")
        prompt = wc.WHISPER_DEFAULT_PROMPT_ZH if lang == "zh" else None

        vad_file = wc.vad_path() if self.whisper_vad_var.get() else None
        if vad_file and not vad_file.exists():
            # VAD 模型很小（1MB），自動下載
            try:
                vad_file.parent.mkdir(parents=True, exist_ok=True)
                wc.download_model(wc.WHISPER_VAD["url"], vad_file)
            except Exception as e:
                self.toast.show(f"VAD 模型下載失敗，改用無 VAD 模式：{e}", "warning")
                vad_file = None

        workdir = Path.home() / ".whisper_tmp" / datetime.now().strftime("%Y%m%d_%H%M%S")
        assert self.env.whisper and self.env.yt_dlp and self.env.ffmpeg
        task = wc.WhisperTask(
            whisper_bin=self.env.whisper,
            ytdlp_bin=self.env.yt_dlp,
            ffmpeg_bin=self.env.ffmpeg,
            model_file=str(wc.model_path(model_key)),
            url=url,
            workdir=workdir,
            on_progress=self._on_whisper_progress,
            on_done=self._on_whisper_done,
            language=lang,
            vad_model=str(vad_file) if vad_file else None,
            prompt=prompt,
            env=self.env.subprocess_env(),
        )
        self.current_whisper_task = task
        self._toggle_cancel(True)
        self._disable_buttons()
        self._set_progress(0, f"Whisper 轉錄中（{model_key}）…")
        task.start()

    def _on_whisper_progress(self, state: wc.WhisperProgress) -> None:
        def apply():
            self.progress.config(value=state.percent)
            self.status_label.config(
                text=state.message or state.phase, fg=COLORS["accent"],
            )
            # 即時預覽
            if state.text_preview:
                self.preview_text.delete("1.0", tk.END)
                self.preview_text.insert("1.0", state.text_preview)
                self.preview_text.see(tk.END)
        self.root.after(0, apply)

    def _on_whisper_done(
        self, success: bool, message: str, text: Optional[str],
    ) -> None:
        def apply():
            self._toggle_cancel(False)
            self._enable_buttons()
            self.current_whisper_task = None
            if success and text:
                self.preview_text.delete("1.0", tk.END)
                self.preview_text.insert("1.0", text)
                chars = len(text)
                lines = text.count("\n") + 1
                self.word_count_label.config(
                    text=f"{lines} 行 · {chars:,} 字元（Whisper）",
                )
                self.progress.config(value=100)
                self._set_idle(message, COLORS["success"])
                self.toast.show("轉錄完成，可按「下載字幕」另存", "success")
            else:
                self.progress.config(value=0)
                self._set_idle(message, COLORS["error"])
                self.toast.show(message, "error")
            self.meta_label.config(text="")
        self.root.after(0, apply)

    def _on_download_progress(self, state: DownloadProgress) -> None:
        def apply():
            self.progress.config(value=state.percent)
            phase_text = {
                "download": "下載中",
                "merge":    "合併中",
                "mirror":   "鏡像中",
                "finished": "完成",
            }.get(state.phase, "處理中")
            self.status_label.config(text=f"{phase_text} {state.percent:.1f}%", fg=COLORS["accent"])
            meta_parts = []
            if state.speed:
                meta_parts.append(state.speed)
            if state.eta:
                meta_parts.append(f"ETA {state.eta}")
            if state.filename:
                meta_parts.append(state.filename[:40])
            self.meta_label.config(text=" · ".join(meta_parts))
        self.root.after(0, apply)

    def _on_download_done(self, success: bool, msg: str) -> None:
        def apply():
            self._toggle_cancel(False)
            self._enable_buttons()
            self.current_task = None
            if success:
                self.progress.config(value=100)
                self._set_idle(msg, COLORS["success"])
                self.toast.show(msg, "success")
            else:
                self.progress.config(value=0)
                self._set_idle(msg, COLORS["error"])
                self.toast.show(msg, "error")
            self.meta_label.config(text="")
        self.root.after(0, apply)

    # ── 其他 ─────────────────────────────────────────────
    def copy_all(self) -> None:
        content = self.preview_text.get("1.0", tk.END).strip()
        if not content:
            self.toast.show("預覽區沒有內容可複製", "warning")
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(content)
        self.toast.show("已複製到剪貼簿", "success")

    def clear_fields(self) -> None:
        self.url_entry.delete(0, tk.END)
        self.preview_text.delete("1.0", tk.END)
        self.word_count_label.config(text="")
        self._show_info(False)
        self.current_info = VideoInfo()
        self._set_idle("就緒", COLORS["text_muted"])
        self.progress.config(value=0)
        self.meta_label.config(text="")

    def _require_vid(self, url: str) -> Optional[str]:
        if not url:
            self.toast.show("請先輸入 YouTube 影片連結", "warning")
            return None
        vid = get_video_id(url)
        if not vid:
            self.toast.show("無效的 YouTube 影片連結", "error")
            return None
        return vid

    # ── 狀態輔助 ─────────────────────────────────────────
    def _set_busy(self, msg: str) -> None:
        self.status_label.config(text=msg, fg=COLORS["accent"])
        self.progress.config(mode="indeterminate")
        self.progress.start(12)
        self._disable_buttons()

    def _set_progress(self, pct: float, msg: str) -> None:
        self.progress.stop()
        self.progress.config(mode="determinate", value=pct)
        self.status_label.config(text=msg, fg=COLORS["accent"])

    def _set_idle(self, msg: str, color: str) -> None:
        self.progress.stop()
        self.progress.config(mode="determinate")
        self.status_label.config(text=msg, fg=color)

    def _toggle_cancel(self, show: bool) -> None:
        if show:
            self.cancel_btn.pack(side=tk.LEFT, padx=(8, 0))
        else:
            self.cancel_btn.pack_forget()

    def _disable_buttons(self) -> None:
        for b in (self.preview_btn, self.download_btn, self.video_dl_btn, self.whisper_btn):
            b.config(state=tk.DISABLED)

    def _enable_buttons(self) -> None:
        for b in (self.preview_btn, self.download_btn, self.video_dl_btn, self.whisper_btn):
            b.config(state=tk.NORMAL)


# ══════════════════════════════════════════════════════════
# 入口
# ══════════════════════════════════════════════════════════
if __name__ == "__main__":
    root = tk.Tk()
    App(root)
    root.mainloop()

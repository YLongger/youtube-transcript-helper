"""
Whisper 轉錄核心模組 — 字幕小幫手的離線 ASR 整合
========================================================

本模組將「yt-dlp 下載音訊 → ffmpeg 轉 16kHz wav → whisper-cli 轉錄」
整條管線封裝起來，並刻意將可測試的純函式（指令組裝、進度解析、路徑
解析）與須執行 subprocess/threading 的 orchestration 分離，以便於
pytest 測試。

使用情境：YouTube 影片沒有字幕（或字幕品質差）時，下載音訊本地轉錄。
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import threading
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

# ══════════════════════════════════════════════════════════
# 常數：模型與路徑
# ══════════════════════════════════════════════════════════
WHISPER_MODEL_DIR = Path.home() / "whisper-models"

# 模型大小越大，越準確但越慢；檔案大小為近似值
WHISPER_MODELS: dict[str, dict] = {
    "tiny": {
        "file": "ggml-tiny.bin",
        "size_mb": 75,
        "url": "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-tiny.bin",
        "desc": "最快，準確度最低（適合短影片粗略轉錄）",
    },
    "base": {
        "file": "ggml-base.bin",
        "size_mb": 142,
        "url": "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-base.bin",
        "desc": "快速，一般口語可用",
    },
    "small": {
        "file": "ggml-small.bin",
        "size_mb": 466,
        "url": "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-small.bin",
        "desc": "中等品質，推薦日常使用",
    },
    "medium": {
        "file": "ggml-medium.bin",
        "size_mb": 1462,
        "url": "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-medium.bin",
        "desc": "高品質，多語言表現佳",
    },
    "large-v3": {
        "file": "ggml-large-v3.bin",
        "size_mb": 3094,
        "url": "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-large-v3.bin",
        "desc": "最佳品質，支援 VAD 下幾乎無幻覺",
    },
}
MODEL_PRIORITY = ["large-v3", "medium", "small", "base", "tiny"]

WHISPER_VAD: dict = {
    "file": "ggml-silero-v5.1.2.bin",
    "size_mb": 1,
    "url": "https://huggingface.co/ggml-org/whisper-vad/resolve/main/ggml-silero-v5.1.2.bin",
}

WHISPER_LANG_MAP: dict[str, str] = {
    "自動偵測": "auto",
    "繁體中文": "zh",
    "英文": "en",
    "日文": "ja",
    "韓文": "ko",
}

WHISPER_DEFAULT_PROMPT_ZH = (
    "以下為繁體中文。"
)

WHISPER_EXTRA_BIN_DIRS = [
    "/opt/homebrew/bin",
    "/usr/local/bin",
]


# ══════════════════════════════════════════════════════════
# 資料型別
# ══════════════════════════════════════════════════════════
@dataclass
class WhisperProgress:
    """進度狀態：phase / percent / message / text_preview。"""
    phase: str = ""          # downloading / converting / transcribing / finished
    percent: float = 0.0
    message: str = ""
    text_preview: str = ""   # 已轉錄的片段（截尾保留 ~1500 字）


# ══════════════════════════════════════════════════════════
# 純函式：路徑與偵測
# ══════════════════════════════════════════════════════════
def find_whisper_bin(extra_dirs: Optional[list[str]] = None) -> Optional[str]:
    """尋找 whisper-cli（或同效 whisper/main）執行檔。"""
    candidates = ["whisper-cli", "whisper", "main"]
    for name in candidates:
        found = shutil.which(name)
        if found:
            return found
    for d in extra_dirs or WHISPER_EXTRA_BIN_DIRS:
        for name in candidates:
            p = Path(d) / name
            if p.exists():
                return str(p)
    return None


def model_path(model_key: str, base_dir: Optional[Path] = None) -> Path:
    """取得某模型應存放的絕對路徑。未知 key 會 KeyError（呼叫端需先驗證）。"""
    base = base_dir or WHISPER_MODEL_DIR
    return base / WHISPER_MODELS[model_key]["file"]


def vad_path(base_dir: Optional[Path] = None) -> Path:
    base = base_dir or WHISPER_MODEL_DIR
    return base / WHISPER_VAD["file"]


def resolve_available_model(base_dir: Optional[Path] = None) -> Optional[str]:
    """回傳已下載的模型中品質最佳者；全部不存在則回傳 None。"""
    for key in MODEL_PRIORITY:
        if model_path(key, base_dir).exists():
            return key
    return None


def safe_stem(name: str, max_len: int = 80) -> str:
    """將任意字串轉為安全的檔名主幹。"""
    name = re.sub(r'[\\/:*?"<>|\r\n\t]+', "_", name)
    name = name.strip(" .")
    return name[:max_len] if name else "transcript"


# ══════════════════════════════════════════════════════════
# 純函式：指令組裝（便於單元測試）
# ══════════════════════════════════════════════════════════
def build_whisper_cmd(
    whisper_bin: str,
    model_file: str,
    wav_file: str,
    out_base: str,
    language: str = "zh",
    vad_model: Optional[str] = None,
    prompt: Optional[str] = None,
) -> list[str]:
    """組裝 whisper-cli 指令；若 vad_model 存在則啟用 VAD。"""
    cmd: list[str] = [
        whisper_bin,
        "-m", model_file,
        "-f", wav_file,
        "-l", language,
        "--entropy-thold", "2.8",
        "--logprob-thold", "-0.7",
        "--temperature", "0",
        "-otxt", "-ovtt",
        "-of", out_base,
        "--print-progress",
    ]
    if vad_model and Path(vad_model).exists():
        cmd += [
            "--vad",
            "--vad-model", vad_model,
            "--vad-threshold", "0.5",
            "--vad-min-silence-duration-ms", "500",
            "--vad-speech-pad-ms", "100",
        ]
    if prompt:
        cmd += ["--prompt", prompt]
    return cmd


def build_ffmpeg_to_wav_cmd(ffmpeg_bin: str, src: str, dst: str) -> list[str]:
    """m4a/其他 → 16kHz mono pcm_s16le wav（Whisper 最佳格式）。"""
    return [
        ffmpeg_bin,
        "-nostdin", "-loglevel", "error", "-y",
        "-i", src,
        "-ac", "1",
        "-ar", "16000",
        "-c:a", "pcm_s16le",
        dst,
    ]


def build_ytdlp_audio_cmd(
    ytdlp_bin: str,
    url: str,
    out_template: str,
    ffmpeg_bin: Optional[str] = None,
) -> list[str]:
    """下載影片音訊為 m4a。"""
    cmd: list[str] = [
        ytdlp_bin,
        "-x",
        "--audio-format", "m4a",
        "--audio-quality", "0",
        "-o", out_template,
        "--no-warnings",
        "--newline",
        "--print", "after_move:filepath",
    ]
    if ffmpeg_bin:
        cmd += ["--ffmpeg-location", ffmpeg_bin]
    cmd.append(url)
    return cmd


# ══════════════════════════════════════════════════════════
# 純類別：進度解析器
# ══════════════════════════════════════════════════════════
class WhisperProgressParser:
    """解析 whisper-cli 的 stdout 行，更新 WhisperProgress。"""

    RX_PROGRESS = re.compile(
        r"whisper_print_progress_callback:\s*progress\s*=\s*(\d+)\s*%"
    )
    RX_TIMELINE = re.compile(
        r"^\[\d{2}:\d{2}:\d{2}\.\d+\s*-->\s*\d{2}:\d{2}:\d{2}\.\d+\]\s+(.+)$"
    )

    def parse(self, line: str, state: WhisperProgress) -> WhisperProgress:
        line = line.rstrip()
        if not line:
            return state
        m = self.RX_PROGRESS.search(line)
        if m:
            state.percent = float(m.group(1))
            state.phase = "transcribing"
            return state
        m = self.RX_TIMELINE.match(line)
        if m:
            text = m.group(1).strip()
            combined = (state.text_preview + "\n" + text) if state.text_preview else text
            # 保留最後 ~1500 字元即可
            state.text_preview = combined[-1500:]
        return state


def parse_ytdlp_percent(line: str) -> Optional[float]:
    """從 yt-dlp 的一行輸出抓出下載百分比，找不到回傳 None。"""
    m = re.search(r"\[download\]\s+([\d.]+)%", line)
    if not m:
        return None
    try:
        return float(m.group(1))
    except ValueError:
        return None


# ══════════════════════════════════════════════════════════
# 模型下載（可切回呼，適合在背景執行）
# ══════════════════════════════════════════════════════════
def download_model(  # pragma: no cover
    url: str,
    dst: Path,
    on_progress: Optional[Callable[[float], None]] = None,
    chunk_size: int = 1 << 20,
    timeout: int = 60,
) -> None:
    """下載模型到 dst（覆蓋）。已存在時不重複下載。"""
    if dst.exists():
        if on_progress:
            on_progress(100.0)
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_suffix(dst.suffix + ".part")
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        total = int(resp.headers.get("Content-Length") or 0)
        written = 0
        with open(tmp, "wb") as out:
            while True:
                chunk = resp.read(chunk_size)
                if not chunk:
                    break
                out.write(chunk)
                written += len(chunk)
                if on_progress and total > 0:
                    on_progress(written / total * 100)
    tmp.rename(dst)


# ══════════════════════════════════════════════════════════
# 任務：下載 → 轉檔 → Whisper 轉錄
# ══════════════════════════════════════════════════════════
class WhisperTask:  # pragma: no cover
    """
    完整管線：
      1. yt-dlp 下載音訊 m4a
      2. ffmpeg 轉 16kHz mono wav
      3. whisper-cli 轉錄，產生 .txt / .vtt

    進度百分比切成三段：
      - 下載階段   0–30%
      - 轉檔階段   30–35%
      - 轉錄階段   35–100%

    on_done(success: bool, message: str, text: Optional[str])
      text 為轉錄結果純文字，失敗時為 None。
    """

    def __init__(
        self,
        whisper_bin: str,
        ytdlp_bin: str,
        ffmpeg_bin: str,
        model_file: str,
        url: str,
        workdir: Path,
        on_progress: Callable[[WhisperProgress], None],
        on_done: Callable[[bool, str, Optional[str]], None],
        language: str = "zh",
        vad_model: Optional[str] = None,
        prompt: Optional[str] = None,
        env: Optional[dict] = None,
        keep_wav: bool = False,
    ):
        self.whisper_bin = whisper_bin
        self.ytdlp_bin = ytdlp_bin
        self.ffmpeg_bin = ffmpeg_bin
        self.model_file = model_file
        self.url = url
        self.workdir = Path(workdir)
        self.on_progress = on_progress
        self.on_done = on_done
        self.language = language
        self.vad_model = vad_model
        self.prompt = prompt
        self.env = env if env is not None else os.environ.copy()
        self.keep_wav = keep_wav
        self._proc: Optional[subprocess.Popen] = None
        self._cancelled = False
        self._parser = WhisperProgressParser()

    def start(self) -> None:
        threading.Thread(target=self._run, daemon=True).start()

    def cancel(self) -> None:
        self._cancelled = True
        if self._proc and self._proc.poll() is None:
            try:
                self._proc.terminate()
            except Exception:
                pass

    def _emit(self, state: WhisperProgress) -> None:
        try:
            self.on_progress(state)
        except Exception:
            pass

    def _run(self) -> None:
        state = WhisperProgress(phase="downloading", percent=0.0, message="下載音訊…")
        self._emit(state)
        self.workdir.mkdir(parents=True, exist_ok=True)

        audio_m4a: Optional[Path] = None
        audio_wav: Optional[Path] = None

        try:
            # ── 1. 下載 m4a 音訊 ────────────────────────────
            m4a_tpl = str(self.workdir / "audio.%(ext)s")
            cmd = build_ytdlp_audio_cmd(
                self.ytdlp_bin, self.url, m4a_tpl, self.ffmpeg_bin,
            )
            audio_path = ""
            self._proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                env=self.env,
            )
            assert self._proc.stdout is not None
            for line in self._proc.stdout:
                if self._cancelled:
                    break
                stripped = line.strip()
                if stripped and os.path.isabs(stripped) and os.path.exists(stripped):
                    audio_path = stripped
                pct = parse_ytdlp_percent(line)
                if pct is not None:
                    state.percent = pct * 0.3
                    state.message = f"下載音訊 {pct:.1f}%"
                    self._emit(state)
            code = self._proc.wait()
            if self._cancelled:
                self.on_done(False, "已取消", None)
                return
            if code != 0 or not audio_path:
                self.on_done(False, f"音訊下載失敗（exit {code}）", None)
                return
            audio_m4a = Path(audio_path)

            # ── 2. 轉 16kHz wav ─────────────────────────────
            state.phase = "converting"
            state.percent = 30.0
            state.message = "轉檔 16kHz WAV…"
            self._emit(state)
            audio_wav = audio_m4a.with_suffix(".wav")
            r = subprocess.run(
                build_ffmpeg_to_wav_cmd(
                    self.ffmpeg_bin, str(audio_m4a), str(audio_wav),
                ),
                capture_output=True,
                text=True,
                env=self.env,
            )
            if self._cancelled:
                self.on_done(False, "已取消", None)
                return
            if r.returncode != 0:
                self.on_done(False, f"音訊轉檔失敗：{r.stderr[:120]}", None)
                return

            # ── 3. Whisper 轉錄 ────────────────────────────
            state.phase = "transcribing"
            state.percent = 35.0
            state.message = "Whisper 轉錄中…"
            self._emit(state)
            out_base = str(self.workdir / "transcript")
            w_cmd = build_whisper_cmd(
                self.whisper_bin,
                self.model_file,
                str(audio_wav),
                out_base,
                language=self.language,
                vad_model=self.vad_model,
                prompt=self.prompt,
            )
            self._proc = subprocess.Popen(
                w_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                env=self.env,
            )
            assert self._proc.stdout is not None
            last_raw_pct = 0.0
            for line in self._proc.stdout:
                if self._cancelled:
                    break
                state = self._parser.parse(line, state)
                if state.phase == "transcribing" and state.percent != last_raw_pct:
                    last_raw_pct = state.percent
                    mapped = 35.0 + (state.percent * 0.65)
                    state.percent = mapped
                    state.message = f"Whisper 轉錄 {int(mapped)}%"
                self._emit(state)
            code = self._proc.wait()
            if self._cancelled:
                self.on_done(False, "已取消", None)
                return
            if code != 0:
                self.on_done(False, f"Whisper 轉錄失敗（exit {code}）", None)
                return

            txt_file = Path(out_base + ".txt")
            if not txt_file.exists():
                self.on_done(False, "找不到轉錄結果檔", None)
                return
            text = txt_file.read_text(encoding="utf-8")
            state.phase = "finished"
            state.percent = 100.0
            state.message = "完成"
            self._emit(state)
            self.on_done(True, "轉錄完成", text)
        except FileNotFoundError as e:
            self.on_done(False, f"找不到執行檔：{e}", None)
        except Exception as e:
            self.on_done(False, f"錯誤：{e}", None)
        finally:
            # 清理暫存（wav 可選保留）
            if audio_m4a and audio_m4a.exists():
                try:
                    audio_m4a.unlink()
                except Exception:
                    pass
            if audio_wav and audio_wav.exists() and not self.keep_wav:
                try:
                    audio_wav.unlink()
                except Exception:
                    pass

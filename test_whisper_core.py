"""whisper_core 單元測試

聚焦純函式與解析器，subprocess 相關 orchestration（WhisperTask）以
pragma: no cover 排除，由手動整合驗證。覆蓋率目標 ≥ 80%。
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

import whisper_core as wc
from whisper_core import (
    MODEL_PRIORITY,
    WHISPER_LANG_MAP,
    WHISPER_MODELS,
    WHISPER_VAD,
    WhisperProgress,
    WhisperProgressParser,
    build_ffmpeg_to_wav_cmd,
    build_whisper_cmd,
    build_ytdlp_audio_cmd,
    find_whisper_bin,
    model_path,
    parse_ytdlp_percent,
    resolve_available_model,
    safe_stem,
    vad_path,
)


# ══════════════════════════════════════════════════════════
# 常數驗證
# ══════════════════════════════════════════════════════════
class TestModelConstants:
    def test_all_models_have_required_fields(self):
        for key, meta in WHISPER_MODELS.items():
            assert set(meta.keys()) >= {"file", "size_mb", "url", "desc"}
            assert meta["file"].endswith(".bin")
            assert meta["url"].startswith("https://")
            assert meta["size_mb"] > 0

    def test_vad_has_url_and_file(self):
        assert WHISPER_VAD["file"].endswith(".bin")
        assert WHISPER_VAD["url"].startswith("https://")

    def test_model_priority_covers_all_models(self):
        assert set(MODEL_PRIORITY) == set(WHISPER_MODELS.keys())

    def test_model_priority_starts_with_largest_quality(self):
        assert MODEL_PRIORITY[0] == "large-v3"

    def test_lang_map_contains_zh(self):
        assert WHISPER_LANG_MAP["繁體中文"] == "zh"


# ══════════════════════════════════════════════════════════
# 路徑函式
# ══════════════════════════════════════════════════════════
class TestModelPath:
    def test_model_path_returns_expected_file_under_base_dir(self, tmp_path):
        p = model_path("large-v3", base_dir=tmp_path)
        assert p == tmp_path / "ggml-large-v3.bin"

    def test_model_path_uses_default_dir_when_no_base_given(self):
        p = model_path("tiny")
        assert p.name == "ggml-tiny.bin"
        assert p.parent == wc.WHISPER_MODEL_DIR

    def test_model_path_raises_keyerror_for_unknown_model(self, tmp_path):
        with pytest.raises(KeyError):
            model_path("nonexistent-model", base_dir=tmp_path)

    def test_vad_path_returns_expected_file(self, tmp_path):
        p = vad_path(base_dir=tmp_path)
        assert p.name == "ggml-silero-v5.1.2.bin"

    def test_vad_path_uses_default_dir_when_no_base_given(self):
        p = vad_path()
        assert p.parent == wc.WHISPER_MODEL_DIR


class TestResolveAvailableModel:
    def test_resolve_available_model_picks_highest_quality_present(self, tmp_path):
        (tmp_path / "ggml-tiny.bin").write_bytes(b"x")
        (tmp_path / "ggml-large-v3.bin").write_bytes(b"x")
        assert resolve_available_model(base_dir=tmp_path) == "large-v3"

    def test_resolve_available_model_returns_none_when_empty(self, tmp_path):
        assert resolve_available_model(base_dir=tmp_path) is None

    def test_resolve_available_model_falls_back_to_smaller(self, tmp_path):
        (tmp_path / "ggml-small.bin").write_bytes(b"x")
        assert resolve_available_model(base_dir=tmp_path) == "small"

    def test_resolve_available_model_ignores_unrelated_files(self, tmp_path):
        (tmp_path / "random.bin").write_bytes(b"x")
        assert resolve_available_model(base_dir=tmp_path) is None


# ══════════════════════════════════════════════════════════
# 檔名工具
# ══════════════════════════════════════════════════════════
class TestSafeStem:
    def test_safe_stem_replaces_invalid_chars_with_underscore(self):
        assert safe_stem("a/b\\c:d*e?f") == "a_b_c_d_e_f"

    def test_safe_stem_truncates_to_max_len(self):
        assert len(safe_stem("x" * 200, max_len=80)) == 80

    def test_safe_stem_returns_default_for_empty_input(self):
        assert safe_stem("") == "transcript"

    def test_safe_stem_strips_leading_trailing_dots_and_spaces(self):
        assert safe_stem("  .hello.  ") == "hello"


# ══════════════════════════════════════════════════════════
# 指令組裝
# ══════════════════════════════════════════════════════════
class TestBuildWhisperCmd:
    def test_build_whisper_cmd_minimal_contains_required_flags(self):
        cmd = build_whisper_cmd(
            "whisper-cli", "/m.bin", "/a.wav", "/out", language="zh",
        )
        assert cmd[0] == "whisper-cli"
        assert cmd[cmd.index("-m") + 1] == "/m.bin"
        assert cmd[cmd.index("-f") + 1] == "/a.wav"
        assert cmd[cmd.index("-l") + 1] == "zh"
        assert "-otxt" in cmd and "-ovtt" in cmd
        assert cmd[cmd.index("-of") + 1] == "/out"
        assert "--print-progress" in cmd

    def test_build_whisper_cmd_includes_anti_hallucination_flags(self):
        cmd = build_whisper_cmd("w", "/m", "/a", "/o")
        assert "--entropy-thold" in cmd
        assert "--logprob-thold" in cmd
        assert "--temperature" in cmd

    def test_build_whisper_cmd_with_prompt_includes_prompt(self):
        cmd = build_whisper_cmd(
            "w", "/m", "/a", "/o", prompt="以下為繁體中文",
        )
        assert cmd[cmd.index("--prompt") + 1] == "以下為繁體中文"

    def test_build_whisper_cmd_without_prompt_excludes_prompt_flag(self):
        cmd = build_whisper_cmd("w", "/m", "/a", "/o")
        assert "--prompt" not in cmd

    def test_build_whisper_cmd_with_existing_vad_model_enables_vad(self, tmp_path):
        vad = tmp_path / "vad.bin"
        vad.write_bytes(b"x")
        cmd = build_whisper_cmd(
            "w", "/m", "/a", "/o", vad_model=str(vad),
        )
        assert "--vad" in cmd
        assert cmd[cmd.index("--vad-model") + 1] == str(vad)
        assert "--vad-threshold" in cmd
        assert "--vad-min-silence-duration-ms" in cmd

    def test_build_whisper_cmd_with_missing_vad_model_skips_vad(self, tmp_path):
        cmd = build_whisper_cmd(
            "w", "/m", "/a", "/o", vad_model=str(tmp_path / "missing.bin"),
        )
        assert "--vad" not in cmd

    def test_build_whisper_cmd_with_none_vad_model_skips_vad(self):
        cmd = build_whisper_cmd("w", "/m", "/a", "/o", vad_model=None)
        assert "--vad" not in cmd


class TestBuildFfmpegCmd:
    def test_build_ffmpeg_to_wav_cmd_uses_16khz_mono_pcm(self):
        cmd = build_ffmpeg_to_wav_cmd("ffmpeg", "/in.m4a", "/out.wav")
        assert cmd[cmd.index("-ar") + 1] == "16000"
        assert cmd[cmd.index("-ac") + 1] == "1"
        assert cmd[cmd.index("-c:a") + 1] == "pcm_s16le"

    def test_build_ffmpeg_to_wav_cmd_has_yes_overwrite(self):
        cmd = build_ffmpeg_to_wav_cmd("ffmpeg", "/in", "/out")
        assert "-y" in cmd


class TestBuildYtdlpCmd:
    def test_build_ytdlp_audio_cmd_requests_m4a_best_quality(self):
        cmd = build_ytdlp_audio_cmd("yt-dlp", "https://x", "/tmp/%(ext)s")
        assert cmd[cmd.index("--audio-format") + 1] == "m4a"
        assert cmd[cmd.index("--audio-quality") + 1] == "0"
        assert "-x" in cmd

    def test_build_ytdlp_audio_cmd_appends_url_last(self):
        cmd = build_ytdlp_audio_cmd("yt-dlp", "https://y", "/tmp/%(ext)s")
        assert cmd[-1] == "https://y"

    def test_build_ytdlp_audio_cmd_with_ffmpeg_includes_location(self):
        cmd = build_ytdlp_audio_cmd(
            "yt-dlp", "https://x", "/tmp/%(ext)s", ffmpeg_bin="/bin/ffmpeg",
        )
        assert cmd[cmd.index("--ffmpeg-location") + 1] == "/bin/ffmpeg"

    def test_build_ytdlp_audio_cmd_without_ffmpeg_excludes_location(self):
        cmd = build_ytdlp_audio_cmd("yt-dlp", "https://x", "/tmp/%(ext)s")
        assert "--ffmpeg-location" not in cmd


# ══════════════════════════════════════════════════════════
# 進度解析器
# ══════════════════════════════════════════════════════════
class TestWhisperProgressParser:
    def test_parse_progress_line_updates_percent_and_phase(self):
        p = WhisperProgressParser()
        state = WhisperProgress()
        state = p.parse(
            "whisper_print_progress_callback: progress = 42%", state,
        )
        assert state.percent == 42.0
        assert state.phase == "transcribing"

    def test_parse_progress_line_100_percent_still_works(self):
        p = WhisperProgressParser()
        state = WhisperProgress()
        state = p.parse(
            "whisper_print_progress_callback: progress = 100%", state,
        )
        assert state.percent == 100.0

    def test_parse_timeline_line_appends_to_preview(self):
        p = WhisperProgressParser()
        state = WhisperProgress()
        state = p.parse(
            "[00:00:00.000 --> 00:00:02.000]  hello world", state,
        )
        assert "hello world" in state.text_preview

    def test_parse_multiple_timeline_lines_accumulates_preview(self):
        p = WhisperProgressParser()
        state = WhisperProgress()
        state = p.parse(
            "[00:00:00.000 --> 00:00:02.000]  line one", state,
        )
        state = p.parse(
            "[00:00:02.000 --> 00:00:04.000]  line two", state,
        )
        assert "line one" in state.text_preview
        assert "line two" in state.text_preview

    def test_parse_preview_truncates_at_1500_chars(self):
        p = WhisperProgressParser()
        state = WhisperProgress(text_preview="x" * 2000)
        state = p.parse(
            "[00:00:00.000 --> 00:00:02.000]  new", state,
        )
        assert len(state.text_preview) <= 1500
        assert state.text_preview.endswith("new")

    def test_parse_empty_line_returns_same_state(self):
        p = WhisperProgressParser()
        state = WhisperProgress(percent=15.0)
        result = p.parse("", state)
        assert result is state
        assert result.percent == 15.0

    def test_parse_unrelated_line_returns_same_state(self):
        p = WhisperProgressParser()
        state = WhisperProgress(percent=15.0)
        result = p.parse("some random log line", state)
        assert result.percent == 15.0


# ══════════════════════════════════════════════════════════
# yt-dlp 進度解析
# ══════════════════════════════════════════════════════════
class TestParseYtdlpPercent:
    def test_parse_ytdlp_percent_from_typical_line(self):
        line = "[download]  43.4% of   21.98MiB at   12.69MiB/s ETA 00:00"
        assert parse_ytdlp_percent(line) == 43.4

    def test_parse_ytdlp_percent_from_100_percent(self):
        line = "[download] 100.0% of   21.98MiB"
        assert parse_ytdlp_percent(line) == 100.0

    def test_parse_ytdlp_percent_returns_none_for_unrelated_line(self):
        assert parse_ytdlp_percent("random line") is None

    def test_parse_ytdlp_percent_returns_none_for_empty(self):
        assert parse_ytdlp_percent("") is None


# ══════════════════════════════════════════════════════════
# 執行檔偵測
# ══════════════════════════════════════════════════════════
class TestFindWhisperBin:
    def test_find_whisper_bin_from_path_returns_shutil_which_result(self):
        with patch("whisper_core.shutil.which") as which_mock:
            which_mock.side_effect = lambda n: (
                "/opt/homebrew/bin/whisper-cli" if n == "whisper-cli" else None
            )
            assert find_whisper_bin() == "/opt/homebrew/bin/whisper-cli"

    def test_find_whisper_bin_falls_back_to_extra_dir(self, tmp_path):
        (tmp_path / "whisper-cli").touch()
        with patch("whisper_core.shutil.which", return_value=None):
            result = find_whisper_bin(extra_dirs=[str(tmp_path)])
            assert result == str(tmp_path / "whisper-cli")

    def test_find_whisper_bin_returns_none_when_all_missing(self, tmp_path):
        with patch("whisper_core.shutil.which", return_value=None):
            assert find_whisper_bin(extra_dirs=[str(tmp_path)]) is None

    def test_find_whisper_bin_finds_legacy_main_binary(self, tmp_path):
        (tmp_path / "main").touch()
        with patch("whisper_core.shutil.which", return_value=None):
            result = find_whisper_bin(extra_dirs=[str(tmp_path)])
            assert result == str(tmp_path / "main")


# ══════════════════════════════════════════════════════════
# WhisperProgress dataclass defaults
# ══════════════════════════════════════════════════════════
class TestWhisperProgressDataclass:
    def test_whisper_progress_has_default_values(self):
        p = WhisperProgress()
        assert p.phase == ""
        assert p.percent == 0.0
        assert p.message == ""
        assert p.text_preview == ""

    def test_whisper_progress_accepts_keyword_args(self):
        p = WhisperProgress(phase="downloading", percent=10.5, message="hi")
        assert p.phase == "downloading"
        assert p.percent == 10.5
        assert p.message == "hi"

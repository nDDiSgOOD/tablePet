"""音乐资源管理 / Music library and on-demand WAV transcoding."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from ..config import FFMPEG_BIN, JAY_CHOU_DIR, MEDIA_DIR, MUSIC_CACHE_DIR
from ..utils.ffmpeg import require_ffmpeg


def music_candidates(query: str) -> list[Path]:
    """根据自然语言关键字挑选本地音乐候选。"""
    terms = query.lower()
    if any(token in terms for token in ("周杰伦", "周杰倫", "jay", "chou")):
        roots: list[Path] = [JAY_CHOU_DIR]
    else:
        roots = [MEDIA_DIR]
    exts = {".wav", ".mp3", ".m4a", ".flac", ".aac", ".ogg"}
    files: list[Path] = []
    for root in roots:
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if path.is_file() and path.suffix.lower() in exts:
                files.append(path)
    return sorted(files, key=lambda p: p.name.lower())


def convert_music_to_wav(source: Path) -> Path:
    """ffmpeg 转码为 16 kHz 单声道 WAV（结果带缓存）。"""
    require_ffmpeg()
    stat = source.stat()
    cache_name = f"{source.stem}_{stat.st_size}_{int(stat.st_mtime)}.wav"
    out_path = MUSIC_CACHE_DIR / re.sub(r"[^A-Za-z0-9_.-]+", "_", cache_name)
    if out_path.exists() and out_path.stat().st_size > 4096:
        return out_path
    subprocess.run(
        [
            FFMPEG_BIN,
            "-y",
            "-loglevel",
            "error",
            "-i",
            str(source),
            "-ac",
            "1",
            "-ar",
            "16000",
            "-sample_fmt",
            "s16",
            str(out_path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return out_path

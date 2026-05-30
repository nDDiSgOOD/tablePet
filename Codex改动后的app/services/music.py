"""音乐资源管理 / Music library and on-demand WAV transcoding."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from urllib.parse import urlparse

import httpx

from ..config import FFMPEG_BIN, ITUNES_SEARCH_URL, JAY_CHOU_DIR, MEDIA_DIR, MUSIC_CACHE_DIR
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


def _safe_preview_name(track: dict) -> str:
    artist = str(track.get("artistName") or "jay_chou")
    name = str(track.get("trackName") or "preview")
    ident = str(track.get("trackId") or abs(hash((artist, name))))
    base = re.sub(r"[^A-Za-z0-9_.-]+", "_", f"{artist}_{name}_{ident}")
    return base[:120]


def download_jay_chou_preview(query: str = "周杰伦") -> Path | None:
    """Download an official 30s preview clip when the local library is empty.

    The iTunes Search API exposes licensed preview URLs. This intentionally does
    not scrape or download full copyrighted tracks.
    """
    params = {
        "term": query or "周杰伦",
        "country": "TW",
        "media": "music",
        "entity": "song",
        "limit": "20",
    }
    with httpx.Client(timeout=12, follow_redirects=True) as client:
        response = client.get(ITUNES_SEARCH_URL, params=params)
        response.raise_for_status()
        results = response.json().get("results", [])

        selected: dict | None = None
        for item in results:
            artist = str(item.get("artistName") or "").lower()
            preview = str(item.get("previewUrl") or "")
            if preview and ("周杰伦" in artist or "jay chou" in artist):
                selected = item
                break
        if selected is None:
            for item in results:
                if item.get("previewUrl"):
                    selected = item
                    break
        if selected is None:
            return None

        ext = Path(urlparse(str(selected.get("previewUrl"))).path).suffix or ".m4a"
        raw_path = MUSIC_CACHE_DIR / f"{_safe_preview_name(selected)}{ext}"
        wav_path = MUSIC_CACHE_DIR / f"{_safe_preview_name(selected)}.wav"
        if wav_path.exists() and wav_path.stat().st_size > 4096:
            return wav_path
        preview_resp = client.get(str(selected["previewUrl"]))
        preview_resp.raise_for_status()
        raw_path.write_bytes(preview_resp.content)

    try:
        return convert_music_to_wav(raw_path)
    finally:
        raw_path.unlink(missing_ok=True)


def jay_chou_wav() -> tuple[Path, str]:
    candidates = music_candidates("周杰伦")
    if candidates:
        return convert_music_to_wav(candidates[0]), candidates[0].stem
    preview = download_jay_chou_preview("周杰伦 Jay Chou")
    if preview:
        return preview, "Jay Chou official preview"
    raise FileNotFoundError("No local Jay Chou music or official preview is available.")

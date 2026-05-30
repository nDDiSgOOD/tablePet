"""人脸/情绪视觉分析 / Face & emotion vision pipeline."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import numpy as np
from fastapi import HTTPException

from .. import state
from ..config import LATEST_FRAME_PATH, YUNET_MODEL_PATH

try:
    import cv2
except Exception:  # pragma: no cover - vision is optional
    cv2 = None  # type: ignore[assignment]


def _rotations(frame: np.ndarray) -> list[tuple[int, np.ndarray]]:
    return [
        (0, frame),
        (90, cv2.rotate(frame, cv2.ROTATE_90_CLOCKWISE)),
        (180, cv2.rotate(frame, cv2.ROTATE_180)),
        (270, cv2.rotate(frame, cv2.ROTATE_90_COUNTERCLOCKWISE)),
    ]


def _detect_with_yunet(frame: np.ndarray) -> dict[str, Any] | None:
    if cv2 is None or not hasattr(cv2, "FaceDetectorYN") or not YUNET_MODEL_PATH.exists():
        return None

    best: dict[str, Any] | None = None
    for orientation, work in _rotations(frame):
        height, width = work.shape[:2]
        detector = cv2.FaceDetectorYN.create(
            str(YUNET_MODEL_PATH),
            "",
            (width, height),
            0.55,
            0.3,
            5000,
        )
        _, faces = detector.detect(work)
        if faces is None:
            continue
        for face in faces:
            x, y, w, h = [float(v) for v in face[:4]]
            score = float(face[-1])
            area_ratio = max(0.0, w * h / float(width * height))
            rank = score * (0.4 + area_ratio)
            if best is None or rank > best["rank"]:
                best = {
                    "engine": "yunet",
                    "rank": rank,
                    "frame": work,
                    "box": (x, y, w, h),
                    "score": score,
                    "orientation": orientation,
                    "landmarks": face[4:14].reshape(5, 2).tolist() if len(face) >= 14 else [],
                }
    return best


def _detect_with_haar(frame: np.ndarray) -> dict[str, Any] | None:
    if cv2 is None:
        return None
    face_cascade = cv2.CascadeClassifier(
        str(Path(cv2.data.haarcascades) / "haarcascade_frontalface_default.xml")
    )
    best: dict[str, Any] | None = None
    for orientation, work in _rotations(frame):
        gray = cv2.cvtColor(work, cv2.COLOR_BGR2GRAY)
        gray = cv2.equalizeHist(gray)
        faces = face_cascade.detectMultiScale(
            gray, scaleFactor=1.08, minNeighbors=3, minSize=(24, 24)
        )
        height, width = gray.shape[:2]
        for x, y, w, h in faces:
            area_ratio = w * h / float(width * height)
            rank = area_ratio
            if best is None or rank > best["rank"]:
                best = {
                    "engine": "haar-fallback",
                    "rank": rank,
                    "frame": work,
                    "box": (float(x), float(y), float(w), float(h)),
                    "score": min(0.72, 0.35 + area_ratio * 7.0),
                    "orientation": orientation,
                    "landmarks": [],
                }
    return best


def _emotion_from_face(gray: np.ndarray, box: tuple[float, float, float, float]) -> str:
    if cv2 is None:
        return "neutral"
    x, y, w, h = [int(v) for v in box]
    x = max(0, x)
    y = max(0, y)
    face_roi = gray[y : max(y + h, y + 1), x : max(x + w, x + 1)]
    if face_roi.size == 0:
        return "neutral"
    smile_cascade = cv2.CascadeClassifier(
        str(Path(cv2.data.haarcascades) / "haarcascade_smile.xml")
    )
    smiles = smile_cascade.detectMultiScale(
        face_roi, scaleFactor=1.7, minNeighbors=14, minSize=(18, 10)
    )
    return "happy" if len(smiles) > 0 else "neutral"


def _vision_result(frame: np.ndarray) -> dict[str, Any]:
    detection = _detect_with_yunet(frame) or _detect_with_haar(frame)
    if detection is None:
        return {
            "face": False,
            "attention": False,
            "emotion": "unknown",
            "confidence": 0.0,
            "engine": "yunet" if YUNET_MODEL_PATH.exists() else "haar-fallback",
        }

    work = detection["frame"]
    gray = cv2.cvtColor(work, cv2.COLOR_BGR2GRAY)
    height, width = gray.shape[:2]
    x, y, w, h = detection["box"]
    cx = (x + w / 2) / width
    cy = (y + h / 2) / height
    area_ratio = (w * h) / float(width * height)
    score = float(detection["score"])
    centered = abs(cx - 0.5) < 0.28 and 0.16 < cy < 0.82
    attention = bool(centered and area_ratio > 0.025 and score > 0.50)
    confidence = float(max(0.18, min(0.99, score * 0.72 + area_ratio * 2.8)))

    return {
        "face": True,
        "attention": attention,
        "emotion": _emotion_from_face(gray, detection["box"]),
        "confidence": round(confidence, 3),
        "engine": detection["engine"],
        "orientation": detection["orientation"],
        "box": {"x": int(x), "y": int(y), "w": int(w), "h": int(h)},
        "frame": {"w": int(width), "h": int(height)},
    }


async def process_vision_jpeg(
    device_id: str, image: bytes, transport: str = "wifi"
) -> dict[str, Any]:
    started = time.perf_counter()
    LATEST_FRAME_PATH.write_bytes(image)
    if cv2 is None:
        result = {
            "face": False,
            "attention": False,
            "emotion": "unknown",
            "confidence": 0.0,
            "reason": "opencv not installed",
        }
        result["vision_ms"] = round((time.perf_counter() - started) * 1000, 1)
        state.update_device(
            device_id, vision=result, last_vision_ms=result["vision_ms"], transport=transport
        )
        return result

    if state.VISION_LOCK.locked():
        result = dict(state.DEVICE_STATES.get(device_id, {}).get("vision", {}))
        if not result:
            result = {
                "face": False,
                "attention": False,
                "emotion": "unknown",
                "confidence": 0.0,
            }
        result["snapshot"] = "/snapshot/latest.jpg"
        result["busy_skip"] = True
        result["vision_ms"] = round((time.perf_counter() - started) * 1000, 1)
        state.update_device(
            device_id,
            vision=result,
            last_vision_ms=result["vision_ms"],
            last_vision_skipped=True,
            transport=transport,
        )
        return result

    async with state.VISION_LOCK:
        arr = np.frombuffer(image, dtype=np.uint8)
        frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if frame is None:
            raise HTTPException(status_code=400, detail="Invalid JPEG image.")

        result = _vision_result(frame)
        result["snapshot"] = "/snapshot/latest.jpg"
        result["busy_skip"] = False
        result["vision_ms"] = round((time.perf_counter() - started) * 1000, 1)
        state.update_device(
            device_id,
            vision=result,
            last_vision_ms=result["vision_ms"],
            last_vision_skipped=False,
            transport=transport,
        )
    return result

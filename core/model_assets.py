"""On-demand download and caching of open-source CV model weights.

The proctoring pipeline needs a handful of small model files. None of them ship
inside the Python wheels, so they are fetched once into ``models/``
(git-ignored) and reused.

Licences differ and that is deliberate: the three face models are Apache-2.0,
while ``yolo11n`` is AGPL-3.0. The user confirmed this project is open source
(a final-year B.Tech project), which makes AGPL acceptable — see the decisions
table in ``tasks.md``.

Downloads are best-effort: if the machine is offline the caller gets ``None``
and the proctoring layer degrades to whatever backend is available instead of
taking the interview down.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from pathlib import Path

import requests

from core.config import PROJECT_ROOT

log = logging.getLogger(__name__)

MODELS_DIR = PROJECT_ROOT / "models"

_OPENCV_ZOO = "https://github.com/opencv/opencv_zoo/raw/main/models"
_MEDIAPIPE = "https://storage.googleapis.com/mediapipe-models"
_ULTRALYTICS = "https://github.com/ultralytics/assets/releases/download/v8.3.0"


@dataclass(frozen=True)
class ModelAsset:
    key: str
    filename: str
    url: str
    approx_bytes: int
    licence: str
    purpose: str


ASSETS: dict[str, ModelAsset] = {
    "yunet": ModelAsset(
        key="yunet",
        filename="face_detection_yunet_2023mar.onnx",
        url=f"{_OPENCV_ZOO}/face_detection_yunet/face_detection_yunet_2023mar.onnx",
        approx_bytes=232_589,
        licence="Apache-2.0 (OpenCV Zoo)",
        purpose="Face detection: how many people are in frame, where they are.",
    ),
    "sface": ModelAsset(
        key="sface",
        filename="face_recognition_sface_2021dec.onnx",
        url=f"{_OPENCV_ZOO}/face_recognition_sface/face_recognition_sface_2021dec.onnx",
        approx_bytes=38_696_353,
        licence="Apache-2.0 (OpenCV Zoo)",
        purpose="Face embeddings: is this still the person who started the interview?",
    ),
    "face_landmarker": ModelAsset(
        key="face_landmarker",
        filename="face_landmarker.task",
        url=(
            f"{_MEDIAPIPE}/face_landmarker/face_landmarker/float16/latest/"
            "face_landmarker.task"
        ),
        approx_bytes=3_758_596,
        licence="Apache-2.0 (MediaPipe)",
        purpose="Dense landmarks + head pose matrix: precise gaze-off-screen detection.",
    ),
    "yolo": ModelAsset(
        key="yolo",
        filename="yolo11n.pt",
        url=f"{_ULTRALYTICS}/yolo11n.pt",
        approx_bytes=5_613_764,
        licence="AGPL-3.0 (Ultralytics)",
        purpose="Object detection: phone in frame, a second person, notes on the desk.",
    ),
}

_locks: dict[str, threading.Lock] = {key: threading.Lock() for key in ASSETS}


def local_path(key: str) -> Path:
    return MODELS_DIR / ASSETS[key].filename


def is_cached(key: str) -> bool:
    path = local_path(key)
    # A truncated download is worse than none: sanity-check the size.
    return path.exists() and path.stat().st_size > ASSETS[key].approx_bytes * 0.5


def ensure(key: str, *, allow_download: bool = True) -> Path | None:
    """Return the local path to a model, downloading it once if needed."""
    if key not in ASSETS:
        raise KeyError(f"unknown model asset: {key}")
    if is_cached(key):
        return local_path(key)
    if not allow_download:
        return None

    asset = ASSETS[key]
    with _locks[key]:
        if is_cached(key):  # another thread may have won the race
            return local_path(key)

        MODELS_DIR.mkdir(parents=True, exist_ok=True)
        target = local_path(key)
        tmp = target.with_suffix(target.suffix + ".part")
        try:
            log.info("Downloading model %s (%s)", asset.key, asset.licence)
            with requests.get(asset.url, stream=True, timeout=180) as response:
                response.raise_for_status()
                with tmp.open("wb") as handle:
                    for chunk in response.iter_content(chunk_size=1 << 16):
                        if chunk:
                            handle.write(chunk)
            tmp.replace(target)
            log.info("Model %s cached at %s", asset.key, target)
            return target
        except Exception as exc:  # network, DNS, disk — all non-fatal
            log.warning("Could not fetch model %s: %s", asset.key, exc)
            tmp.unlink(missing_ok=True)
            return None


def status() -> list[dict[str, object]]:
    """Used by the admin health page."""
    return [
        {
            "key": asset.key,
            "file": asset.filename,
            "cached": is_cached(asset.key),
            "size_mb": round(asset.approx_bytes / 1_048_576, 2),
            "licence": asset.licence,
            "purpose": asset.purpose,
        }
        for asset in ASSETS.values()
    ]

"""Per-frame computer-vision analysis for proctoring.

Wraps three optional backends in a single interface:
- YuNet (OpenCV Zoo, Apache-2.0): face detection + count
- SFace (OpenCV Zoo, Apache-2.0): face embeddings for substitution detection
- MediaPipe FaceLandmarker (Apache-2.0): head pose (yaw / pitch)
- Ultralytics YOLO11n (AGPL-3.0): phone, extra person, notes on desk

Every backend is lazy-loaded and silently absent when the model file is not
cached. The caller gets a ``FrameAnalysis`` whether the models are present or
not; missing fields are ``None`` / empty so downstream rules can branch on
availability rather than catching exceptions mid-interview.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from core import model_assets
from core.config import settings

log = logging.getLogger(__name__)

# YOLO class ids relevant to proctoring (COCO-80 subset)
_YOLO_CLASSES = {
    67: "phone",      # cell phone
    0:  "person",     # person (second person in frame)
    73: "book",       # book / notes
    63: "laptop",     # laptop
}


@dataclass
class HeadPose:
    yaw: float = 0.0    # left/right  — positive = turned right
    pitch: float = 0.0  # up/down     — positive = tilted down
    roll: float = 0.0   # tilt        — positive = tilted right


@dataclass
class DetectedObject:
    label: str
    confidence: float
    box: tuple[int, int, int, int]  # x, y, w, h  (pixel coords)


@dataclass
class FrameAnalysis:
    face_count: int = 0
    face_boxes: list[tuple[int, int, int, int]] = field(default_factory=list)
    head_pose: HeadPose | None = None
    face_embedding: np.ndarray | None = None  # shape (128,) float32
    objects: list[DetectedObject] = field(default_factory=list)


class Analyzer:
    """Stateful per-session analyzer.  Create one instance per interview."""

    def __init__(self) -> None:
        self._enrollment_embedding: np.ndarray | None = None
        self._frame_idx = 0
        self._step = settings.proctoring.analyze_every_n_frames

    # ------------------------------------------------------------------ #
    # Lazy backends                                                        #
    # ------------------------------------------------------------------ #

    @cached_property
    def _yunet(self) -> Any | None:
        path = model_assets.ensure("yunet", allow_download=True)
        if path is None:
            return None
        try:
            detector = cv2.FaceDetectorYN.create(
                str(path), "", (640, 640),
                score_threshold=0.7,
                nms_threshold=0.3,
                top_k=5,
            )
            return detector
        except Exception as exc:
            log.warning("YuNet load failed: %s", exc)
            return None

    @cached_property
    def _sface(self) -> Any | None:
        path = model_assets.ensure("sface", allow_download=True)
        if path is None:
            return None
        try:
            return cv2.FaceRecognizerSF.create(str(path), "")
        except Exception as exc:
            log.warning("SFace load failed: %s", exc)
            return None

    @cached_property
    def _mp_pose(self) -> Any | None:
        """MediaPipe FaceLandmarker for head-pose estimation."""
        try:
            import mediapipe as mp
            path = model_assets.ensure("face_landmarker", allow_download=True)
            if path is None:
                return None
            base_opts = mp.tasks.BaseOptions(model_asset_path=str(path))
            opts = mp.tasks.vision.FaceLandmarkerOptions(
                base_options=base_opts,
                output_face_blendshapes=False,
                output_facial_transformation_matrixes=True,
                num_faces=1,
                running_mode=mp.tasks.vision.RunningMode.IMAGE,
            )
            return mp.tasks.vision.FaceLandmarker.create_from_options(opts)
        except Exception as exc:
            log.debug("MediaPipe FaceLandmarker unavailable: %s", exc)
            return None

    @cached_property
    def _yolo(self) -> Any | None:
        if not settings.proctoring.object_detection:
            return None
        path = model_assets.ensure("yolo", allow_download=True)
        if path is None:
            return None
        try:
            from ultralytics import YOLO
            return YOLO(str(path))
        except Exception as exc:
            log.warning("YOLO load failed: %s", exc)
            return None

    # ------------------------------------------------------------------ #
    # Public API                                                           #
    # ------------------------------------------------------------------ #

    def enroll(self, frame: np.ndarray) -> bool:
        """Capture enrollment embedding from the consent screen."""
        emb = self._face_embedding(frame)
        if emb is not None:
            self._enrollment_embedding = emb
            return True
        return False

    def analyze(self, frame: np.ndarray) -> FrameAnalysis | None:
        """Return analysis or None if this frame should be skipped."""
        self._frame_idx += 1
        if self._frame_idx % self._step != 0:
            return None
        return self._analyze(frame)

    def analyze_always(self, frame: np.ndarray) -> FrameAnalysis:
        """Analyze unconditionally — for snapshots and enrollment."""
        return self._analyze(frame)

    # ------------------------------------------------------------------ #
    # Internal                                                             #
    # ------------------------------------------------------------------ #

    def _analyze(self, frame: np.ndarray) -> FrameAnalysis:
        result = FrameAnalysis()
        h, w = frame.shape[:2]

        # --- face detection (YuNet) ---
        faces = self._detect_faces(frame, w, h)
        result.face_count = len(faces)
        result.face_boxes = faces

        # --- head pose (MediaPipe) ---
        if faces:
            result.head_pose = self._head_pose_mp(frame)

        # --- face embedding (SFace) for substitution ---
        if faces and self._enrollment_embedding is not None:
            result.face_embedding = self._face_embedding(frame)

        # --- object detection (YOLO) ---
        if self._yolo is not None:
            result.objects = self._detect_objects(frame)

        return result

    def _detect_faces(
        self, frame: np.ndarray, w: int, h: int
    ) -> list[tuple[int, int, int, int]]:
        det = self._yunet
        if det is None:
            return []
        try:
            det.setInputSize((w, h))
            _, faces = det.detect(frame)
            if faces is None:
                return []
            return [
                (int(f[0]), int(f[1]), int(f[2]), int(f[3]))
                for f in faces
            ]
        except Exception as exc:
            log.debug("Face detection error: %s", exc)
            return []

    def _head_pose_mp(self, frame: np.ndarray) -> HeadPose | None:
        lm = self._mp_pose
        if lm is None:
            return None
        try:
            import mediapipe as mp
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            mp_image = mp.Image(
                image_format=mp.ImageFormat.SRGB, data=rgb
            )
            result = lm.detect(mp_image)
            if not result.facial_transformation_matrixes:
                return None
            mat = np.array(result.facial_transformation_matrixes[0])
            # Euler angles from the 4×4 rotation matrix
            sy = np.sqrt(mat[0, 0] ** 2 + mat[1, 0] ** 2)
            if sy > 1e-6:
                pitch = float(np.degrees(np.arctan2(-mat[2, 0], sy)))
                yaw   = float(np.degrees(np.arctan2(mat[2, 1], mat[2, 2])))
                roll  = float(np.degrees(np.arctan2(mat[1, 0], mat[0, 0])))
            else:
                pitch = float(np.degrees(np.arctan2(-mat[2, 0], sy)))
                yaw   = 0.0
                roll  = float(np.degrees(np.arctan2(-mat[0, 1], mat[1, 1])))
            return HeadPose(yaw=yaw, pitch=pitch, roll=roll)
        except Exception as exc:
            log.debug("Head pose error: %s", exc)
            return None

    def _face_embedding(self, frame: np.ndarray) -> np.ndarray | None:
        rec = self._sface
        det = self._yunet
        if rec is None or det is None:
            return None
        h, w = frame.shape[:2]
        try:
            det.setInputSize((w, h))
            _, faces = det.detect(frame)
            if faces is None or len(faces) == 0:
                return None
            return rec.feature(rec.alignCrop(frame, faces[0]))
        except Exception as exc:
            log.debug("Face embedding error: %s", exc)
            return None

    def substitution_cosine(self, embedding: np.ndarray) -> float | None:
        """Return cosine similarity to enrollment embedding, or None."""
        if self._enrollment_embedding is None or self._sface is None:
            return None
        try:
            score = self._sface.match(
                self._enrollment_embedding,
                embedding,
                cv2.FaceRecognizerSF_FR_COSINE,
            )
            return float(score)
        except Exception:
            return None

    def _detect_objects(self, frame: np.ndarray) -> list[DetectedObject]:
        yolo = self._yolo
        if yolo is None:
            return []
        try:
            results = yolo(frame, verbose=False, conf=0.40)[0]
            found = []
            for box in results.boxes:
                cls = int(box.cls[0])
                if cls not in _YOLO_CLASSES:
                    continue
                x1, y1, x2, y2 = box.xyxy[0].tolist()
                found.append(DetectedObject(
                    label=_YOLO_CLASSES[cls],
                    confidence=float(box.conf[0]),
                    box=(int(x1), int(y1), int(x2 - x1), int(y2 - y1)),
                ))
            return found
        except Exception as exc:
            log.debug("YOLO error: %s", exc)
            return []

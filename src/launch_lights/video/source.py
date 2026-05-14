"""Video sources: webcam, file, synthetic test patterns.

Each source returns BGR uint8 frames (so we share the OpenCV color
convention through the file/webcam paths). The pipeline converts to RGB
at ingress."""
from __future__ import annotations

import logging
from typing import Optional, Protocol

import cv2
import numpy as np

from launch_lights.engine.frame import Frame
from launch_lights.video.patterns import build_pattern

log = logging.getLogger(__name__)


class VideoSource(Protocol):
    def read(self) -> Optional[np.ndarray]:
        """Return a BGR uint8 H x W x 3 frame, or None when no frame is
        available right now (caller should reuse the last good frame)."""
        ...

    def close(self) -> None: ...


class WebcamSource:
    """Live camera via cv2.VideoCapture."""

    def __init__(self, index: int = 0) -> None:
        self._cap = cv2.VideoCapture(index)
        if not self._cap.isOpened():
            raise RuntimeError(f"could not open webcam at index {index}")

    def read(self) -> Optional[np.ndarray]:
        ok, frame = self._cap.read()
        return frame if ok else None

    def close(self) -> None:
        self._cap.release()


class FileSource:
    """Video file with EOF looping. Decodes on the calling thread."""

    def __init__(self, path: str, *, loop: bool = True) -> None:
        self._path = path
        self._loop = loop
        self._cap = cv2.VideoCapture(path)
        if not self._cap.isOpened():
            raise RuntimeError(f"could not open video file: {path}")

    def read(self) -> Optional[np.ndarray]:
        ok, frame = self._cap.read()
        if ok:
            return frame
        if not self._loop:
            return None
        # EOF: rewind and try once more
        self._cap.release()
        self._cap = cv2.VideoCapture(self._path)
        ok, frame = self._cap.read()
        return frame if ok else None

    def close(self) -> None:
        self._cap.release()


class TestPatternSource:
    """Synthesizes Frames from the built-in patterns.

    Unlike webcam/file sources this returns a Frame directly; the pipeline
    treats this as a special case (skip downsample/quantize, go straight to
    the renderer). The CLI wires it via ``read_frame`` instead of ``read``.
    """

    def __init__(self, name: str, *, flood_color: str = "#ff0000") -> None:
        self._pattern = build_pattern(name, flood_color=flood_color)
        self._t0: Optional[float] = None

    def read_frame(self, elapsed: float) -> Frame:
        return self._pattern(elapsed)

    def read(self) -> Optional[np.ndarray]:
        raise NotImplementedError("TestPatternSource returns Frames, not BGR arrays")

    def close(self) -> None:
        return None

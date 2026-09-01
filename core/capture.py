"""Screen capture abstraction using Pillow's ImageGrab (X11/Windows/macOS)."""

import io
import threading

from PIL import Image, ImageGrab


class CaptureError(Exception):
    """Raised when no working capture backend is available."""


class ScreenCapture:
    """Captures the primary screen at a target fps and encodes JPEG frames.

    The captured image is normalized to ``target_size`` (fit, preserving the
    aspect ratio, up- or down-scaled) so every sharer sends the same
    resolution - by default 1600x900 (16:9).
    """

    def __init__(self, region=None, scale=1.0, quality=80, target_size=(1600, 900)):
        self.region = tuple(region) if region else None
        self.scale = scale
        self.quality = quality
        self.target_size = tuple(target_size) if target_size else None
        self._lock = threading.Lock()

    def grab_raw(self):
        """Return a raw PIL.Image of the current screen (normalized)."""
        with self._lock:
            image = ImageGrab.grab(bbox=self.region)
        if self.scale != 1.0:
            size = (
                max(1, int(image.width * self.scale)),
                max(1, int(image.height * self.scale)),
            )
            image = image.resize(size, Image.BILINEAR)
        if self.target_size:
            image = self._fit(image, self.target_size[0], self.target_size[1])
        return image

    @staticmethod
    def _fit(image, width, height):
        ratio = min(width / image.width, height / image.height)
        if abs(ratio - 1.0) < 1e-6:
            return image
        size = (
            max(1, round(image.width * ratio)),
            max(1, round(image.height * ratio)),
        )
        return image.resize(size, Image.LANCZOS)

    def grab_jpeg(self):
        """Return JPEG bytes of the current screen."""
        image = self.grab_raw()
        buffer = io.BytesIO()
        image.convert("RGB").save(buffer, format="JPEG", quality=self.quality)
        return buffer.getvalue()

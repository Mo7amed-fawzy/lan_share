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

    def __init__(
        self,
        region=None,
        scale=1.0,
        quality=80,
        target_size=(1600, 900),
        show_cursor=True,
    ):
        self.region = tuple(region) if region else None
        self.scale = scale
        self.quality = quality
        self.target_size = tuple(target_size) if target_size else None
        self.show_cursor = show_cursor
        self._lock = threading.Lock()

    def grab_raw(self):
        """Return a raw PIL.Image of the current screen (normalized).

        When ``show_cursor`` is set the real X11 pointer is painted onto the
        frame so remote viewers can see where the host's mouse is.
        """
        with self._lock:
            image = ImageGrab.grab(bbox=self.region)
        if self.show_cursor:
            image = self._draw_cursor(image)
        if self.scale != 1.0:
            size = (
                max(1, int(image.width * self.scale)),
                max(1, int(image.height * self.scale)),
            )
            image = image.resize(size, Image.BILINEAR)
        if self.target_size:
            image = self._fit(image, self.target_size[0], self.target_size[1])
        return image

    def _draw_cursor(self, image):
        """Paint the current X11 pointer onto ``image`` (scaled to fit it)."""
        try:
            from Xlib import display as xdisplay

            d = xdisplay.Display()
            root = d.screen().root
            pointer = root.query_pointer()
            px, py = pointer.root_x, pointer.root_y
        except Exception:
            return image
        try:
            rx, ry = (self.region[0], self.region[1]) if self.region else (0, 0)
            ix, iy = px - rx, py - ry
            if ix < 0 or iy < 0 or ix >= image.width or iy >= image.height:
                return image
            from PIL import ImageDraw

            draw = ImageDraw.Draw(image, "RGBA")
            s = max(10, min(image.width, image.height) // 90)
            tip = (ix, iy)
            pts = [
                tip,
                (ix + s, iy + s),
                (ix + int(0.55 * s), iy + int(0.6 * s)),
            ]
            draw.polygon(pts, outline=(0, 0, 0, 255), fill=(255, 255, 255, 255))
            draw.polygon(
                [tip, (ix + s, iy + s), (ix + int(0.7 * s), iy + s)],
                outline=(0, 0, 0, 255),
                fill=(0, 0, 0, 255),
            )
        except Exception:
            pass
        finally:
            try:
                d.close()
            except Exception:
                pass
        return image
        """Expected (width, height) of images produced by ``grab_raw``.

        Performs a single screen grab (no encode) so the sharer can report
        its true normalized resolution to the relay.
        """
        with self._lock:
            image = ImageGrab.grab(bbox=self.region)
        if self.scale != 1.0:
            return (
                max(1, int(image.width * self.scale)),
                max(1, int(image.height * self.scale)),
            )
        if self.target_size:
            ratio = min(
                self.target_size[0] / image.width,
                self.target_size[1] / image.height,
            )
            if abs(ratio - 1.0) < 1e-6:
                return (image.width, image.height)
            return (
                max(1, round(image.width * ratio)),
                max(1, round(image.height * ratio)),
            )
        return (image.width, image.height)

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

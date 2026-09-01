"""Stream viewer.

Uses a tkinter window to display incoming JPEG frames. If tkinter is not
available a headless fallback just decodes frames (useful on servers / CI).
"""

import io

from PIL import Image


class HeadlessPlayer:
    """Decodes frames but does not display anything."""

    def __init__(self, title="Lan-Share", on_close=None):
        self.title = title
        self._on_close = on_close

    def show(self, jpeg_bytes):
        Image.open(io.BytesIO(jpeg_bytes))

    def start(self):
        pass

    def close(self):
        if self._on_close:
            self._on_close()


def create_player(title="Lan-Share", on_close=None):
    """Return a player. Prefers tkinter, falls back to HeadlessPlayer."""
    try:
        import tkinter as tk
    except ImportError:
        return HeadlessPlayer(title=title, on_close=on_close)

    class TkPlayer:
        def __init__(self):
            try:
                from PIL import ImageTk
            except ImportError:
                raise RuntimeError("PIL ImageTk requires python3-tk")
            self._imagetk = ImageTk
            self.root = tk.Tk()
            self.root.title(title)
            self.root.protocol("WM_DELETE_WINDOW", self._closed)
            self._label = tk.Label(self.root)
            self._label.pack()
            self._photo = None
            self._on_close = on_close

        def show(self, jpeg_bytes):
            image = Image.open(io.BytesIO(jpeg_bytes))
            self._photo = self._imagetk.PhotoImage(image)
            self._label.configure(image=self._photo)
            self._label.update_idletasks()

        def start(self):
            self.root.mainloop()

        def _closed(self):
            self.root.destroy()
            if self._on_close:
                self._on_close()

        def close(self):
            try:
                self.root.after(0, self.root.destroy)
            except Exception:
                pass

    return TkPlayer()

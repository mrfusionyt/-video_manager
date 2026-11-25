"""Log capture for the VideoManager launcher.

Kept in a separate module so it can be imported (and installed) *before*
app / yt-dlp / downloader modules are imported — their startup prints are then
visible in the launcher log panel too.
"""

import logging
import queue
import sys
import threading

LOG_QUEUE = queue.Queue(maxsize=5000)
_installed = False


def _queue_line(line):
    line = (line or '').rstrip()
    if not line:
        return
    try:
        LOG_QUEUE.put_nowait(line)
    except queue.Full:
        pass


class _LogStream:
    """Tee: writes to the real stream (may be None in a windowed exe) and to the UI log."""

    def __init__(self, stream):
        self.stream = stream
        self._buf = ''
        self._lock = threading.Lock()

    def write(self, data):
        if data is None:
            return 0
        if isinstance(data, (bytes, bytearray)):
            data = data.decode('utf-8', 'replace')
        text = str(data)
        if self.stream is not None:
            try:
                self.stream.write(text)
            except Exception:
                pass
        text = text.replace('\r\n', '\n').replace('\r', '\n')
        with self._lock:
            self._buf += text
            while '\n' in self._buf:
                line, self._buf = self._buf.split('\n', 1)
                _queue_line(line)
        return len(text)

    def flush(self):
        if self.stream is not None:
            try:
                self.stream.flush()
            except Exception:
                pass
        with self._lock:
            if self._buf:
                _queue_line(self._buf)
                self._buf = ''

    def isatty(self):
        return False

    def fileno(self):
        raise OSError('stream has no fileno')


def install_log_redirect():
    """Capture Flask / yt-dlp / updater output into the launcher log panel."""
    global _installed
    if _installed:
        return
    _installed = True
    sys.stdout = _LogStream(sys.stdout)
    sys.stderr = _LogStream(sys.stderr)
    try:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(logging.Formatter('%(message)s'))
        handler.setLevel(logging.INFO)
        root = logging.getLogger()
        root.addHandler(handler)
        if root.level == logging.NOTSET or root.level > logging.INFO:
            root.setLevel(logging.INFO)
    except Exception:
        pass


def append_log(text):
    _queue_line(text)

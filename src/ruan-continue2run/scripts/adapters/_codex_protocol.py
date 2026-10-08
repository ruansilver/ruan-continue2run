"""Read app-server JSONL without mixing selectors and buffered text streams."""
from __future__ import annotations

import json
import queue
import threading
import time
from typing import Any

_READER_ATTRIBUTE = "_ruan_continue2run_jsonl_reader"
_EOF = object()


class _Reader:
    def __init__(self, stream):
        self.messages: queue.Queue[Any] = queue.Queue()
        self.eof_consumed = False
        self.thread = threading.Thread(target=self._read, args=(stream,), daemon=True)
        self.thread.start()

    def _read(self, stream) -> None:
        try:
            for line in stream:
                try:
                    message = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(message, dict):
                    self.messages.put(message)
        except (OSError, ValueError, UnicodeError):
            # Closing the process/stream ends this reader just like EOF.
            pass
        finally:
            self.messages.put(_EOF)


def read_message(proc, deadline: float) -> dict[str, Any] | None:
    """Read one JSON object before an absolute monotonic deadline.

    Each process keeps one daemon reader for its text stdout. It consumes all
    buffered lines and preserves unread messages between RPC/notification waits.
    A timeout leaves the reader alive, so a later call can receive the message.
    """
    if proc.stdout is None:
        return None
    reader = getattr(proc, _READER_ATTRIBUTE, None)
    if reader is None:
        reader = _Reader(proc.stdout)
        setattr(proc, _READER_ATTRIBUTE, reader)
    if reader.eof_consumed:
        return None
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        return None
    try:
        message = reader.messages.get(timeout=remaining)
    except queue.Empty:
        return None
    if message is _EOF:
        reader.eof_consumed = True
        return None
    return message

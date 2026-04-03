"""Flow control handler for XON/XOFF pause/resume from firmware."""

from __future__ import annotations

import logging
import threading
from collections import deque
from typing import Callable, Optional

logger = logging.getLogger(__name__)


class FlowControl:
    """Handles XON/XOFF flow control from the ESP32 firmware.

    The firmware sends XOFF (0x14 "OFF") when its TX queue exceeds 48 items
    and XON (0x14 "ON") when the queue drops below 16 items.

    When paused, outgoing writes are buffered locally until XON is received.
    """

    def __init__(self, write_callback: Callable[[bytes], int]) -> None:
        """Initialize flow control handler.

        Args:
            write_callback: Function to call for actual serial writes.
                           Must return number of bytes written, or -1 on error.
        """
        self._write_callback = write_callback
        self._paused: bool = False
        self._buffer: deque[bytes] = deque()
        self._lock = threading.Lock()
        self._total_dropped: int = 0
        self._max_buffer: int = 0

    @property
    def is_paused(self) -> bool:
        """True if writes are currently paused due to XOFF."""
        with self._lock:
            return self._paused

    @property
    def buffer_size(self) -> int:
        """Number of buffered write chunks waiting to be sent."""
        with self._lock:
            return len(self._buffer)

    @property
    def max_buffer_reached(self) -> int:
        """Peak number of buffered chunks."""
        with self._lock:
            return self._max_buffer

    @property
    def total_dropped(self) -> int:
        """Number of writes dropped while at max buffer."""
        with self._lock:
            return self._total_dropped

    def handle_xoff(self) -> None:
        """Called when XOFF is received. Pause outgoing writes."""
        with self._lock:
            if not self._paused:
                self._paused = True
                logger.info("FlowControl: paused (XOFF received)")

    def handle_xon(self) -> None:
        """Called when XON is received. Resume outgoing writes, flush buffer."""
        with self._lock:
            if self._paused:
                self._paused = False
                logger.info("FlowControl: resumed (XON received) — flushing %d buffered chunks", len(self._buffer))

        # Flush outside the lock to avoid blocking reads
        self._flush()

    def write(self, data: bytes) -> int:
        """Write data, respecting flow control.

        Args:
            data: Bytes to write.

        Returns:
            Number of bytes written (buffered + sent), or -1 on error.
        """
        with self._lock:
            if self._paused:
                self._buffer.append(data)
                self._max_buffer = max(self._max_buffer, len(self._buffer))
                return len(data)  # accepted but buffered
            else:
                return self._write_callback(data)

    def _flush(self) -> None:
        """Flush all buffered data. Must be called without _lock held."""
        while True:
            chunk = None
            with self._lock:
                if self._paused or len(self._buffer) == 0:
                    break
                chunk = self._buffer.popleft()

            if chunk is not None:
                written = self._write_callback(chunk)
                if written < 0:
                    logger.warning("FlowControl: write error during flush")
                    with self._lock:
                        self._total_dropped += 1
                    break

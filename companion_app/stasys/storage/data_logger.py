"""Background thread that routes parsed protocol packets to session and raw storage."""

from __future__ import annotations

import logging
import threading
from queue import Queue
from typing import Any

from stasys.protocol.packets import DataRawSample, EvtShotDetected
from stasys.storage.raw_store import RawStore
from stasys.storage.session_store import SessionStore

logger = logging.getLogger(__name__)


class DataLogger:
    """Background thread that drains a packet queue and writes to storage.

    Routes each packet type to the appropriate storage backend:
        - ``DataRawSample``   → raw_store (npy) + session_store (DB)
        - ``EvtShotDetected`` → raw_store (npy) + session_store (DB)
        - Other packet types → silently ignored
    """

    def __init__(
        self,
        session_id: int,
        session_store: SessionStore,
        raw_store: RawStore,
        packet_queue: Queue[Any],
    ) -> None:
        self._session_id = session_id
        self._session_store = session_store
        self._raw_store = raw_store
        self._queue = packet_queue
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._shot_count = 0

    @property
    def shot_count(self) -> int:
        """Number of shots recorded in this session."""
        return self._shot_count

    # -------------------------------------------------------------------------
    # Lifecycle
    # -------------------------------------------------------------------------

    def start(self) -> None:
        """Start the logger thread. Idempotent if already running."""
        if self._thread is not None:
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, name="DataLogger", daemon=True)
        self._thread.start()
        logger.info("DataLogger started for session %d", self._session_id)

    def stop(self, timeout: float = 5.0) -> None:
        """Signal the logger thread to stop and wait for it to finish.

        Drains any remaining packets in the queue before returning.
        """
        if self._thread is None:
            return
        self._stop_event.set()
        self._thread.join(timeout=timeout)
        self._thread = None
        logger.info("DataLogger stopped for session %d (shots=%d)", self._session_id, self._shot_count)

    # -------------------------------------------------------------------------
    # Internal: worker loop
    # -------------------------------------------------------------------------

    def _run(self) -> None:
        """Consume packets from the queue until stop() is called."""
        while not self._stop_event.is_set():
            try:
                packet = self._queue.get(timeout=0.1)
            except Exception:
                continue

            try:
                self._route(packet)
            except Exception as exc:
                logger.exception("Error routing packet: %s", exc)

            self._queue.task_done()

        # Drain remaining packets on shutdown
        while True:
            try:
                packet = self._queue.get_nowait()
            except Exception:
                break
            try:
                self._route(packet)
            except Exception as exc:
                logger.exception("Error draining packet: %s", exc)
            self._queue.task_done()

    def _route(self, packet: Any) -> None:
        """Dispatch a packet to the appropriate storage backend."""
        if isinstance(packet, DataRawSample):
            self._raw_store.record_imu(self._session_id, packet)
            self._session_store.record_imu(self._session_id, packet)

        elif isinstance(packet, EvtShotDetected):
            self._shot_count += 1
            self._raw_store.record_shot(self._session_id, packet)
            self._session_store.record_shot(self._session_id, packet)
            self._session_store.update_shot_count(self._session_id, self._shot_count)

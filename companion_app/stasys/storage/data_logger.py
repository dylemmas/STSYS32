"""Background thread that routes parsed protocol packets to session and raw storage."""

from __future__ import annotations

import logging
import threading
import time
from queue import Queue
from typing import Any

from stasys.protocol.packets import DataRawSample, EvtShotDetected
from stasys.storage.raw_store import RawStore
from stasys.storage.session_store import SessionStore

logger = logging.getLogger(__name__)

# Flush batches when either threshold is reached
BATCH_SIZE = 100       # flush after accumulating this many IMU samples
BATCH_TIMEOUT = 1.0    # or after this many seconds, whichever comes first


class DataLogger:
    """Background thread that drains a packet queue and writes to storage.

    Routes each packet type to the appropriate storage backend:
        - ``DataRawSample``   → raw_store (npy, batched) + session_store (batched)
        - ``EvtShotDetected`` → raw_store (npy) + session_store (immediate)
        - Other packet types → silently ignored

    IMU samples are accumulated in memory and flushed to disk in batches
    of up to BATCH_SIZE samples or at most every BATCH_TIMEOUT seconds,
    whichever comes first. This avoids the overhead of synchronous disk I/O
    on every packet (which would stall the reader thread at high sample rates).
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

        # Batching buffers
        self._imu_batch: list[DataRawSample] = []
        self._imu_batch_lock = threading.Lock()
        self._last_flush = time.monotonic()

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
        self._imu_batch.clear()
        self._last_flush = time.monotonic()
        self._thread = threading.Thread(target=self._run, name="DataLogger", daemon=True)
        self._thread.start()
        logger.info("DataLogger started for session %d", self._session_id)

    def stop(self, timeout: float = 5.0) -> None:
        """Signal the logger thread to stop and wait for it to finish.

        Drains any remaining packets in the queue and flushes pending batches
        before returning.
        """
        if self._thread is None:
            return
        self._stop_event.set()
        self._thread.join(timeout=timeout)
        self._thread = None
        logger.info("DataLogger stopped for session %d (shots=%d)", self._session_id, self._shot_count)

    # -------------------------------------------------------------------------
    # Batching
    # -------------------------------------------------------------------------

    def _flush_imu_batch(self) -> None:
        """Write all buffered IMU samples to storage and clear the buffer.

        Called when the batch reaches BATCH_SIZE, on timeout, or on shutdown.
        """
        batch: list[DataRawSample] = []
        with self._imu_batch_lock:
            if not self._imu_batch:
                return
            batch = self._imu_batch
            self._imu_batch = []
            self._last_flush = time.monotonic()

        try:
            self._raw_store.record_imu_batch(self._session_id, batch)
        except Exception as exc:
            logger.exception("Error writing IMU batch to raw_store: %s", exc)

        try:
            self._session_store.record_imu_batch(self._session_id, batch)
        except Exception as exc:
            logger.exception("Error writing IMU batch to session_store: %s", exc)

        logger.debug("Flushed %d IMU samples to storage", len(batch))

    def _should_flush(self) -> bool:
        """True when the batch has accumulated enough samples or timed out."""
        with self._imu_batch_lock:
            return len(self._imu_batch) >= BATCH_SIZE

    def _should_flush_timeout(self) -> bool:
        """True when BATCH_TIMEOUT seconds have elapsed since last flush."""
        return (time.monotonic() - self._last_flush) >= BATCH_TIMEOUT

    # -------------------------------------------------------------------------
    # Internal: worker loop
    # -------------------------------------------------------------------------

    def _run(self) -> None:
        """Consume packets from the queue until stop() is called."""
        while not self._stop_event.is_set():
            try:
                packet = self._queue.get(timeout=0.1)
            except Exception:
                # Check for timeout-based flush even when queue is empty
                if self._should_flush_timeout() and self._should_flush():
                    self._flush_imu_batch()
                continue

            try:
                self._route(packet)
            except Exception as exc:
                logger.exception("Error routing packet: %s", exc)

            self._queue.task_done()

            # Flush if batch is full
            if self._should_flush():
                self._flush_imu_batch()

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

        # Final flush of any remaining buffered samples
        self._flush_imu_batch()

    def _route(self, packet: Any) -> None:
        """Dispatch a packet to the appropriate storage backend."""
        if isinstance(packet, DataRawSample):
            with self._imu_batch_lock:
                self._imu_batch.append(packet)
            # Batch is flushed by _run() when size threshold is reached;
            # timeout flush is also handled there.

        elif isinstance(packet, EvtShotDetected):
            self._shot_count += 1
            try:
                self._raw_store.record_shot(self._session_id, packet)
            except Exception as exc:
                logger.exception("Error recording shot to raw_store: %s", exc)
            try:
                self._session_store.record_shot(self._session_id, packet)
            except Exception as exc:
                logger.exception("Error recording shot to session_store: %s", exc)
            self._session_store.update_shot_count(self._session_id, self._shot_count)

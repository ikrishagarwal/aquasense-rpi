"""One bounded SQLite writer; successful futures mean transaction commit."""

import asyncio
import queue
import sqlite3
import threading
from concurrent.futures import Future
from pathlib import Path
from typing import Any, Callable

from purifier.utils.async_bridge import committed
from purifier.storage.migrations import migrate


class StorageError(RuntimeError):
    pass


class Writer:
    def __init__(self, path: Path, capacity: int = 128) -> None:
        if capacity < 1:
            raise ValueError("writer capacity must be positive")
        self.path = path
        self._queue: queue.Queue[tuple[Callable[[sqlite3.Connection], Any], Future[Any]]] = queue.Queue(capacity)
        self._lock = threading.Lock()
        self._closing = threading.Event()
        self._ready: Future[None] = Future()
        self._fault = threading.Event()
        self._started = False
        self._thread = threading.Thread(target=self._run, name="sqlite-writer", daemon=True)

    @property
    def healthy(self) -> bool:
        return self._started and self._thread.is_alive() and not self._fault.is_set()

    @property
    def depth(self) -> int:
        return self._queue.qsize()

    def start(self, timeout: float = 5) -> None:
        if self._started:
            raise StorageError("writer already started")
        self._started = True
        self._thread.start()
        try:
            self._ready.result(timeout)
        except Exception:
            self._closing.set()
            raise

    async def start_async(self) -> None:
        if self._started:
            raise StorageError("writer already started")
        self._started = True
        self._thread.start()
        await committed(self._ready)

    async def close_async(self, timeout: float = 5) -> None:
        with self._lock:
            self._closing.set()
        async with asyncio.timeout(timeout):
            while self._started and self._thread.is_alive():
                await asyncio.sleep(0.05)

    def submit(self, work: Callable[[sqlite3.Connection], Any]) -> Future[Any]:
        future: Future[Any] = Future()
        with self._lock:
            if not self.healthy or self._closing.is_set():
                raise StorageError("writer unavailable")
            try:
                self._queue.put_nowait((work, future))
            except queue.Full as exc:
                self._fault.set()
                raise StorageError("critical writer queue full") from exc
        return future

    def close(self, timeout: float = 5) -> None:
        with self._lock:
            self._closing.set()
        if self._started:
            self._thread.join(timeout)
            if self._thread.is_alive():
                raise StorageError("critical writes did not finish before shutdown deadline")

    def _run(self) -> None:
        connection = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            connection = sqlite3.connect(self.path, timeout=2)
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=FULL")
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA busy_timeout=2000")
            with connection:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute("CREATE TABLE IF NOT EXISTS system_events (id INTEGER PRIMARY KEY, code TEXT NOT NULL, ts TEXT NOT NULL)")
                migrate(connection)
            self._ready.set_result(None)
            while not self._closing.is_set() or not self._queue.empty():
                try:
                    work, future = self._queue.get(timeout=0.1)
                except queue.Empty:
                    continue
                try:
                    with connection:
                        result = work(connection)
                    if not future.done():
                        future.set_result(result)
                except Exception as exc:
                    self._fault.set()
                    if not future.done():
                        future.set_exception(exc)
                finally:
                    self._queue.task_done()
        except Exception as exc:
            self._fault.set()
            if not self._ready.done():
                self._ready.set_exception(exc)
        finally:
            if connection is not None:
                connection.close()

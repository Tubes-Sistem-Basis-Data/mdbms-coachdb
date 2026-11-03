"""Buffer pool dan WAL hook (SM-12, SM-13)."""

from __future__ import annotations

import logging
import threading
from collections import OrderedDict
from dataclasses import dataclass
from typing import Callable

from common import config
from common.errors import StorageError

__all__ = ["BufferPool", "Frame"]

logger = logging.getLogger("coachdb.sm")


def _slotted_page():
    """Ambil SlottedPage dari SM2 saat dibutuhkan."""
    try:
        from storage_manager.page import SlottedPage
    except ImportError as exc:
        raise StorageError("storage_manager.page (SlottedPage) belum tersedia") from exc
    return SlottedPage


@dataclass
class Frame:
    """Satu page yang lagi ada di buffer."""

    table: str
    page_id: int
    data: bytearray
    pin_count: int = 0
    dirty: bool = False
    page_lsn: int = 0


class BufferPool:
    """Simpan page di memori dan atur flush ke disk."""

    def __init__(self, disk, capacity: int | None = None) -> None:
        """Bikin buffer pool; capacity default diambil dari config."""
        if capacity is None:
            capacity = config.BUFFER_FRAMES
        if capacity < 1:
            raise StorageError(f"kapasitas buffer tidak valid: {capacity}")
        self._disk = disk
        self._capacity = capacity
        self._log_flusher: Callable[[int], bool] | None = None
        self._frames: OrderedDict[tuple[str, int], Frame] = OrderedDict()
        self._lock = threading.RLock()
        logger.debug("buffer pool dibuat dengan %d frame", capacity)

    def set_log_flusher(self, fn: Callable[[int], bool]) -> None:
        """Pasang callback flush log dari FRM (K6). SM tidak memegang objek FRM."""
        self._log_flusher = fn
        logger.debug("buffer pool terhubung ke WAL")

    def get_page(self, table: str, page_id: int) -> Frame:
        """Ambil page dari buffer, baca dari disk kalo belum ada. Pin +1."""
        with self._lock:
            key = (table, page_id)
            frame = self._frames.get(key)
            if frame is not None:
                self._frames.move_to_end(key)
                frame.pin_count += 1
                return frame
            self._make_room()
            data = self._disk.read_page(table, page_id)
            frame = Frame(table, page_id, data, pin_count=1)
            self._frames[key] = frame
            return frame

    def new_page(self, table: str, record_size: int) -> Frame:
        """Bikin page baru di disk, masukin ke buffer, lalu pin."""
        with self._lock:
            self._make_room()
            page_id = self._disk.num_pages(table)
            data = _slotted_page().init_page(page_id, record_size)
            allocated = self._disk.allocate_page(table, data)
            if allocated != page_id:
                raise StorageError(
                    f"alokasi page {table} tidak sesuai: {allocated} != {page_id}"
                )
            frame = Frame(table, page_id, data, pin_count=1)
            self._frames[(table, page_id)] = frame
            return frame

    def unpin(self, frame: Frame, dirty: bool, lsn: int | None = None) -> None:
        """Kurangi pin satu; kalo dirty, tandai page dan simpan LSN terbesar."""
        with self._lock:
            key = (frame.table, frame.page_id)
            if self._frames.get(key) is not frame:
                raise StorageError(
                    f"frame {frame.table}:{frame.page_id} tidak ada di buffer pool"
                )
            if frame.pin_count <= 0:
                raise StorageError(f"unpin berlebih pada {frame.table}:{frame.page_id}")
            frame.pin_count -= 1
            if dirty:
                frame.dirty = True
                if lsn is not None and lsn > frame.page_lsn:
                    frame.page_lsn = lsn

    def flush_page(self, table: str, page_id: int) -> None:
        """Write page ini ke disk kalo dirty."""
        with self._lock:
            frame = self._frames.get((table, page_id))
            if frame is None or not frame.dirty:
                return
            self._write_back(frame)
            self._disk.sync(table)

    def flush_table(self, table: str) -> None:
        """Write semua page dirty tabel ini ke disk."""
        with self._lock:
            wrote = False
            for frame in self._frames.values():
                if frame.table == table and frame.dirty:
                    self._write_back(frame)
                    wrote = True
            if wrote:
                self._disk.sync(table)

    def flush_all(self) -> None:
        """Write semua page dirty ke disk."""
        with self._lock:
            wrote = False
            for frame in self._frames.values():
                if frame.dirty:
                    self._write_back(frame)
                    wrote = True
            if wrote:
                self._disk.sync()

    def discard_table(self, table: str) -> None:
        """Buang frame tabel ini tanpa write."""
        with self._lock:
            keys = [key for key in self._frames if key[0] == table]
            for key in keys:
                del self._frames[key]
            if keys:
                logger.debug("%d frame tabel %s dibuang", len(keys), table)

    def _make_room(self) -> None:
        """Keluarkan satu frame kalo buffer udah penuh."""
        if len(self._frames) >= self._capacity:
            self._evict()

    def _evict(self) -> Frame:
        """Keluarkan frame tak ter-pin paling lama; error kalo semua ter-pin."""
        with self._lock:
            for key, frame in self._frames.items():
                if frame.pin_count == 0:
                    if frame.dirty:
                        self._write_back(frame)
                    del self._frames[key]
                    logger.debug("evict page %s:%d", frame.table, frame.page_id)
                    return frame
            raise StorageError("buffer penuh")

    def _write_back(self, frame: Frame) -> None:
        """Cek log dulu, seal page, baru write ke disk."""
        if not frame.dirty:
            return
        self._flush_log_until(frame.page_lsn)
        _slotted_page().seal(frame.data)
        self._disk.write_page(frame.table, frame.page_id, bytes(frame.data))
        frame.dirty = False

    def _flush_log_until(self, page_lsn: int) -> None:
        """Minta FRM pastikan log sampai page_lsn udah aman."""
        if self._log_flusher is None:
            return
        if not self._log_flusher(page_lsn):
            raise StorageError(f"log sampai LSN {page_lsn} belum permanen")

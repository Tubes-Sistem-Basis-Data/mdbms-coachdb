from __future__ import annotations

import struct
from dataclasses import dataclass
from enum import Enum
from typing import Optional

LSN = int


class SlotStatus(Enum):
    EMPTY = 0
    LIVE = 1
    TOMBSTONE = 2

    def __repr__(self) -> str:
        return f"SlotStatus.{self.name}"

    def __str__(self) -> str:
        return self.name



@dataclass
class PageHeader:
    
    page_id: int
    page_lsn: int
    slot_count: int
    record_size: int
    checksum: int

    def __repr__(self) -> str:
        return (
            f"PageHeader("
            f"page_id={self.page_id!r}, "
            f"page_lsn={self.page_lsn!r}, "
            f"slot_count={self.slot_count!r}, "
            f"record_size={self.record_size!r}, "
            f"checksum=0x{self.checksum:016x})"
        )

    def __str__(self) -> str:
        return (
            f"Page#{self.page_id} "
            f"lsn={self.page_lsn} "
            f"slots={self.slot_count} "
            f"rsz={self.record_size}B"
        )


class SlottedPage:

    HEADER: struct.Struct = struct.Struct("<IqHHQ")

    @staticmethod
    def init_page(page_id: int, record_size: int) -> bytearray:
        raise NotImplementedError

    @staticmethod
    def read_header(buf: bytearray) -> PageHeader:
        raise NotImplementedError

    @staticmethod
    def set_page_lsn(buf: bytearray, lsn: LSN) -> None:
        raise NotImplementedError


    @staticmethod
    def seal(buf: bytearray) -> None:
        raise NotImplementedError

    @staticmethod
    def verify(buf: bytearray) -> bool:
        raise NotImplementedError

    @staticmethod
    def slot_status(buf: bytearray, slot: int) -> SlotStatus:
        raise NotImplementedError

    @staticmethod
    def read_slot(buf: bytearray, slot: int) -> Optional[bytes]:
        raise NotImplementedError

    @staticmethod
    def write_slot(buf: bytearray, slot: int, data: bytes) -> None:
        raise NotImplementedError

    @staticmethod
    def mark_tombstone(buf: bytearray, slot: int) -> None:
        raise NotImplementedError

    @staticmethod
    def first_never_used_slot(buf: bytearray) -> Optional[int]:
        raise NotImplementedError

    @staticmethod
    def capacity(record_size: int) -> int:
        raise NotImplementedError

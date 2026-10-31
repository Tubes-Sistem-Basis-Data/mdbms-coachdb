from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass
from enum import Enum
from typing import Optional

# Log Sequence Number, terhubung sebagai dependensi dari failure recovery manager.
LSN = int

class SlotStatus(Enum):
    """
    Status satu slot di dalam SlottedPage

    Arti:
        EMPTY (0): Slot belum pernah diisi
        LIVE (1): Slot berisi data aktif dan dapat dibaca
        TOMBSTONE (2): Slot telah dihapus secara soft
    """

    EMPTY = 0
    LIVE = 1
    TOMBSTONE = 2

    def __repr__(self) -> str:
        """
        @brief Representasi teknis status slot.

        @return str berupa representasi teknis
        """
        return f"SlotStatus.{self.name}"

    def __str__(self) -> str:
        """
        @brief Representasi status slot untuk pengguna

        @return str berupa representasi nama enum
        """
        return self.name


@dataclass
class PageHeader:
    """
    Header sebuah page

    Atribut:
        page_id (int): Nomor urut page dalam file tabel (0-based).
        page_lsn (int): LSN terakhir yang memodifikasi page ini.
        slot_count (int): Jumlah slot yang pernah dialokasikan (termasuk TOMBSTONE). Sama dengan indeks slot berikutnya yang akan dipakai.
        record_size (int): Ukuran tetap satu record dalam byte.
        checksum (int): CRC-32 seluruh page untuk error correction
    """

    page_id: int
    page_lsn: int
    slot_count: int
    record_size: int
    checksum: int

    def __repr__(self) -> str:
        """
        @brief Representasi teknis PageHeader untuk debugging.

        @return str berupa format representasi teknis
        """
        return (
            f"PageHeader("
            f"page_id={self.page_id!r}, "
            f"page_lsn={self.page_lsn!r}, "
            f"slot_count={self.slot_count!r}, "
            f"record_size={self.record_size!r}, "
            f"checksum=0x{self.checksum:016x}"
            f")"
        )

    def __str__(self) -> str:
        """
        @brief Representasi PageHeader untuk pengguna.

        @return str berupa format representasi
        """
        return (
            f"Page#{self.page_id} "
            f"lsn={self.page_lsn} "
            f"slots={self.slot_count}/{SlottedPage.capacity(self.record_size)} "
            f"rsz={self.record_size}B"
        )



class SlottedPage:
    """
    Kumpulan operasi low-level byte dalam level page, adalah static class yang tidak bisa dijadikan objek
    """

    @staticmethod
    def init_page(page_id: int, record_size: int) -> bytearray:
        """
        @brief Buat page baru dengan semua slot bersifat EMPTY, tetapi dengan header terisi dan valid.

        @param page_id (int) nomor page baru
        @param record_size (int) Ukuran tetap satu record dalam byte

        @return bytearray yang merepresentasikan sebuah page kosong
        @todo implement
        """
        raise NotImplementedError

    @staticmethod
    def read_header(buf: bytearray) -> PageHeader:
        """
        @brief Mendapatkan header valid dari sebuah page

        @param buf (bytearray) buffer dari page yang akan di-ekstrak header-nya

        @return PageHeader berupa header valid dari page tersebut
        @todo implement
        """
        raise NotImplementedError

    @staticmethod
    def set_page_lsn(buf: bytearray, lsn: LSN) -> None:
        """
        @brief Menulis page_lsn ke header tanpa mengubah field lain.

        @param buf (bytearray) buffer dari page yang dituju.
        @param lsn (int) Log Sequence Number operasi yang terakhir menyentuh page

        @return None
        @todo implement
        """
        raise NotImplementedError


    @staticmethod
    def _checksum_offset() -> int:
        """
        @brief Mendapatkan byte-offset field checksum di dalam header page, private method

        @return int berupa offset dalam byte
        """
        raise NotImplementedError

    @staticmethod
    def _compute_checksum(buf: bytearray) -> int:
        """
        @brief Menghitung checksum CRC-32 dari sebuah page

        @param buf (bytearray) buffer dari page yang dituju.
        @return int berupa nilai CRC-32 
        """
        raise NotImplementedError

    @staticmethod
    def seal(buf: bytearray) -> None:
        """
        @brief Menghitung dan menulis checksum sebelum page ditulis ke disk.

        @param buf (bytearray) buffer dari page yang dituju.

        @return None
        @todo implement
        """
        raise NotImplementedError

    @staticmethod
    def verify(buf: bytearray) -> bool:
        """
        @brief Verifikasi checksum valid pada sebuah page.

        @param buf (bytearray) buffer dari page yang dituju.

        @return bool berupa validitas checksum
        @todo implement
        """
        raise NotImplementedError


    @staticmethod
    def _slot_offset(record_size: int, slot: int) -> int:
        """
        @brief Menghitung byte-offset awal slot, private method

        @param record_size (int) ukuran byte data per-slot
        @param slot (int) indeks slot

        @return int berupa jumlah offset byte dari awal buffer untuk slot tersebut
        @todo implement
        """
        raise NotImplementedError


    @staticmethod
    def slot_status(buf: bytearray, slot: int) -> SlotStatus:
        """
        @brief Membaca status slot tertentu

        @param buf (bytearray) buffer dari page
        @param slot (int) indeks slot
        
        @return SlotStatus berupa status dari slot tersebut
        @todo implement
        """
        raise NotImplementedError

    @staticmethod
    def read_slot(buf: bytearray, slot: int) -> Optional[bytes]:
        """
        @brief membaca slot tertentu dari sebuah page

        @param buf (bytearray) buffer dari page
        @param slot (int) indeks slot
        
        @return bytes berupa data pada slot dari page tersebut jika bersifat LIVE, selain itu None
        @todo implement
        """
        raise NotImplementedError

    @staticmethod
    def write_slot(buf: bytearray, slot: int, data: bytes) -> None:
        """
        @brief Menulis ke slot tertentu dari sebuah page

        @param buf (bytearray) buffer dari page
        @param slot (int) indeks slot
        @param data (bytes) data yang ingin dituliskan ke slot

        @return None
        @todo implement
        """
        raise NotImplementedError

    @staticmethod
    def mark_tombstone(buf: bytearray, slot: int) -> None:
        """
        @brief Melakukan soft delete pada sebuah slot milik page (menandai sebagai TOMBSTONE)

        @param buf (bytearray) buffer dari page
        @param slot (int) indeks slot
        """
        raise NotImplementedError

    @staticmethod
    def first_never_used_slot(buf: bytearray) -> Optional[int]:
        """
        @brief Mengembalikan indeks slot pertama yang memiliki status EMPTY pada sebuah page

        @param buf (bytearray) buffer dari page

        @return int berupa indeks slot EMPTY berikutnya, atau None jika page penuh
        @todo implement
        """
        raise NotImplementedError

    @staticmethod
    def capacity(record_size: int) -> int:
        """
        @brief Mengembalikan jumlah slot maksimum per-page (mungkin kurang dari capacity keseluruhan jika ada fragmentasi)

        @param record_size (int) ukuran tetap satu record dalam byte
        
        @return int berupa jumlah slot yang dapat dimuat oleh satu page
        @todo implement
        """
        raise NotImplementedError

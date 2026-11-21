from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass
from enum import Enum
from typing import Optional

from common.config import BLOCK_SIZE
from common.errors import StorageError

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
        page_lsn (LSN): LSN terakhir yang memodifikasi page ini.
        slot_count (int): Jumlah slot yang pernah dialokasikan (termasuk TOMBSTONE). Sama dengan indeks slot berikutnya yang akan dipakai.
        record_size (int): Ukuran tetap satu record dalam byte.
        checksum (int): CRC-32 seluruh page untuk error correction
    """

    page_id: int
    page_lsn: LSN
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

    HEADER_FORMAT_STR: str          = "<IQHHI"
    HEADER_FORMAT: struct.Struct    = struct.Struct(HEADER_FORMAT_STR)
    CHECKSUM_OFFSET: int            = struct.calcsize(HEADER_FORMAT_STR[:-1]) # All element in header format except the last one (that last one is the checksum)
    CHECKSUM_SIZE: int              = struct.calcsize("<" + HEADER_FORMAT_STR[-1]) # The checksum

    # This free space pointer is after the header (4 bytes)
    FREE_SPACE_PTR_FORMAT: str      = "<H"
    FREE_SPACE_PTR_OFFSET: int      = HEADER_FORMAT.size
    FREE_SPACE_PTR_SIZE: int        = struct.calcsize(FREE_SPACE_PTR_FORMAT)

    SLOT_DIR_ENTRY_SIZE: int    = 5     # 1 byte Status + 2 bytes Offset + 2 bytes Length
    SLOT_PTR_FORMAT: str        = "<HH" # The offset and length
    DIR_START_OFFSET: int       = HEADER_FORMAT.size + FREE_SPACE_PTR_SIZE # Offset after header and free space pointer

    @staticmethod
    def init_page(page_id: int, record_size: int) -> bytearray:
        """
        @brief Buat page baru dengan semua slot bersifat EMPTY, tetapi dengan header terisi dan valid.

        @param page_id (int) nomor page baru
        @param record_size (int) Ukuran tetap satu record dalam byte

        @return bytearray yang merepresentasikan sebuah page kosong
        """
        buf = bytearray(BLOCK_SIZE)
        buf[:SlottedPage.HEADER_FORMAT.size] = SlottedPage.HEADER_FORMAT.pack(page_id, 0, 0, record_size, 0)
        struct.pack_into(SlottedPage.FREE_SPACE_PTR_FORMAT, buf, SlottedPage.FREE_SPACE_PTR_OFFSET, BLOCK_SIZE)
        return buf

    @staticmethod
    def read_header(buf: bytearray) -> PageHeader:
        """
        @brief Mendapatkan header valid dari sebuah page

        @param buf (bytearray) buffer dari page yang akan di-ekstrak header-nya

        @return PageHeader berupa header valid dari page tersebut
        """
        page_id, page_lsn, slot_count, record_size, checksum = SlottedPage.HEADER_FORMAT.unpack_from(buf)
        return PageHeader(page_id=page_id, page_lsn=page_lsn, slot_count=slot_count, record_size=record_size, checksum=checksum)

    @staticmethod
    def set_page_lsn(buf: bytearray, lsn: LSN) -> None:
        """
        @brief Menulis page_lsn ke header tanpa mengubah field lain.

        @param buf (bytearray) buffer dari page yang dituju.
        @param lsn (int) Log Sequence Number operasi yang terakhir menyentuh page

        @return None
        """
        hdr = SlottedPage.read_header(buf)
        buf[:SlottedPage.HEADER_FORMAT.size] = SlottedPage.HEADER_FORMAT.pack(hdr.page_id, lsn, hdr.slot_count, hdr.record_size, hdr.checksum)

    @staticmethod
    def _compute_checksum(buf: bytearray) -> int:
        """
        @brief Menghitung checksum CRC-32 dari sebuah page

        @param buf (bytearray) buffer dari page yang dituju.
        @return int berupa nilai CRC-32 
        """
        payload = bytes(buf[:SlottedPage.CHECKSUM_OFFSET]) + b"\x00" * SlottedPage.CHECKSUM_SIZE + bytes(buf[SlottedPage.CHECKSUM_OFFSET + SlottedPage.CHECKSUM_SIZE:])
        return zlib.crc32(payload) & 0xFFFFFFFF

    @staticmethod
    def seal(buf: bytearray) -> None:
        """
        @brief Menghitung dan menulis checksum sebelum page ditulis ke disk.

        @param buf (bytearray) buffer dari page yang dituju.

        @return None
        """
        cs = SlottedPage._compute_checksum(buf)
        struct.pack_into("<I", buf, SlottedPage.CHECKSUM_OFFSET, cs)

    @staticmethod
    def verify(buf: bytearray) -> bool:
        """
        @brief Verifikasi checksum valid pada sebuah page.

        @param buf (bytearray) buffer dari page yang dituju.

        @return bool berupa validitas checksum
        """
        hdr = SlottedPage.read_header(buf)
        return (hdr.checksum == SlottedPage._compute_checksum(buf))


    @staticmethod
    def _slot_offset(record_size: int, slot: int) -> int:
        """
        @brief Menghitung byte-offset awal slot, private method

        @param record_size (int) ukuran byte data per-slot
        @param slot (int) indeks slot

        @return int berupa jumlah offset byte dari awal buffer untuk slot tersebut
        """
        return SlottedPage.DIR_START_OFFSET + (slot * SlottedPage.SLOT_DIR_ENTRY_SIZE)


    @staticmethod
    def slot_status(buf: bytearray, slot: int) -> SlotStatus:
        """
        @brief Membaca status slot tertentu

        @param buf (bytearray) buffer dari page
        @param slot (int) indeks slot
        
        @return SlotStatus berupa status dari slot tersebut
        """
        hdr = SlottedPage.read_header(buf)
        if slot < 0 or slot >= hdr.slot_count:
            return SlotStatus.EMPTY

        off = SlottedPage._slot_offset(hdr.record_size, slot)
        return SlotStatus(buf[off])

    @staticmethod
    def read_slot(buf: bytearray, slot: int) -> Optional[bytes]:
        """
        @brief membaca slot tertentu dari sebuah page

        @param buf (bytearray) buffer dari page
        @param slot (int) indeks slot
        
        @return bytes berupa data pada slot dari page tersebut jika bersifat LIVE, selain itu None
        """
        hdr = SlottedPage.read_header(buf)
        if slot < 0 or slot >= hdr.slot_count:
            return None

        off = SlottedPage._slot_offset(hdr.record_size, slot)
        if SlotStatus(buf[off]) is not SlotStatus.LIVE:
            return None

        data_offset, data_len = struct.unpack_from(SlottedPage.SLOT_PTR_FORMAT, buf, off+1)
        return bytes(buf[data_offset : data_offset + data_len])

    @staticmethod
    def write_slot(buf: bytearray, slot: int, data: bytes) -> None:
        """
        @brief Menulis ke slot tertentu dari sebuah page

        @param buf (bytearray) buffer dari page
        @param slot (int) indeks slot
        @param data (bytes) data yang ingin dituliskan ke slot

        @return None
        @raise IndexError jika slot index tidak valid (seharusnya non-negatif)
        @raise ValueError jika ukuran space di slot yang dituju tidak cukup untuk melakukan penulisan seluruh data
        """
        if slot < 0:
            raise IndexError(f"Slot index must be non-negative (slot={slot})")

        hdr = SlottedPage.read_header(buf)
        data_len = len(data)
        off = SlottedPage._slot_offset(hdr.record_size, slot)

        # Lakukan penulisan in-place jika slot sudah bersifat live dan ukuran data baru masih lebih kecil dibanding ukuran data lama
        # selain itu maka penulisan dilakukan secara menyalin
        if slot < hdr.slot_count and SlotStatus(buf[off]) == SlotStatus.LIVE:
            old_offset, old_len = struct.unpack_from(SlottedPage.SLOT_PTR_FORMAT, buf, off + 1)
            if data_len <= old_len:
                buf[old_offset : old_offset + data_len] = data
                struct.pack_into(SlottedPage.SLOT_PTR_FORMAT, buf, off + 1, old_offset, data_len)
                return

        # Kebutuhan ruang direktori dan seluruh record yang bersifat live
        req_slot_count = max(hdr.slot_count, slot + 1)
        dir_end = SlottedPage.DIR_START_OFFSET + (req_slot_count * SlottedPage.SLOT_DIR_ENTRY_SIZE)

        total_payload = data_len
        for s in range(hdr.slot_count):
            if s == slot:
                continue
            s_off = SlottedPage._slot_offset(hdr.record_size, s)
            if SlotStatus(buf[s_off]) == SlotStatus.LIVE:
                _, d_len = struct.unpack_from(SlottedPage.SLOT_PTR_FORMAT, buf, s_off + 1)
                total_payload += d_len

        if dir_end + total_payload > len(buf):
            raise ValueError(
                f"Not enough space to write into slot (slot={slot}, dir_end={dir_end}, total_payload={total_payload}, block_size={len(buf)})"
            )

        free_space = struct.unpack_from(SlottedPage.FREE_SPACE_PTR_FORMAT, buf, SlottedPage.FREE_SPACE_PTR_OFFSET)[0]

        # Jika ruang bebas yang kontinu tidak cukup mungkin sebenarnya masih ada space tetapi terlale ter-fragmentasi, maka coba lakukan defragmentasi
        if free_space - data_len < dir_end:
            SlottedPage.defragment(buf, excluding_slot=slot if slot < hdr.slot_count else None)
            free_space = struct.unpack_from(SlottedPage.FREE_SPACE_PTR_FORMAT, buf, SlottedPage.FREE_SPACE_PTR_OFFSET)[0]

        new_free = free_space - data_len
        buf[new_free : free_space] = data

        buf[off] = SlotStatus.LIVE.value
        struct.pack_into(SlottedPage.SLOT_PTR_FORMAT, buf, off + 1, new_free, data_len)
        struct.pack_into(SlottedPage.FREE_SPACE_PTR_FORMAT, buf, SlottedPage.FREE_SPACE_PTR_OFFSET, new_free)

        if slot >= hdr.slot_count:
            buf[:SlottedPage.HEADER_FORMAT.size] = SlottedPage.HEADER_FORMAT.pack(
                hdr.page_id, hdr.page_lsn, slot + 1, hdr.record_size, hdr.checksum
            )

    @staticmethod
    def mark_tombstone(buf: bytearray, slot: int) -> None:
        """
        @brief Melakukan soft delete pada sebuah slot milik page (menandai sebagai TOMBSTONE)

        @param buf (bytearray) buffer dari page
        @param slot (int) indeks slot

        @return None
        @raise IndexError jika slot yang dituju tidak berada di range valid slot_count dari header
        """
        hdr = SlottedPage.read_header(buf)
        if slot < 0 or slot >= hdr.slot_count:
            raise IndexError(f"Slot {slot} out of range (slot_count={hdr.slot_count})")

        off = SlottedPage._slot_offset(hdr.record_size, slot)
        buf[off] = SlotStatus.TOMBSTONE.value

    @staticmethod
    def first_never_used_slot(buf: bytearray) -> Optional[int]:
        """
        @brief Mengembalikan indeks slot pertama yang memiliki status EMPTY pada sebuah page

        @param buf (bytearray) buffer dari page

        @return int berupa indeks slot EMPTY berikutnya, atau None jika page penuh
        """
        hdr = SlottedPage.read_header(buf)

        free_space = struct.unpack_from(SlottedPage.FREE_SPACE_PTR_FORMAT, buf, SlottedPage.FREE_SPACE_PTR_OFFSET)[0]
        dir_end = SlottedPage.DIR_START_OFFSET + (hdr.slot_count * SlottedPage.SLOT_DIR_ENTRY_SIZE)
        available_space = free_space - dir_end

        if available_space < SlottedPage.SLOT_DIR_ENTRY_SIZE + hdr.record_size:

            # Jika space tersisa kurang dari yang dibutuhkan, mungkin masih cukup sebenarnya tetapi terfragmentasi
            SlottedPage.defragment(buf)

            free_space = struct.unpack_from(SlottedPage.FREE_SPACE_PTR_FORMAT, buf, SlottedPage.FREE_SPACE_PTR_OFFSET)[0]
            available_space = free_space - dir_end
            if available_space < SlottedPage.SLOT_DIR_ENTRY_SIZE + hdr.record_size: 
                return None # Jika masih belum cukup maka memang benar storage habis

        return hdr.slot_count

    @staticmethod
    def capacity(record_size: int, block_size: int = BLOCK_SIZE) -> int:
        """
        @brief Mengembalikan jumlah slot maksimum per-page (mungkin kurang dari capacity keseluruhan jika ada fragmentasi)

        @param record_size (int) ukuran tetap satu record dalam byte
        @param block_size (int) ukuran satu blok dalam byte (default: BLOCK_SIZE)

        @return int berupa jumlah slot yang dapat dimuat oleh satu page
        @raise StorageError jika record_size bukan parameter valid (harus integer dan positif)
        """
        if type(record_size) is not int:
            raise StorageError(f"record_size is not a valid int type (type={type(record_size)})")

        if record_size <= 0:
            raise StorageError(f"record_size must be positive (record_size={record_size})")

        usable_size = block_size - SlottedPage.DIR_START_OFFSET
        return max(0, usable_size // (SlottedPage.SLOT_DIR_ENTRY_SIZE + record_size))


    @staticmethod
    def defragment(buf: bytearray, excluding_slot: Optional[int] = None) -> None:
        """
        @brief Melakukan defragmentasi page dengan memadatkan seluruh record yang masih bersifat live ke batas akhir buffer. Fragmentasi banyak terjadi saat soft delete.

        @param buf (bytearray) buffer dari page
        @param excluding_slot (Optional[int]) slot yang datanya diabaikan dari proses defragmentasi (misalnya kalo ada heuristik akan segera ditimpa)

        @return None
        """
        hdr = SlottedPage.read_header(buf)
        records: list[tuple[int, int, bytes]] = []

        for s in range(hdr.slot_count):
            if s == excluding_slot:
                continue

            s_off = SlottedPage._slot_offset(hdr.record_size, s)
            if SlotStatus(buf[s_off]) == SlotStatus.LIVE: # Ambil jika live, untuk didefragmentasi
                d_off, d_len = struct.unpack_from(SlottedPage.SLOT_PTR_FORMAT, buf, s_off + 1)
                records.append((s, d_off, bytes(buf[d_off : d_off + d_len])))

        # Defragmentasi diurutkan berdasarkan data_offset awal menurun
        records.sort(key=lambda item: item[1], reverse=True)

        # Lakukan defragmentasi dengan proses menulis data dari awal page seperti membuat linked-list 
        curr_ptr = len(buf)
        for s, _, data in records:
            curr_ptr -= len(data)
            buf[curr_ptr : curr_ptr + len(data)] = data
            s_off = SlottedPage._slot_offset(hdr.record_size, s)
            struct.pack_into(SlottedPage.SLOT_PTR_FORMAT, buf, s_off + 1, curr_ptr, len(data))

        struct.pack_into(SlottedPage.FREE_SPACE_PTR_FORMAT, buf, SlottedPage.FREE_SPACE_PTR_OFFSET, curr_ptr)

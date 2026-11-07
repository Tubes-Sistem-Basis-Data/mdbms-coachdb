from __future__ import annotations

import os
from typing import BinaryIO, Optional

from common.config import DATA_DIR, BLOCK_SIZE
from common.errors import StorageError


class DiskManager:
    """
    @brief Manajer disk I/O tingkat paling rendah, tugasnya langsung copy byte secara low-level

    Akan merepresentasikan tabel melalui file .db di dalam sebuah data direktori (data_dir).

    Atribut:
    - data_dir  : direktori tempat semua file .db disimpan.
    - files     : dictionary dari nama tabel ke file handle yang terbuka.
    - closed    : boolean menandakan apakah semua file tabel sudah ditutup atau belum
    """

    def __init__(self, data_dir: str = DATA_DIR) -> None:
        """
        @brief Inisialisasi kelas DiskManager 

        @param data_dir Path direktori penyimpanan file tabel (Default: ``DATA_DIR``)
        @return None
        """
        self._data_dir: str = data_dir
        self._files: dict[str, BinaryIO] = {}
        self._closed: bool = False
        os.makedirs(self._data_dir, exist_ok=True)

    def __repr__(self) -> str:
        """
        @brief representasi teknis objek untuk debugging.

        @return str yang merupakan format representasi objek
        """
        return (
            f"DiskManager("
            f"data_dir={self._data_dir!r}, "
            f"tables={sorted(self._files)!r})"
        )

    def __str__(self) -> str:
        """
        @brief representasi teknis objek untuk digunakan.

        @return str yang merupakan format representasi objek
        """
        return f"DiskManager[{len(self._files)} table(s) @ {self._data_dir}]"


    def _get_file(self, table: str) -> BinaryIO:
        """
        @brief Mengambil atau membuka secara lazy file handle tabel yang dituju

        @param table (str) nama tabel

        @return BinaryIO file handle
        @raise KeyError jika DiskManager sudah ditutup atau file tabel tidak ditemukan
        """
        if self._closed:
            KeyError(f"DiskManager is closed (table='{table}')")

        if table not in self._files:
            path = os.path.join(self._data_dir, f"{table}.db")
            if os.path.exists(path):
                self._files[table] = open(path, "r+b")
            else:
                raise KeyError(f"Table file for '{table}' not found in '{self._data_dir}'")

        return self._files[table]


    def create_table_file(self, table: str) -> None:
        """
        @brief Membuat file biner untuk tabel baru dan lansung membuat handle file tersebut
        @param table (str) nama tabel yang akan dibuat

        @return None
        """
        if self._closed:
            raise KeyError(f"DiskManager is closed (table='{table}')")

        path = os.path.join(self._data_dir, f"{table}.db")
        if not os.path.exists(path):
            with open(path, "wb"):
                pass
        if table in self._files:
            try:
                self._files[table].close()
            except OSError:
                pass
        self._files[table] = open(path, "r+b")

    def read_page(self, table: str, page_id: int) -> bytearray:
        """
        @brief Membaca satu page dari file tabel dan kembalikan sebagai objek bytearray.

        @param table (str) nama tabel yang ingin dibaca
        @param page_id (int) nomor ID page

        @return bytearray yang merupakan data bytes mentah dari isi page
        @raise StorageError jika page tidak ada di tabel file atau hasil pembacaan tidak align dengan BLOCK_SIZE
        """
        fh = self._get_file(table)
        total_pages = self.num_pages(table)
        if page_id < 0 or page_id >= total_pages:
            raise StorageError(f"Page {page_id} does not exist in table '{table}' (total pages: {total_pages})")

        fh.seek(page_id * BLOCK_SIZE)
        raw = fh.read(BLOCK_SIZE)
        if len(raw) != BLOCK_SIZE:
            raise StorageError(f"Incomplete page read from table '{table}' at page {page_id}")

        return bytearray(raw)


    def write_page(self, table: str, page_id: int, data: bytes) -> None:
        """
        @brief Menulis ke satu page sebuah tabel

        @param table (str) nama tabel yang ingin dituju
        @param page_id (int) nomor ID page
        @param data (bytes) bytes yang akan dituliskan  

        @return None
        @raise ValueError jika ukuran data tidak align dengan BLOCK_SIZE
        """
        if len(data) != BLOCK_SIZE:
            raise ValueError(f"Data size ({len(data)} bytes) does not align with BLOCK_SIZE ({BLOCK_SIZE} bytes)")

        fh = self._get_file(table)
        fh.seek(page_id * BLOCK_SIZE)
        fh.write(data)


    def allocate_page(self, table: str, data: bytes) -> int:
        """
        @brief Menambahkan page baru di akhir file tabel dan mengembalikan page_id milik page tersebut

        @param table (str) nama tabel tujuan
        @param data (bytes) data untuk page baru

        @return int yang berupa page_id milik page baru
        @raise ValueError jika ukuran data tidak align dengan BLOCK_SIZE
        @raise KeyError jika disk manager sudah menutup file tetapi tetap dilakukan operasi ini
        """
        if len(data) != BLOCK_SIZE:
            raise ValueError(f"Data size ({len(data)} bytes) does not align with BLOCK_SIZE ({BLOCK_SIZE} bytes)")

        if self._closed:
            raise KeyError(f"DiskManager is closed (table='{table}')")

        path = os.path.join(self._data_dir, f"{table}.db")
        if table not in self._files and not os.path.exists(path):
            self.create_table_file(table)

        fh = self._get_file(table)
        new_page_id = self.num_pages(table)
        fh.seek(0, 2)
        fh.write(data)
        return new_page_id


    def num_pages(self, table: str) -> int:
        """
        @brief Mengembalikan jumlah page yang ada di sebuah tabel

        @param table (str) nama tabel yang dituju

        @return int berupa jumlah page pada tabel tersebut
        @raise KeyError jika disk manager sudah menutup file tetapi tetap dilakukan operasi ini
        """
        if self._closed:
            raise KeyError(f"DiskManager is closed (table='{table}')")

        if table not in self._files:
            path = os.path.join(self._data_dir, f"{table}.db")
            if os.path.exists(path):
                fh = self._get_file(table)
            else:
                return 0
        else:
            fh = self._files[table]

        fh.seek(0, 2)
        size = fh.tell()
        return size // BLOCK_SIZE


    def sync(self, table: Optional[str] = None) -> None:
        """
        @brief Melakukan flush buffer ke disk, umumnya write yang dilakukan pasti buffered sehingga tidak langsung tertulis ke disk

        @param table (None/str) nama tabel tujuan, jika None maka akan flus semua file tabel

        @return None
        @raise KeyError jika disk manager sudah menutup file tetapi tetap dilakukan operasi ini
        """
        if self._closed:
            msg = f"DiskManager is closed (table=ALL)"
            if table != None:
                msg = f"DiskManager is closed (table='{table}')"
            raise KeyError(msg)

        if table is not None:
            fh = self._files.get(table)
            if fh is not None:
                fh.flush()
                os.fsync(fh.fileno())
        else:
            for fh in self._files.values():
                fh.flush()
                os.fsync(fh.fileno())


    def close(self) -> None:
        """
        @brief Menutup semua handle file milik tabel

        @return None
        """
        self._closed = True
        for fh in self._files.values():
            try:
                fh.close()
            except OSError:
                pass
        self._files.clear()
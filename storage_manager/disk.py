from __future__ import annotations

import os
from typing import BinaryIO, Optional

from common.config import DATA_DIR


class DiskManager:
    """
    @brief Manajer disk I/O tingkat paling rendah, tugasnya langsung copy byte secara low-level

    Akan merepresentasikan tabel melalui file .db di dalam sebuah data direktori (data_dir).

    Atribut:
    - data_dir  : direktori tempat semua file .db disimpan.
    - files     : dictionary dari nama tabel ke file handle yang terbuka.
    """

    def __init__(self, data_dir: str = DATA_DIR) -> None:
        """
        @brief Inisialisasi kelas DiskManager 

        @param data_dir Path direktori penyimpanan file tabel (Default: ``DATA_DIR``)
        @return None
        """
        self._data_dir: str = data_dir
        self._files: dict[str, BinaryIO] = {}


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


    def create_table_file(self, table: str) -> None:
        """
        @brief Membuat file biner untuk tabel baru dan lansung membuat handle file tersebut
        @param table (str) nama tabel yang akan dibuat

        @return None
        @todo implement
        """
        raise NotImplementedError

    def read_page(self, table: str, page_id: int) -> bytearray:
        """
        @brief Membaca satu page dari file tabel dan kembalikan sebagai objek bytearray.

        @param table (str) nama tabel yang ingin dibaca
        @param page_id (int) nomor ID page

        @return bytearray yang merupakan data bytes mentah dari isi page
        @todo implement
        """
        raise NotImplementedError


    def write_page(self, table: str, page_id: int, data: bytes) -> None:
        """
        @brief Menulis ke satu page sebuah tabel

        @param table (str) nama tabel yang ingin dituju
        @param page_id (int) nomor ID page
        @param data (bytes) bytes yang akan dituliskan  

        @return None
        @todo implement
        """
        raise NotImplementedError


    def allocate_page(self, table: str, data: bytes) -> int:
        """
        @brief Menambahkan page baru di akhir file tabel dan mengembalikan page_id milik page tersebut

        @param table (str) nama tabel tujuan
        @param data (bytes) data untuk page baru

        @return int yang berupa page_id milik page baru
        @todo implement
        """
        raise NotImplementedError


    def num_pages(self, table: str) -> int:
        """
        @brief Mengembalikan jumlah page yang ada di sebuah tabel

        @param table (str) nama tabel yang dituju

        @return int berupa jumlah page pada tabel tersebut
        @todo implement
        """
        raise NotImplementedError


    def sync(self, table: Optional[str] = None) -> None:
        """
        @brief Melakukan flush buffer ke disk, umumnya write yang dilakukan pasti buffered sehingga tidak langsung tertulis ke disk

        @param table (None/str) nama tabel tujuan, jika None maka akan flus semua file tabel

        @return None
        @todo implement
        """
        raise NotImplementedError


    def close(self) -> None:
        """
        @brief Menutup semua handle file milik tabel

        @return None
        @todo implement
        """
        raise NotImplementedError

# Storage Manager (SM)

Implementasi oleh group SM (Group 1, Rust).

Interface: `StorageManager` di `common/interfaces/storage_manager.py`. Facade: `StorageManager` di `storage_manager/facade.py`. Selama implementasi asli belum ada, komponen lain memakai `MockStorageManager` di `mocks/mock_storage_manager.py`. Tipe internal SM (`Value`, `LSN`) ada di `storage_manager/types.py`, error internal di `storage_manager/errors.py`.

## Requirement

| Kode | Judul | PIC |
| --- | --- | --- |
| SM-01 | Menyimpan Data Secara Permanen di Disk | SM2 - Fatih |
| SM-02 | Menyimpan Tipe Data Integer, Float, Char, dan Varchar | SM3 - Syaqina |
| SM-03 | Menyimpan Record dalam Format Internal | SM3 - Syaqina |
| SM-04 | Mengambil Data Tabel | SM1 - Kloce |
| SM-05 | Menambah Data | SM1 - Kloce |
| SM-06 | Mengubah Data | SM1 - Kloce |
| SM-07 | Menghapus Data | SM1 - Kloce |
| SM-08 | Mengetahui Struktur Tabel | SM4 - Azri |
| SM-09 | Mengetahui Statistik Tabel | SM4 - Azri |
| SM-10 | Membuat Index (B+Tree/Hash) | SM5 - Vara |
| SM-11 | Mencari Data Lewat Index | SM5 - Vara |
| SM-12 | Mempercepat Akses Data dengan Buffer | SM2b - Ariel |
| SM-13 | Menulis Perubahan ke Disk | SM2b - Ariel |
| SM-14 | Menyediakan Basis Data Contoh | SM4 - Azri |
| SM-15 | Driver Program Komponen | SM1 - Kloce |
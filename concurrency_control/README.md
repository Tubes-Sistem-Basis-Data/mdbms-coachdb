# Concurrency Control Manager (CCM)

Implementasi oleh group CCM (Group 4).

Facade: `ConcurrencyControlManager` di `common/interfaces/concurrency_control.py`. Selama implementasi asli belum ada, komponen lain memakai `mocks/mock_concurrency_control.py`.

## Requirement

| Kode | Judul |
| --- | --- |
| CC-01 | Memulai dan Menyelesaikan Transaksi |
| CC-02 | Mengganti Protokol Concurrency Control |
| CC-03 | Meneruskan Catatan Transaksi ke FRM sesuai WAL |
| CC-04 | Mengoordinasikan Pembatalan Transaksi |
| CC-05 | Inisialisasi CCM setelah Pemulihan Sistem |
| CC-06 | Mengatur Akses Data dengan Penguncian |
| CC-07 | Menangani Deadlock |
| CC-08 | Mengatur Akses Data Berdasarkan Timestamp |
| CC-09 | Memverifikasi Serializability Hasil Eksekusi |
| CC-10 | Driver Program Komponen |

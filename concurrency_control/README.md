# Concurrency Control Manager (CCM)

Implementasi oleh group CCM (Group 4).

Facade: `ConcurrencyControlManager` di `common/interfaces/concurrency_control.py`.

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

## Demo driver CCM

Jalankan `python -m concurrency_control.driver` dari root repo. Contoh bawaan menguji
Strict 2PL, konflik tanpa siklus, deadlock dua dan tiga transaksi, upgrade lock,
serta abort eksplisit. Driver membatasi setiap skenario hingga lima detik.

Untuk skenario dari berkas, pakai `--file skenario.txt`. `expect` memuat respons
pertama setiap READ dan WRITE. Retry setelah `WAIT` berjalan setiap 50 ms.
Tutup transaksi dengan `COMMIT`, `ABORT` untuk membatalkan secara eksplisit,
atau `ABORTED` untuk memeriksa pembatalan otomatis oleh CCM.

CCM memakai wait-for graph dan memilih ID transaksi terbesar dalam siklus
sebagai korban. Korban menerima `ABORTED` setelah rollback pada permintaan akses
yang mendeteksi deadlock atau pada retry korban. Lock data korban tetap ditahan
sampai FRM menyelesaikan rollback dan flush log abort.

`--protocol timestamp` menjalankan contoh CC-08 melalui facade. Contoh konflik
akan gagal selama `TimestampProtocol` masih stub. Driver menerima pemeriksa
CC-09 untuk jadwal transaksi yang commit. Tanpa pemeriksa, output menampilkan
`BELUM DIPERIKSA`.

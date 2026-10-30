# Query Optimizer (QO)

Implementasi oleh group QO (Group 3).

Facade: `QueryOptimizer` di `common/interfaces/query_optimizer.py`. Selama implementasi asli belum ada, komponen lain memakai `mocks/mock_query_optimizer.py`.

## Requirement

| Kode | Judul |
| --- | --- |
| QO-01 | Tokenisasi dan Penanganan Kesalahan Sintaks |
| QO-02 | Mengenali Query SELECT |
| QO-03 | Mengenali Penggabungan Tabel |
| QO-04 | Mengenali Ekspresi dan Kondisi |
| QO-05 | Mengenali Query Perubahan Data |
| QO-06 | Mengenali Perintah Transaksi dan Indeks |
| QO-07 | Memvalidasi Query terhadap Katalog |
| QO-08 | Mengestimasi Biaya Query Plan |
| QO-09 | Mengoptimasi Seleksi |
| QO-10 | Mengoptimasi Proyeksi |
| QO-11 | Mengubah Cartesian Product menjadi Join |
| QO-12 | Menukar Urutan dan Pengelompokan Join |
| QO-13 | Memanfaatkan Index (Index Scan) |
| QO-14 | Memilih Query Plan Terbaik |
| QO-15 | Menampilkan Query Plan sebelum dan sesudah Optimasi |
| QO-16 | Driver Program Komponen |
| QO-17* | Fitur Ekstra Parsing (CREATE/DROP TABLE, LIMIT, AS, Subquery) |
| QO-18* | Optimasi dengan Genetic Algorithm |

Kode bertanda `*` adalah bonus.

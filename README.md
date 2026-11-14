# CoachDB

Mini DBMS relasional untuk Tugas Besar **IF3140 Sistem Basis Data 2026**, dikerjakan oleh supergroup CoachDB.

Python 3.10 atau lebih baru, hanya library standar.

**Status:** Milestone 3. Kelima komponen sudah memakai implementasi asli dan dirangkai oleh `main.py`. Setiap komponen punya driver program untuk pengujian mandiri.

## Komponen

| Komponen | Group | Folder | Facade |
| --- | --- | --- | --- |
| Storage Manager (SM) | Group 1 | `storage_manager/` | `StorageManager` |
| Failure Recovery Manager (FRM) | Group 2 | `failure_recovery/` | `FailureRecoveryManager` |
| Query Optimizer (QO) | Group 3 | `query_optimizer/` | `QueryOptimizer` |
| Concurrency Control Manager (CCM) | Group 4 | `concurrency_control/` | `ConcurrencyControlManager` |
| Query Processor (QP) | Group 5 | `query_processor/` | `QueryProcessor` |

Daftar requirement tiap komponen ada di `README.md` folder komponennya.

## Anggota

**Ketua supergroup:** Angelina Andra Alanna (13524079, Group 3)

### Group 1 - Storage Manager

| NIM | Nama | Role |
| --- | --- | --- |
| 13524040 | Kloce Paul William Saragih | SM1 (usulan) |
| 13524004 | Muhammad Fatih Irkham Mauludi | SM2 (usulan) |
| 13524085 | Ariel Cornelius Sitorus | SM2b (usulan) |
| 13524088 | Syaqina Octavia Rizha | SM3 (usulan) |
| 13524139 | Azri Arzaq Pohan | SM4 (usulan) |
| 13524091 | Vara Azzara Ramli Pulukadang | SM5 (usulan) |

### Group 2 - Failure Recovery Manager

| NIM | Nama | Role |
| --- | --- | --- |
| 13524058 | Muhammad Naufal Romi Annafi | FR1 |
| 13524028 | Muhammad Nur Majiid | FR2 |
| 13524055 | Junior Natra Situmorang | FR3 |
| 13524016 | Wafiq Hibban Robbany | FR4 |
| 13524031 | Vincent Rionarlie | FR5 |

### Group 3 - Query Optimizer

| NIM | Nama | Role |
| --- | --- | --- |
| 13524079 | Angelina Andra Alanna | QO1 |
| 13524049 | Arina Azka | QO2 |
| 13524013 | Anindya Naufal Pinasthika | QO3 |
| 13524097 | Muhammad Faiz Alfada Dharma | QO4 |
| 13524145 | Dzakwan Muhammad K. P. P. (ketua group) | QO5 |

### Group 4 - Concurrency Control Manager

| NIM | Nama | Role |
| --- | --- | --- |
| 13524112 | Richard Samuel Simanullang | CC1 |
| 13524127 | Fazri Arrashyi Putra | CC2 |
| 13523037 | Buege Mahara Putra | CC3 |
| 13524121 | Billy Ontoseno Irawan | CC4 |
| 13524100 | Arghawisesa Dwinanda Arham | CC5 |

### Group 5 - Query Processor

| NIM | Nama | Role |
| --- | --- | --- |
| 13524019 | Kevin Wirya Valerian | QP1 |
| 13524043 | Emilio Justin | QP2 |
| 13523151 | Ardell Aghna Mahendra | QP3 |
| 13524025 | Moh. Hafizh Irham Perdana | QP4 |
| 13524007 | Muhammad Ashkar | QP5 |

## Menjalankan

```bash
git clone https://github.com/Tubes-Sistem-Basis-Data/mdbms-coachdb.git
cd mdbms-coachdb

python main.py --demo                 # jalankan query contoh lalu keluar
python main.py --cli                  # REPL CLI interaktif lokal
python main.py --serve                # server TCP multi-client
python client.py                      # CLI client ke server (--host, --port)
python -m unittest discover -s tests  # jalankan semua test
```

Opsi `main.py`:

| Opsi | Keterangan | Default |
| --- | --- | --- |
| `--protocol lock\|timestamp` | protokol concurrency control | `lock` |
| `--data-dir <path>` | direktori data | `data` |
| `--host`, `--port` | alamat server TCP | `127.0.0.1`, `5440` |

Data disimpan di `--data-dir`. Pada direktori data yang masih kosong belum ada tabel, jadi buat tabel dan isi datanya dulu (`CREATE TABLE`, `INSERT`) sebelum menjalankan query lain.

### Driver komponen

```bash
python -m drivers.qp_driver
python -m drivers.qo_driver --no-catalog
python -m drivers.sm_driver
python -m concurrency_control.driver --protocol lock   # atau timestamp
python -m drivers.frm_driver
```

`drivers.qo_driver` tanpa `--no-catalog` membaca skema dan statistik dari Storage Manager di `--data-dir`, sehingga tabel contoh harus sudah ada. Dengan `--no-catalog`, driver memakai katalog contoh in-memory (`student`, `course`, `enrollment`) supaya QO bisa diuji terpisah dari Storage Manager.

## Struktur

```
mdbms-coachdb/
├── main.py            # bootstrap semua komponen
├── client.py          # CLI client
├── common/            # kontrak: shared data type dan interface facade
├── query_processor/   # Group 5
├── query_optimizer/   # Group 3
├── storage_manager/   # Group 1
├── concurrency_control/  # Group 4
├── failure_recovery/  # Group 2
├── drivers/           # driver program komponen
├── scripts/           # skrip pendukung (fixture data contoh)
├── tests/             # test kontrak dan unit test komponen
└── docs/              # keputusan desain dan riwayat perubahan kontrak
```

## Kontrak

- Shared data type ada di `common/`, bisa di-import langsung (`from common import RID, Row`).
- Interface facade ada di `common/interfaces/`.
- Komponen lain hanya boleh di-import lewat `common.interfaces`.
- Perubahan kontrak harus disetujui semua group dan dicatat di [`docs/changelog-kontrak.md`](docs/changelog-kontrak.md). Keputusan desain ada di [`docs/decisions.md`](docs/decisions.md).

## Aturan kontribusi

Detail lengkap ada di [`CONTRIBUTING.md`](CONTRIBUTING.md).

- Branch dibuat dari `main` dan masuk kembali ke `main` lewat PR. `main` diproteksi, tidak bisa push langsung.
- Nama branch: `<type>/<scope>/<deskripsi>`, contoh `feat/qo/qo-04-expression-parser`.
- Pesan commit dan judul PR: `<type>(<scope>): <deskripsi>`, contoh `feat(qo): tambah parser ekspresi`.
- Format di atas dicek otomatis oleh GitHub Actions (`Conventions`), begitu juga unit test (`Tests`).
- Jangan mengubah folder milik group lain.
- Hanya library standar Python. Unit test memakai `unittest`, bukan `pytest`.
- `python -m unittest discover -s tests` harus lulus sebelum PR di-merge.

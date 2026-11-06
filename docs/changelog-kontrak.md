# Changelog Kontrak

Setiap perubahan pada `common/` atau `common/interfaces/` dicatat di sini: tanggal, perubahan, alasan, dan group yang menyetujui.

## 25 Oktober 2026

### Versi awal

- Shared data type di `common/` (catalog, storage, expression, plan, transaction, log, result, errors) dan konfigurasi default di `common/config.py`.
- ABC kelima facade di `common/interfaces/`: `QueryProcessor` (+ `Session`), `QueryOptimizer`, `StorageManager`, `ConcurrencyControlManager`, `FailureRecoveryManager`.
- Facade tiap komponen berupa mock (semua method mengembalikan tipe sesuai kontrak) dan uji kontrak di `tests/test_contract.py`.

### Tambahan terhadap draf kontrak

| Perubahan | Alasan | Terdampak |
| --- | --- | --- |
| `ConcurrencyControlManager.get_protocol() -> ProtocolType` | `main.py` mencetak protokol aktif saat startup. | CCM, main.py |
| `FailureRecoveryManager.set_dependencies(sm, ccm)` | FRM butuh SM (undo/redo, flush) dan CCM (checkpoint), padahal CCM dibuat dengan FRM. Disuntik setelah ketiganya dibuat. | FRM, main.py |
| `QueryKind.EXPLAIN` | Perintah `EXPLAIN <query>;` dikenali QO lalu diteruskan QP ke `QO.explain`. | QO, QP |
| Parameter `lsn: int \| None` di `SM.insert/update/delete/delete_all` | FRM memanggil operasi undo/redo tanpa LSN baru (`lsn=None`). | SM, FRM |

## 5 November 2026

### Usulan SM dan FRM yang diterima

Berdasarkan tinjauan usulan perubahan kontrak SM dan FRM (4 November 2026). Semua perubahan hanya menambah, kode yang memakai kontrak 25 Oktober tetap jalan.

| Perubahan | Alasan | Terdampak | Disetujui |
| --- | --- | --- | --- |
| `LogRecord.prev_lsn: int = -1` (field terakhir) | Rollback bisa menelusuri log satu transaksi tanpa memindai seluruh log. Diisi FRM di `write_log`, CCM tidak perlu mengisi. | FRM | (menunggu) |
| `LogKind.RESTORE` | FRM mencatat point-in-time recovery. | FRM | (menunggu) |
| `LogWriteError(LogError)`, `RecoveryError(MdbmsError)` | QP tetap cukup menangkap `MdbmsError`; `main.py` bisa memberi pesan lebih jelas saat recovery gagal. | FRM, main.py | (menunggu) |
| `TableNotFoundError`, `IndexDefinitionError` (turunan `SemanticError`), `SchemaViolationError` (turunan `StorageError`) | Pindah dari `storage_manager/errors.py` ke `common/errors.py` supaya binder QO bisa membedakan tabel tidak ada dari error lain. `storage_manager/errors.py` sekarang hanya re-export. | SM, QO, QP | (menunggu) |
| Alias `Value = int \| float \| str \| None` di `common/storage.py` | Satu definisi nilai kolom untuk semua komponen. | semua (opsional dipakai) | (menunggu) |

Usulan berstatus diterima dengan syarat (`snapshot_to`/`restore_from` dan `rebuild_indexes` di interface SM) belum dimasukkan, menunggu kesepakatan SM dan FRM.

### Tipe untuk requirement bonus (QO-17, QP-15, QP-16)

| Perubahan | Alasan | Terdampak | Disetujui |
| --- | --- | --- | --- |
| `QueryKind.CREATE_TABLE`, `QueryKind.DROP_TABLE` | Jenis perintah DDL. | QO, QP | (menunggu) |
| Node `CreateTable(schema: TableSchema)` dan `DropTable(table: str)` | Plan untuk `CREATE TABLE` dan `DROP TABLE`. | QO, QP | (menunggu) |
| Node `Limit(count: int)` | Plan untuk `LIMIT <n>`, membungkus anak paling atas. | QO, QP | (menunggu) |
| `TableScan.alias: str \| None = None` | Alias tabel `FROM <tabel> [AS] <alias>`. | QO, QP | (menunggu) |
| `Project.aliases: list[str \| None] = []` | Alias kolom `SELECT <kolom> AS <alias>`, sejajar dengan `columns`. | QO, QP | (menunggu) |
| Node `SubqueryScan(alias: str)`, plan subquery di `children[0]` | Subquery di FROM. | QO, QP | (menunggu) |
| Ekspresi `Subquery(plan: PlanNode)` | Subquery skalar di kondisi, mis. `WHERE ipk > (SELECT ...)`. | QO, QP | (menunggu) |
| Method abstrak `SM.create_table(schema)` dan `SM.drop_table(table)` | QP butuh jalur ke katalog SM untuk DDL. Facade SM dan stub di test QP ikut diperbarui. | SM, QP | (menunggu) |

### Hasil review PR kontrak

| Perubahan | Alasan | Terdampak | Disetujui |
| --- | --- | --- | --- |
| Method abstrak `SM.validate(table, values, rid: RID \| None = None) -> list` | Sebelumnya hanya method tambahan di facade SM, padahal QP-10/QP-11 sudah memanggilnya. Raise `SchemaViolationError`. `rid` diisi saat UPDATE supaya baris yang diubah tidak bentrok dengan dirinya sendiri saat cek primary key. | SM, QP | (menunggu) |
| Docstring `ColumnRef`: `table` berisi alias jika sumbernya punya alias | Aturan bersama untuk binder QO dan executor QP. | QO, QP | (menunggu) |
| Docstring `Subquery`: hanya subquery tak-berkorelasi | Batas lingkup bonus subquery. | QO, QP | (menunggu) |
| Docstring `LogRecord.prev_lsn`: catatan dengan `tx_id=None` selalu -1 | Catatan global tidak punya rantai transaksi. | FRM | (menunggu) |
| Docstring `FRM.recover`/`recover_to`: gagal dikembalikan sebagai `ok=False`; `recover_to` menulis `RESTORE` global lalu flush dan checkpoint | `main.py` mengecek `result.ok`. Checkpoint setelah restore mencegah redo catatan sebelum titik restore tanpa field baru di `LogRecord`. | FRM, main.py | (menunggu) |

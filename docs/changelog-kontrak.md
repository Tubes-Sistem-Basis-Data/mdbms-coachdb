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

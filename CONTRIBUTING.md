# Panduan Kontribusi

Branch `main` diproteksi. Tidak ada yang boleh push langsung ke `main`; semua perubahan masuk lewat Pull Request (PR).

## Alur kerja

1. Sinkronkan `main`, lalu buat branch baru dari `main`:
   ```bash
   git switch main
   git pull
   git switch -c feat/qo/qo-04-expression-parser
   ```
2. Commit perubahan mengikuti konvensi di bawah, lalu push branch:
   ```bash
   git add <file>
   git commit -m "feat(qo): tambah parser ekspresi dan kondisi"
   git push -u origin feat/qo/qo-04-expression-parser
   ```
3. Buka PR ke `main`. Judul PR memakai format yang sama dengan commit. Isi template PR.
4. Hanya anggota **@Tubes-Sistem-Basis-Data/coachdb-group-leads** (ketua supergroup dan ketua tiap group) yang bisa merge ke `main`. PR siap di-merge kalau:
   - check **Conventions** dan **Tests** lulus,
   - ada minimal satu approval dari pemilik folder yang diubah (lihat `.github/CODEOWNERS`),
   - semua percakapan review sudah resolved.

   Push commit baru setelah approval akan membatalkan approval tersebut, jadi PR harus di-approve ulang.

   Ketua group boleh melewati syarat ini saat merge (misalnya untuk PR miliknya sendiri). Push langsung ke `main` tetap diblokir untuk semua orang.
5. Setelah PR di-merge, hapus branch-nya dan kembali ke langkah 1 untuk fitur berikutnya.

## Konvensi

**Type:** `feat`, `fix`, `docs`, `test`, `refactor`, `perf`, `ci`, `chore`

**Scope:** `qp` (Query Processor), `qo` (Query Optimizer), `sm` (Storage Manager), `ccm` (Concurrency Control Manager), `frm` (Failure Recovery Manager), `global` (lintas komponen atau seluruh repo)

| | Format | Contoh |
|---|---|---|
| Nama branch | `<type>/<scope>/<deskripsi-singkat>` (kebab-case, huruf kecil) | `feat/sm/sm-10-bplus-tree`, `fix/sm/buffer-flush` |
| Pesan commit & judul PR | `<type>(<scope>): <deskripsi>` (scope opsional) | `feat(qo): tambah join reordering` |

- Kalau branch mengerjakan fitur spek, awali deskripsi dengan kode fiturnya: `feat/ccm/cc-06-strict-2pl`, `feat/frm/fr-01-wal-writer`.
- Satu branch untuk satu fitur. Fitur besar dipecah: `feat/qo/qo-12-swap-sides`, lalu `feat/qo/qo-12-associative`.
- Jangan pakai nama orang atau nama kelompok di nama branch.
- Perubahan kontrak (`common/`, `tests/test_contract.py`) memakai scope `global`, misalnya `feat/global/tambah-field-tablestats`, dan wajib disetujui satu orang dari setiap group terdampak serta dicatat di `docs/changelog-kontrak.md`.
- Commit `Merge ...` dan `Revert ...` buatan git tetap diterima.

## Menjaga branch tetap mutakhir

Tarik `main` terbaru ke branch fitur minimal sekali sehari supaya konflik tetap kecil:

```bash
git switch main
git pull
git switch feat/qo/qo-04-expression-parser
git merge main
```

## Memindahkan branch lama

Branch dengan pola lama (`qo/qo-04-...`, `group/qo`) atau commit berformat `[QO-04] ...` akan ditolak check **Conventions**. Pindahkan isinya ke branch baru dalam satu commit:

```bash
git fetch origin
git switch main
git pull
git switch -c feat/qo/qo-04-expression-parser
git merge --squash origin/qo/qo-04-expression-parser
git commit -m "feat(qo): tambah parser ekspresi dan kondisi"
git push -u origin feat/qo/qo-04-expression-parser
```

Kalau commit di branch yang sudah sesuai pola ternyata salah format, perbaiki lalu push ulang:

```bash
git commit --amend -m "fix(sm): perbaiki flush buffer"   # commit terakhir saja
git push --force-with-lease
```

## Aturan proyek

- Hanya ubah kode di folder komponen milik group sendiri. Koordinasikan dulu dengan group pemilik sebelum mengubah komponen lain.
- Hanya library standar Python. Library yang mengimplementasikan fungsi inti DBMS (misalnya `sqlite3`, `pandas`, parser SQL) dilarang.
- Komponen lain hanya boleh di-import lewat `common.interfaces`.
- `python -m unittest discover -s tests` harus lulus sebelum membuka PR.

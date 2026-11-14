"""Uji persistensi metadata checkpoint (FR-06)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from common.errors import LogError
from common.log import RecoveryTarget
from failure_recovery.checkpoint.checkpoint_store import CheckpointStore, _to_dict
from failure_recovery.checkpoint.meta import CheckpointMeta


class CheckpointStoreTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.path = self.root / "master.json"
        self.store = CheckpointStore(str(self.path), str(self.root / "snapshots"))
        self.meta = CheckpointMeta(
            checkpoint_lsn=12,
            snapshot_path="",
            timestamp=datetime.now(timezone.utc),
            max_tx_id=7,
            completed=True,
        )

    def test_master_belum_ada(self):
        self.assertIsNone(self.store.read_master())
        self.assertIsNone(self.store.find_latest_completed())

    def test_metadata_terbaca_setelah_store_dibuat_ulang(self):
        self.store.write_master_atomic(self.meta)
        baru = CheckpointStore(str(self.path), self.store.snapshot_dir)
        self.assertEqual(baru.read_master(), self.meta)
        self.assertEqual(baru.find_latest_completed(), self.meta)

    def test_metadata_terbaca_dari_proses_baru(self):
        self.store.write_master_atomic(self.meta)
        result = subprocess.run(
            [
                sys.executable, "-c",
                "import sys; "
                "from failure_recovery.checkpoint.checkpoint_store import CheckpointStore; "
                "meta = CheckpointStore(sys.argv[1], sys.argv[2]).find_latest_completed(); "
                "print(meta.checkpoint_lsn, meta.max_tx_id)",
                str(self.path), self.store.snapshot_dir,
            ],
            cwd=Path(__file__).resolve().parents[2],
            capture_output=True, text=True, timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "12 7")

    def test_checkpoint_belum_selesai_diabaikan(self):
        payload = _to_dict(self.meta)
        payload["completed"] = False
        self.path.write_text(json.dumps(payload), encoding="utf-8")
        self.assertFalse(self.store.read_master().completed)
        self.assertIsNone(self.store.find_latest_completed())

    def test_json_rusak_menghasilkan_error(self):
        self.path.write_text("{terpotong", encoding="utf-8")
        with self.assertRaises(LogError):
            self.store.read_master()

    def test_metadata_tidak_valid_ditolak(self):
        for field, value in (
            ("checkpoint_lsn", 0), ("checkpoint_lsn", True),
            ("max_tx_id", -1), ("completed", "true"),
            ("active_tx", [False]), ("timestamp", "bukan-waktu"),
        ):
            with self.subTest(field=field, value=value):
                payload = _to_dict(self.meta)
                payload[field] = value
                self.path.write_text(json.dumps(payload), encoding="utf-8")
                with self.assertRaises(LogError):
                    self.store.read_master()

    def test_penulisan_memanggil_fsync_sebelum_replace(self):
        calls = []
        original_fsync, original_replace = os.fsync, os.replace

        def fsync(fd):
            calls.append("fsync")
            return original_fsync(fd)

        def replace(source, target):
            calls.append("replace")
            return original_replace(source, target)

        with patch("failure_recovery.checkpoint.checkpoint_store.os.fsync", side_effect=fsync), \
             patch("failure_recovery.checkpoint.checkpoint_store.os.replace", side_effect=replace):
            self.store.write_master_atomic(self.meta)
        self.assertEqual(calls, ["fsync", "replace"])
        self.assertEqual(list(self.root.glob(".checkpoint-*.tmp")), [])

    def test_gagal_menyimpan_mempertahankan_master_lama(self):
        self.store.write_master_atomic(self.meta)
        before = self.path.read_bytes()
        self.meta.checkpoint_lsn = 20
        for operation in ("fsync", "replace"):
            with self.subTest(operation=operation):
                with patch(
                    f"failure_recovery.checkpoint.checkpoint_store.os.{operation}",
                    side_effect=OSError("disk gagal"),
                ):
                    with self.assertRaises(LogError):
                        self.store.write_master_atomic(self.meta)
                self.assertEqual(self.path.read_bytes(), before)
                self.assertEqual(list(self.root.glob(".checkpoint-*.tmp")), [])

    def test_direktori_master_dibuat(self):
        path = self.root / "nested" / "master.json"
        store = CheckpointStore(str(path), self.store.snapshot_dir)
        store.write_master_atomic(self.meta)
        self.assertEqual(store.find_latest_completed(), self.meta)

    def test_find_snapshot_before_memilih_kandidat_terbaru(self):
        old = CheckpointMeta(
            checkpoint_lsn=10,
            snapshot_path=str(self.root / "snapshots" / "checkpoint-10.snap"),
            timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc),
            max_tx_id=5,
            completed=True,
        )
        new = CheckpointMeta(
            checkpoint_lsn=20,
            snapshot_path=str(self.root / "snapshots" / "checkpoint-20.snap"),
            timestamp=datetime(2026, 1, 2, tzinfo=timezone.utc),
            max_tx_id=8,
            completed=True,
        )
        self.store.write_snapshot_metadata(old)
        self.store.write_snapshot_metadata(new)
        self.store.write_master_atomic(new)

        self.assertEqual(
            self.store.find_snapshot_before(RecoveryTarget(tx_id=7)),
            old,
        )
        self.assertEqual(
            self.store.find_snapshot_before(RecoveryTarget(tx_id=8)),
            new,
        )
        middle = datetime(2026, 1, 1, 12, tzinfo=timezone.utc).timestamp()
        self.assertEqual(
            self.store.find_snapshot_before(RecoveryTarget(timestamp=middle)),
            old,
        )
        before = datetime(2025, 12, 31, tzinfo=timezone.utc).timestamp()
        self.assertIsNone(
            self.store.find_snapshot_before(RecoveryTarget(timestamp=before))
        )

    def test_find_snapshot_before_memakai_active_tx(self):
        metadata = CheckpointMeta(
            checkpoint_lsn=30,
            snapshot_path=str(self.root / "snapshots" / "checkpoint-30.snap"),
            timestamp=datetime(2026, 1, 3, tzinfo=timezone.utc),
            active_tx=[7],
            max_tx_id=10,
            completed=True,
        )
        self.store.write_snapshot_metadata(metadata)
        self.store.write_master_atomic(metadata)

        self.assertEqual(
            self.store.find_snapshot_before(RecoveryTarget(tx_id=7)),
            metadata,
        )
        self.assertIsNone(
            self.store.find_snapshot_before(RecoveryTarget(tx_id=6))
        )

    def test_find_snapshot_before_mengabaikan_checkpoint_belum_selesai(self):
        self.meta.completed = False
        self.store.write_snapshot_metadata(self.meta)
        self.store.write_master_atomic(self.meta)

        self.assertIsNone(
            self.store.find_snapshot_before(RecoveryTarget(tx_id=99))
        )

    def test_snapshot_path_berada_di_direktori_snapshot(self):
        self.assertEqual(
            self.store.snapshot_path(12),
            str(Path(self.store.snapshot_dir) / "checkpoint-12.snap"),
        )
        for invalid in (0, -1, True, "12"):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                self.store.snapshot_path(invalid)


if __name__ == "__main__":
    unittest.main()

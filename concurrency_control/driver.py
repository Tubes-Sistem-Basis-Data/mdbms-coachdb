# CC-10: masih skeleton buat skenario, parser, sama driver. parser dan runner belum jalan
# Tipe schedule sama checker punya cc4, jadi import tipenya pas TYPE_CHECKING aja biar file ini bisa diimport dulu

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from common.interfaces.concurrency_control import ConcurrencyControlManager
from common.transaction import AccessStatus, ProtocolType
from concurrency_control.serializability import (
    Schedule,
    SerializabilityResult,
)

if TYPE_CHECKING:
    from concurrency_control.serializability import (
        SerializabilityChecker,
    )


@dataclass
class Scenario:
    # expected isinya respons pertama tiap R/W sesuai urutan input
    # BEGIN/COMMIT/ABORT pakai hasil facade masing-masing, bukan AccessStatus
    # Operation ikut cc4: Operation(tx_id: int, action: Action, resource: ResourceId)
    # Mapping parser: "T1 R(A)" -> Operation(1, Action.READ, ResourceId("A")), W -> Action.WRITE

    name: str
    protocol: ProtocolType
    schedule: Schedule
    expected: list[AccessStatus]


@dataclass
class ScenarioResult:
    # Ini hasil skenario yang beneran jalan, jadi jangan isi dummy terus dianggap lulus
    # statuses catat respons pertama tiap R/W termasuk WAIT. retry ga dihitung jadi input baru
    # Yang akhirnya diizinin dan dieksekusi masuk final_schedule buat dicek cc4
    # final_schedule dicatat driver sendiri (bukan ccm.schedule) biar tetep jalan selama CCManager masih skeleton

    statuses: list[AccessStatus]
    final_schedule: Schedule
    verdict: SerializabilityResult
    passed: bool


class ScheduleParser:
    # Formatnya satu operasi per baris, misal T1 BEGIN, T1 R(A), T1 W(A), T1 COMMIT, atau T1 ABORT
    # Metadata buat nama skenario, protokol, sama expected status. komentar schedule pakai #

    def parse(self, path: str) -> list[Scenario]:
        # Baca file schedule terus kirim isinya ke parse_text
        return []

    def parse_text(self, text: str) -> list[Scenario]:
        # Parse metadata, operasi per baris, komentar #, sama expected statusnya
        return []


class CCMDriver:
    # Buat tes ccm sendiri tanpa qp atau cli qp. ccm sama checker dikasih dari yang manggil
    # Selama CCManager masih skeleton, method-nya cuma balikin nilai default

    def __init__(
        self,
        ccm: ConcurrencyControlManager,
        checker: SerializabilityChecker,
        parser: ScheduleParser | None = None,
    ) -> None:
        self.ccm = ccm
        self.checker = checker
        self.parser = parser if parser is not None else ScheduleParser()

    def run(self, scenario: Scenario) -> ScenarioResult:
        # Nanti satu thread per transaksi (sekarang belum jalan)
        # Pakai begin_transaction, validate_object, commit, abort, sama set_protocol dari facade repo
        # Kalau WAIT, ulang request yang sama pakai jeda common.config.WAIT_RETRY_SECONDS. ga pakai wait(tx)
        # WRITE harus bawa DataChange, dapet izin aja belum berarti datanya udah berubah
        # Kalau dapet ABORTED, facade udah abort sendiri. jangan panggil abort lagi, stop thread tx itu
        # Catat yang beneran dieksekusi ke final_schedule terus cek serializability lewat checker cc4
        # Jangan return passed=True sebelum skenario jalan dan hasilnya dicek
        return ScenarioResult(
            statuses=[],
            final_schedule=Schedule(),
            verdict=SerializabilityResult(serializable=False, serial_order=[]),
            passed=False,
        )

    def run_file(self, path: str) -> list[ScenarioResult]:
        # Parse skenario dari file terus jalanin satu-satu
        return []

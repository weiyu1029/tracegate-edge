from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tracegate.db import Database
from tracegate.errors import DomainError
from tracegate.service import TraceGateService
from tracegate.util import normalize_mac


class MacNormalizationContractTestCase(unittest.TestCase):
    def test_supported_mac_notations_have_one_canonical_form(self) -> None:
        expected = "AA:BB:CC:DD:EE:FF"
        for value in (
            "AABBCCDDEEFF",
            "aabbccddeeff",
            "AA:BB:CC:DD:EE:FF",
            "aa-bb-cc-dd-ee-ff",
            "aabb.CCDD.eeff",
        ):
            with self.subTest(value=value):
                self.assertEqual(normalize_mac(value), expected)

    def test_malformed_or_ambiguous_mac_notations_are_rejected(self) -> None:
        for value in (
            "02/54/47/00/01/01",
            "02:54-47:00:01:01",
            "02 54 47 00 01 01",
            "02:54:47:00:01:GG",
            "02:54:47:00:01:01junk",
            " 02:54:47:00:01:01",
            "02:54:47:00:01:01 ",
            "02:54:47:00:01",
            "0254.4700.0101.0000",
            "",
            None,
        ):
            with self.subTest(value=value):
                self.assertEqual(normalize_mac(value), "")


class IdempotentErrorContractTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        database = Database(Path(self.temp.name) / "tracegate.db")
        database.migrate(seed=True)
        self.service = TraceGateService(database)

    def test_stored_error_replay_is_marked_in_serialized_error_body(self) -> None:
        payload = {
            "request_id": "contract-error-replay-0001",
            "acn": "ACN-DEMO-0001",
            "station_code": "ST-FT",
            "result": "PASS",
        }

        with self.assertRaises(DomainError) as first:
            self.service.complete_station(payload)
        self.assertNotIn("idempotent_replay", first.exception.as_dict())

        with self.assertRaises(DomainError) as replay:
            self.service.complete_station(payload)
        body = replay.exception.as_dict()
        self.assertEqual(replay.exception.status, 409)
        self.assertEqual(body["error"]["code"], "ROUTE_VIOLATION")
        self.assertIs(body["idempotent_replay"], True)


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from scripts import public_release_check


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCANNER = PROJECT_ROOT / "scripts" / "public_release_check.py"


class PublicReleaseCheckTests(unittest.TestCase):
    def run_scanner(self, root: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(SCANNER), str(root)],
            check=False,
            capture_output=True,
            text=True,
        )

    def test_database_inside_data_directory_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            database = root / "data" / "tracegate.db"
            database.parent.mkdir()
            database.write_bytes(b"SQLite format 3\x00synthetic")

            result = self.run_scanner(root)

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("data/tracegate.db: sensitive-file", result.stderr)

    def test_sqlite_sidecars_are_rejected(self) -> None:
        sidecar_names = (
            "tracegate.db-wal",
            "tracegate.db-shm",
            "tracegate.sqlite-wal",
            "tracegate.sqlite3-shm",
        )
        for sidecar_name in sidecar_names:
            with self.subTest(sidecar_name=sidecar_name):
                with tempfile.TemporaryDirectory() as temporary_directory:
                    root = Path(temporary_directory)
                    sidecar = root / "data" / sidecar_name
                    sidecar.parent.mkdir()
                    sidecar.write_bytes(b"synthetic SQLite sidecar")

                    result = self.run_scanner(root)

                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertIn(f"data/{sidecar_name}: sensitive-file", result.stderr)

    def test_pkcs_and_openssh_private_key_headers_are_rejected(self) -> None:
        headers = (
            "-" * 5 + "BEGIN " + "PRIVATE KEY" + "-" * 5,
            "-" * 5 + "BEGIN " + "OPENSSH PRIVATE KEY" + "-" * 5,
        )
        for index, header in enumerate(headers):
            with self.subTest(index=index):
                with tempfile.TemporaryDirectory() as temporary_directory:
                    root = Path(temporary_directory)
                    (root / "sample.txt").write_text(header, encoding="utf-8")

                    result = self.run_scanner(root)

                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertIn("private-key", result.stderr)

    def test_text_larger_than_limit_is_not_content_scanned(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "oversized.txt"
            path.write_bytes(b"x" * (public_release_check.MAX_TEXT_BYTES + 1))

            self.assertIsNone(public_release_check.read_text(path))

    def test_binary_body_is_not_content_scanned(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "opaque.bin"
            path.write_bytes(b"synthetic\x00payload")

            self.assertIsNone(public_release_check.read_text(path))

    def test_image_and_pdf_bodies_are_not_content_scanned(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            for name in ("evidence.png", "evidence.pdf"):
                path = root / name
                path.write_bytes(b"synthetic text that is deliberately not inspected")
                with self.subTest(name=name):
                    self.assertIsNone(public_release_check.read_text(path))


if __name__ == "__main__":
    unittest.main()

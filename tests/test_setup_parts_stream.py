"""Game-free ZIP byte compatibility and bounded reads: python -I -B tests/test_setup_parts_stream.py."""
import hashlib
import importlib.util
import os
from pathlib import Path
import random
import tempfile
import unittest
from unittest import mock
import zipfile

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("setup_parts_under_test", ROOT / "scripts" / "make_setup_parts.py")
parts = importlib.util.module_from_spec(spec)
spec.loader.exec_module(parts)


class BoundedReader:
    def __init__(self, stream):
        self.stream = stream

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return self.stream.__exit__(*args)

    def read(self, size=-1):
        if not 0 < size <= 1 << 20:
            raise AssertionError("package files must be read in bounded blocks")
        return self.stream.read(size)


def original_zip(path, root, names):
    """The released writer, used as a byte-compatibility oracle on small authored fixtures."""
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for name in sorted(names):
            info = zipfile.ZipInfo("HookshotBuilder/" + name, date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            archive.writestr(info, (root / name).read_bytes(), compresslevel=6)


class SetupPartsTests(unittest.TestCase):
    def check_archive(self, zip64=False):
        with tempfile.TemporaryDirectory(prefix="hookshot-setup-parts-") as folder:
            root = Path(folder)
            files = {"empty.bin": b"", "nested/François.txt": "authored fixture\n".encode("utf-8"),
                     "large.bin": random.Random(7).randbytes((1 << 20) + 257)}
            for name, data in files.items():
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
            names = list(files)
            baseline, streamed = root / "baseline.zip", root / "streamed.zip"
            original_zip(baseline, root, names)
            real_open = open

            def bounded_open(path, mode="r", *args, **kwargs):
                stream = real_open(path, mode, *args, **kwargs)
                return BoundedReader(stream) if mode == "rb" else stream

            with mock.patch("builtins.open", side_effect=bounded_open):
                sha, size = parts.write_zip(str(streamed), str(root), names)
            expected = baseline.read_bytes()
            self.assertEqual(streamed.read_bytes(), expected)
            self.assertEqual(sha, hashlib.sha256(expected).hexdigest())
            self.assertEqual(size, len(expected))
            self.assertFalse(Path(str(streamed) + ".new").exists())
            with zipfile.ZipFile(streamed) as archive:
                self.assertEqual(archive.namelist(), ["HookshotBuilder/" + name for name in sorted(names)])
                self.assertIsNone(archive.testzip())
                for name, data in files.items():
                    info = archive.getinfo("HookshotBuilder/" + name)
                    self.assertEqual(archive.read(info), data)
                    self.assertEqual(info.date_time, (2026, 1, 1, 0, 0, 0))
                    self.assertEqual(info.external_attr, 0o644 << 16)
                if zip64:
                    self.assertIn(b"PK\x06\x06", expected)
            for name in names:
                os.utime(root / name, (1_600_000_000, 1_600_000_000))
            self.assertEqual(parts.write_zip(str(streamed), str(root), list(reversed(names))), (sha, size))
            self.assertEqual(streamed.read_bytes(), expected)

    def test_bounded_reads_and_identical_archive(self):
        self.check_archive()

    def test_zip64_without_large_files(self):
        # Exercise the real ZIP64 path with a small fixture instead of allocating gigabytes.
        with mock.patch.object(zipfile, "ZIP64_LIMIT", 1 << 20):
            self.check_archive(zip64=True)


if __name__ == "__main__":
    unittest.main()

"""Exercise flash detection and capture errors with authored probe samples."""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "probe.py"


class ProbeTests(unittest.TestCase):
    def run_probe(self, rows):
        with tempfile.TemporaryDirectory(prefix="hookshot-probe-") as folder:
            trace = Path(folder) / "probe_1.txt"
            trace.write_text("".join(rows), encoding="utf-8")
            return subprocess.run([sys.executable, "-I", "-B", str(SCRIPT), str(trace)],
                                  capture_output=True, text=True)

    def test_nan_is_not_a_brightness_jump(self):
        rows = []
        for frame in range(3):
            # R16_SFLOAT: 0x3c00 is 1.0 and 0x7e00 is NaN.
            nan_sample = 0x7E00 if frame == 1 else 0x3C00
            rows.append(f"{frame} 0 D abcd nan_only 1x1 76 {nan_sample:08x} 0 0 0\n")
            # R16G16_SFLOAT: a NaN in R must not hide a real jump in G.
            jump_sample = 0x4C007E00 if frame == 1 else 0x3C003C00
            rows.append(f"{frame} 1 D abcd real_jump 1x1 83 {jump_sample:08x} 0 0 0\n")
            rows.append("\n")
        result = self.run_probe(rows)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("nan_only", result.stdout)
        self.assertIn("real_jump", result.stdout)
        self.assertIn("['nan', '16']", result.stdout)
        self.assertIn("\n1 entries far from both neighbours\n", result.stdout)

    def test_reports_incorrect_frame_count(self):
        for count in (0, 1, 2, 4):
            with self.subTest(count=count):
                rows = [f"{frame} 0 D abcd target 1x1 76 00003c00 0 0 0\n" for frame in range(count)]
                result = self.run_probe(rows)
                self.assertEqual(result.returncode, 1)
                self.assertIn(f"expected 3 frames, found {count}", result.stderr)
                self.assertIn("Capture a new probe_", result.stderr)
                self.assertNotIn("Traceback", result.stderr)

    def test_reports_truncated_row_with_line_number(self):
        rows = ["0 0 D abcd target 1x1 76 00003c00 0 0 0\n", "\n", "1 0 D abcd target 1x1 76 00003c00\n",
                "2 0 D abcd target 1x1 76 00003c00 0 0 0\n"]
        result = self.run_probe(rows)
        self.assertEqual(result.returncode, 1)
        self.assertIn("line 3 is incomplete", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_reports_invalid_numeric_fields_with_line_number(self):
        cases = [(6, value) for value in ("bad", "-1", "4294967296")]
        cases += [(column, value) for column in range(7, 11) for value in ("xyz", "-1", "100000000")]
        for column, value in cases:
            with self.subTest(column=column, value=value):
                rows = [f"{frame} 0 D abcd target 1x1 109 3f800000 0 0 0\n" for frame in range(3)]
                fields = rows[1].split()
                fields[column] = value
                rows[1] = " ".join(fields) + "\n"
                rows.insert(1, "\n")
                result = self.run_probe(rows)
                self.assertEqual(result.returncode, 1)
                self.assertIn("line 3 has invalid numeric fields", result.stderr)
                self.assertIn("32-bit", result.stderr)
                self.assertNotIn("Traceback", result.stderr)

    def test_accepts_full_uint32_samples_and_unknown_formats(self):
        for fmt in (0, 76, 83, 97, 100, 109, 122, 123, 999, 0xFFFFFFFF):
            with self.subTest(fmt=fmt):
                rows = [f"{frame} 0 D abcd target 1x1 {fmt} ffffffff ffffffff ffffffff ffffffff\n"
                        for frame in range(3)]
                result = self.run_probe(rows)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("\n0 entries far from both neighbours\n", result.stdout)

    def test_auxiliary_capture_records_do_not_change_pixel_analysis(self):
        rows = ["0 0 D abcd target 1x1 100 3f800000 0 0 0\n",
                "1 0 D abcd target 1x1 100 41200000 0 0 0\n",
                "2 0 D abcd target 1x1 100 3f800000 0 0 0\n"]
        baseline = self.run_probe(rows)
        self.assertEqual(baseline.returncode, 0, baseline.stderr)
        self.assertIn("\n1 entries far from both neighbours\n", baseline.stdout)
        # vk_draw.cpp writes these diagnostic records after the pixel samples.
        records = ["U 1 000000000000abcd s0 c3 0x1000 +0x20 nan 1 inf 0 huge 0 zero 0 first 0x7fc00000\n",
                   "H 1 000000000000abcd c3 3f8000007fc00000\n",
                   "S 1 000000000000abcd s0 c3+0x20 addr 0x2000 size 0x10 read 1 nz 4 hash abcd\n"]
        for record in records:
            with self.subTest(record=record[0]):
                result = self.run_probe(rows + [record])
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout, baseline.stdout)


if __name__ == "__main__":
    unittest.main()

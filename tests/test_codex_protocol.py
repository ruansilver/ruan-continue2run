import io
import subprocess
import sys
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src/ruan-continue2run/scripts"))
from adapters._codex_protocol import read_message  # noqa: E402


class CodexProtocolTests(unittest.TestCase):
    def spawn(self, code):
        proc = subprocess.Popen(
            [sys.executable, "-u", "-c", code],
            stdout=subprocess.PIPE,
            text=True,
            encoding="utf-8",
        )

        def cleanup():
            if proc.poll() is None:
                proc.terminate()
            proc.wait(timeout=2)
            proc.stdout.close()

        self.addCleanup(cleanup)
        return proc

    def test_messages_in_one_flush_survive_separate_waits(self):
        # A text readline can prefetch both lines, leaving the OS pipe empty.
        # A selector on that pipe then misses the second buffered message.
        proc = self.spawn(
            'import sys,time; sys.stdout.write(\'{"id":1}\\n{"id":2}\\n\'); '
            'sys.stdout.flush(); time.sleep(2)'
        )
        self.assertEqual(read_message(proc, time.monotonic() + 1), {"id": 1})
        self.assertEqual(read_message(proc, time.monotonic() + 0.3), {"id": 2})

    def test_split_utf8_and_partial_line_finish_after_timeout(self):
        proc = self.spawn(
            'import os,time; os.write(1, b"{\\\"text\\\":\\\"\\xe4"); '
            'time.sleep(0.15); os.write(1, b"\\xb8\\xad\\xe6\\x96\\x87\\\"}\\n")'
        )
        self.assertIsNone(read_message(proc, time.monotonic() + 0.05))
        self.assertEqual(read_message(proc, time.monotonic() + 1), {"text": "中文"})
        self.assertIsNone(read_message(proc, time.monotonic() + 1))

    def test_eof_drains_messages_and_does_not_wait_again(self):
        proc = self.spawn('print(\'{"id":1}\\n{"id":2}\')')
        self.assertEqual(read_message(proc, time.monotonic() + 1), {"id": 1})
        self.assertEqual(read_message(proc, time.monotonic() + 1), {"id": 2})
        self.assertIsNone(read_message(proc, time.monotonic() + 1))
        started = time.monotonic()
        self.assertIsNone(read_message(proc, started + 1))
        self.assertLess(time.monotonic() - started, 0.1)

    def test_stream_without_fileno_and_non_object_lines(self):
        proc = SimpleNamespace(stdout=io.StringIO('noise\n[]\n{"id":3}\n'))
        self.assertEqual(read_message(proc, time.monotonic() + 1), {"id": 3})
        self.assertIsNone(read_message(proc, time.monotonic() + 1))

    def test_deadline_keeps_wait_bounded(self):
        proc = self.spawn('import time; time.sleep(2)')
        started = time.monotonic()
        self.assertIsNone(read_message(proc, started + 0.05))
        self.assertLess(time.monotonic() - started, 0.4)


if __name__ == "__main__":
    unittest.main()

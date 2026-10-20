import io
import threading
import unittest
from unittest.mock import Mock, patch

from tinylib.ingest import _deadline_confirmation


class DeadlineConfirmationTests(unittest.TestCase):
    def test_success_does_not_wait_for_process_exit(self):
        release = threading.Event()
        process = Mock()
        process.stdout = io.StringIO('Submitting job...\nResult=Success\nJobID=abc123\n')
        process.wait.side_effect = lambda *args, **kwargs: 0 if release.wait(2) else 1
        try:
            with patch('tinylib.ingest.subprocess.Popen', return_value=process):
                identifier, output, elapsed = _deadline_confirmation(['deadlinecommand'], Mock())
            self.assertEqual(identifier, 'abc123')
            self.assertLess(elapsed, 1)
            self.assertIn('Result=Success', output)
            process.kill.assert_not_called()
        finally:
            release.set()

    def test_failure_includes_command_output(self):
        process = Mock()
        process.stdout = io.StringIO('Error: repository unavailable\n')
        process.wait.return_value = 1
        with patch('tinylib.ingest.subprocess.Popen', return_value=process):
            with self.assertRaisesRegex(RuntimeError, 'repository unavailable'):
                _deadline_confirmation(['deadlinecommand'], Mock())

    def test_progress_shows_deadline_output(self):
        process = Mock()
        process.stdout = io.StringIO('Running submission event...\nResult=Success\nJobID=abc123\n')
        process.wait.return_value = 0
        report = Mock()
        with patch('tinylib.ingest.subprocess.Popen', return_value=process):
            _deadline_confirmation(['deadlinecommand'], report)
        self.assertIn('Running submission event', report.call_args.args[0])

    def test_timeout_warns_about_possible_existing_job(self):
        release = threading.Event()
        class WaitingOutput:
            def __iter__(self):
                release.wait(2)
                return iter([])
            def close(self):
                pass
        process = Mock()
        process.stdout = WaitingOutput()
        process.poll.return_value = None
        process.kill.side_effect = release.set
        process.wait.return_value = -1
        try:
            with patch('tinylib.ingest.subprocess.Popen', return_value=process):
                with self.assertRaisesRegex(RuntimeError, 'job may already exist'):
                    _deadline_confirmation(['deadlinecommand'], Mock(), timeout=.01)
            process.kill.assert_called_once()
        finally:
            release.set()

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


import base64
import time
import json
import tempfile
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from tinylib.ingest import (DeadlineSubmissionError, WebServiceUnavailable, _web_service_submit,
                            submit_deadline, submit_deadline_batch)


class FakeWebService:
    """A local stand-in for the Deadline Web Service's POST /api/jobs."""
    def __init__(self, status=200, delay=0, fail_after=None):
        self.requests = []
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                owner.requests.append((self.path, dict(self.headers), body))
                if delay:
                    time.sleep(delay)
                failing = fail_after is not None and len(owner.requests) > fail_after
                self.send_response(500 if failing else status)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({'_id': '%024x' % len(owner.requests)}).encode())

            def log_message(self, *args):
                pass
        self.server = HTTPServer(('127.0.0.1', 0), Handler)
        self.server.handle_error = lambda request, address: None  # Client gave up (timeout test).
        self.url = 'http://127.0.0.1:%d' % self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


class WebServiceTests(unittest.TestCase):
    def setUp(self):
        self.service = FakeWebService()
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        (root / 'spool').mkdir()
        (root / 'worker.py').write_text('')
        access = Mock()
        self.settings = {'_access': access, 'deadline': {
            'worker_script': str(root / 'worker.py'), 'spool_root': str(root / 'spool'),
            'web_service': {'url': self.service.url, 'user': 'artist'}}}
        self.manifests = [{'job_id': 'job%d' % i, 'name': 'Asset %d' % i, 'library': {'root': str(root)}}
                          for i in range(3)]

    def tearDown(self):
        self.service.close()
        self.temp.cleanup()

    def test_post_body_and_job_id(self):
        identifier, path = submit_deadline(self.manifests[0], self.settings)
        self.assertEqual(identifier, '%024x' % 1)
        url, headers, body = self.service.requests[0]
        self.assertEqual(url, '/api/jobs')
        self.assertTrue(body['IdOnly'])
        self.assertEqual(body['AuxFiles'], [])
        self.assertEqual(body['JobInfo']['Plugin'], 'Python')
        self.assertEqual(body['JobInfo']['UserName'], 'artist')
        self.assertEqual(body['PluginInfo']['Arguments'], '"%s"' % path)
        self.assertNotIn('Authorization', headers)
        record = json.loads(path.with_suffix('.submission.json').read_text())
        self.assertEqual((record['deadline_job_id'], record['method']), (identifier, 'web_service'))

    def test_basic_authentication(self):
        self.settings['deadline']['web_service'].update(require_authentication=True, password='secret')
        submit_deadline(self.manifests[0], self.settings)
        headers = self.service.requests[0][1]
        self.assertEqual(headers['Authorization'], 'Basic ' + base64.b64encode(b'artist:secret').decode())

    def test_batch_posts_each_job(self):
        seen = []
        results = submit_deadline_batch(self.manifests, self.settings,
                                        submitted=lambda m, i, p: seen.append((m['name'], i)))
        self.assertEqual(len(self.service.requests), 3)
        self.assertEqual([i for _, i in seen], [r[0] for r in results])

    def test_refused_connection_is_unavailable(self):
        self.service.close()
        with self.assertRaises(WebServiceUnavailable):
            _web_service_submit({'url': self.service.url}, {}, {}, timeout=5)

    def test_refused_connection_falls_back_to_deadlinecommand(self):
        self.service.close()
        process = Mock()
        process.stdout = io.StringIO('Result=Success\nJobID=abc123\n')
        process.wait.return_value = 0
        with patch('tinylib.ingest.subprocess.Popen', return_value=process) as popen:
            identifier, _ = submit_deadline(self.manifests[0], self.settings)
        self.assertEqual(identifier, 'abc123')
        self.assertEqual(popen.call_args.args[0][0], 'deadlinecommand')

    def deadlinecommand(self, output):
        process = Mock()
        process.stdout = io.StringIO(output)
        process.wait.return_value = 0
        return patch('tinylib.ingest.subprocess.Popen', return_value=process)

    def test_http_error_falls_back(self):
        self.service.close()
        self.service = FakeWebService(status=500)
        self.settings['deadline']['web_service']['url'] = self.service.url
        with self.deadlinecommand('Result=Success\nJobID=abc123\n') as popen:
            identifier, path = submit_deadline(self.manifests[0], self.settings)
        self.assertEqual(identifier, 'abc123')
        self.assertEqual(len(self.service.requests), 1)
        self.assertEqual(json.loads(path.with_suffix('.submission.json').read_text())['method'], 'deadlinecommand')

    def test_bad_url_falls_back(self):
        self.settings['deadline']['web_service']['url'] = 'deadline:8081'
        with self.deadlinecommand('Result=Success\nJobID=abc123\n'):
            self.assertEqual(submit_deadline(self.manifests[0], self.settings)[0], 'abc123')

    def test_lost_answer_does_not_fall_back(self):
        self.service.close()
        self.service = FakeWebService(delay=1)
        web = {'url': self.service.url}
        with self.assertRaisesRegex(RuntimeError, 'may already exist') as caught:
            _web_service_submit(web, {}, {}, timeout=.2)
        self.assertNotIsInstance(caught.exception, WebServiceUnavailable)

    def test_batch_falls_back_for_remaining_jobs(self):
        self.service.close()
        self.service = FakeWebService(fail_after=1)
        self.settings['deadline']['web_service']['url'] = self.service.url
        seen = []
        with self.deadlinecommand('Result=Success\nJobID=id1\nResult=Success\nJobID=id2\n') as popen:
            results = submit_deadline_batch(self.manifests, self.settings,
                                            submitted=lambda m, i, p: seen.append(i))
        self.assertEqual(seen, ['%024x' % 1, 'id1', 'id2'])
        self.assertEqual([r[0] for r in results], seen)
        command = popen.call_args.args[0]
        self.assertEqual(command.count('-job'), 2)


class BatchCommandTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        (root / 'spool').mkdir()
        (root / 'worker.py').write_text('')
        self.settings = {'_access': Mock(), 'deadline': {
            'worker_script': str(root / 'worker.py'), 'spool_root': str(root / 'spool')}}
        self.manifests = [{'job_id': 'job%d' % i, 'name': 'Asset %d' % i, 'library': {'root': str(root)}}
                          for i in range(3)]

    def tearDown(self):
        self.temp.cleanup()

    def run_batch(self, output, code=0):
        process = Mock()
        process.stdout = io.StringIO(output)
        process.wait.return_value = code
        with patch('tinylib.ingest.subprocess.Popen', return_value=process) as popen:
            results = submit_deadline_batch(self.manifests, self.settings)
        return popen, results

    def test_one_call_for_all_jobs(self):
        output = ''.join('Result=Success\nJobID=id%d\n' % i for i in range(3))
        popen, results = self.run_batch(output)
        self.assertEqual(popen.call_count, 1)
        command = popen.call_args.args[0]
        self.assertEqual(command[:2], ['deadlinecommand', '-SubmitMultipleJobs'])
        self.assertEqual(command.count('-job'), 3)
        self.assertEqual([r[0] for r in results], ['id0', 'id1', 'id2'])
        for identifier, path in results:
            self.assertEqual(json.loads(path.with_suffix('.submission.json').read_text())['deadline_job_id'],
                             identifier)

    def test_missing_ids_report_what_deadline_returned(self):
        with self.assertRaises(DeadlineSubmissionError) as caught:
            self.run_batch('Result=Success\nJobID=id0\nError: something\n', code=1)
        self.assertEqual(caught.exception.job_ids, ['id0'])
        self.assertIn('1 of 3', str(caught.exception))

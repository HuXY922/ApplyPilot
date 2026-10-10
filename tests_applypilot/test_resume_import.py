"""Real fictional file bytes through local WSGI, bounded extraction, and review.

No browser, live model, external network, or real candidate data is used here.
"""
import base64
import io
import json
import tempfile
import unittest
from unittest.mock import patch

from applypilot_core import runtime
from applypilot_core.server import ApplyPilotApp, MAX_BODY
from applypilot_core.service import MAX_IMPORT_BYTES, validate_profile

from tests_applypilot.resume_fixtures import fixture_bytes


class ResumeImportTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.app = ApplyPilotApp(self.directory.name)
        self.cookie = ''
        self.csrf = ''
        self.state = self.request('/api/state')
        self.csrf = self.state['csrf']

    def request(self, path, data=None, expected=200):
        raw = json.dumps(data, ensure_ascii=False).encode() if data is not None else b''
        environ = {'REQUEST_METHOD': 'POST' if data is not None else 'GET', 'PATH_INFO': path,
                   'HTTP_HOST': '127.0.0.1:8787', 'HTTP_ORIGIN': 'http://127.0.0.1:8787',
                   'HTTP_COOKIE': self.cookie, 'HTTP_X_CSRF_TOKEN': self.csrf,
                   'CONTENT_TYPE': 'application/json', 'CONTENT_LENGTH': str(len(raw)),
                   'wsgi.input': io.BytesIO(raw), 'REMOTE_ADDR': 'resume-test'}
        response = {}
        def start(status, headers):
            response.update(status=int(status.split()[0]), headers=dict(headers))
        result = json.loads(b''.join(self.app(environ, start)))
        self.assertEqual(response['status'], expected, result)
        if 'Set-Cookie' in response['headers']:
            self.cookie = response['headers']['Set-Cookie'].split(';')[0]
        return result

    def test_actual_txt_pdf_docx_extract_review_and_save_without_model_calls(self):
        with patch('applypilot_core.model.request_json') as model_call:
            for ext in ('txt', 'pdf', 'docx'):
                with self.subTest(format=ext):
                    previous = self.request('/api/state')
                    content = fixture_bytes(ext)
                    result = self.request('/api/profile/import', {
                        'filename': 'fictional-resume.' + ext,
                        'content_base64': base64.b64encode(content).decode()})
                    self.assertTrue(result['requires_review'])
                    self.assertEqual(result['file_type'], ext)
                    self.assertEqual(result['mode'], 'rules')
                    draft = result['draft']
                    self.assertEqual(draft['name'], '陈星河（虚构文件测试）')
                    self.assertEqual(draft['preferences']['graduation_year'], '2027')
                    self.assertEqual(draft['education'][0], {'institution': '虚构示例大学', 'degree': '硕士',
                                                           'major': '信息管理', 'graduation': '2027-07'})
                    self.assertEqual(draft['projects'][0]['name'], '校园学习助手（虚构教学项目）')
                    self.assertIn('以上均为虚构教学资料', draft['projects'][0]['description'])
                    self.assertIn('以上均为虚构教学资料', draft['resume_text'])
                    after = self.request('/api/state')
                    self.assertEqual(after['profile'], previous['profile'])
                    self.assertEqual(after['profile_version'], previous['profile_version'])
                    self.assertEqual(after['profile_confirmed'], previous['profile_confirmed'])
                    self.request('/api/profile', {'profile': draft, 'confirmed': False,
                                                  'expected_version': previous['profile_version']}, expected=409)
                    draft['name'] = '陈星河（虚构已核对）'
                    self.request('/api/profile', {'profile': draft, 'confirmed': True,
                                                  'expected_version': previous['profile_version']})
                    self.app = ApplyPilotApp(self.directory.name)
                    saved = self.request('/api/state')
                    self.assertEqual(saved['profile']['name'], draft['name'])
                    self.assertEqual(saved['profile_version'], previous['profile_version'] + 1)
                    self.assertTrue(saved['profile_confirmed'])
                    self.assertIsNone(saved['draft'])
        model_call.assert_not_called()

    def test_binary_limit_fits_json_envelope_and_excess_rejected_before_extraction(self):
        encoded = base64.b64encode(b'x' * MAX_IMPORT_BYTES).decode()
        payload = {'filename': 'fictional.txt', 'content_base64': encoded}
        self.assertLess(len(json.dumps(payload).encode()), MAX_BODY)
        # File-byte limit is admitted; the separate text-character limit rejects it.
        result = self.request('/api/profile/import', payload, expected=400)
        self.assertEqual(result['code'], 'invalid_input')
        payload['content_base64'] = base64.b64encode(b'x' * (MAX_IMPORT_BYTES + 1)).decode()
        result = self.request('/api/profile/import', payload, expected=413)
        self.assertEqual(result['code'], 'file_too_large')

    def test_education_ambiguity_and_invalid_years_stay_unknown(self):
        for year, date in (('', '2027-07'), ('2028', '2027-07'), ('2027', '2023-09 - 2027-07'),
                           ('2027', '入学2027-07'), ('2027', '入学 2027-07'), ('2027', '2027-13')):
            with self.subTest(year=year, date=date):
                profile = validate_profile(runtime.extract_profile(
                    '毕业年份：' + year + '\n教育经历\n虚构示例大学 硕士 信息管理 ' + date))
                self.assertEqual(profile['education'][0]['graduation'], '')
        for status in ('录取', '入读', '报到', '就读', '在读', '肄业', '退学'):
            for separator in (' ', ''):
                profile = runtime.extract_profile('毕业年份：2027\n教育经历\n虚构示例大学 硕士 信息管理' + separator + status + ' 2027-07')
                self.assertEqual(profile['education'][0]['graduation'], '')
                self.assertEqual(profile['education'][0]['major'], '')
        for value in ('2027年', '2027届', '2027'):
            profile = runtime.extract_profile('毕业年份：' + value)
            self.assertEqual(profile['preferences']['graduation_year'], '2027')
        for value in ('maybe 2027', '2027 or 2028', '20277', '1999'):
            self.assertEqual(runtime.extract_profile('毕业年份：' + value)['preferences']['graduation_year'], '')
        self.assertEqual(runtime.extract_profile('项目经历\n完成调研。\n整理记录。')['projects'][0]['name'], '')


if __name__ == '__main__':
    unittest.main()

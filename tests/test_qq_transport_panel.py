"""SnowLuma cutover UI/API stays gated until an operator confirms a live account."""
from tests import _env  # noqa: F401

import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient
from furina.core import db, paths, winproc
from panel.services import qq_transport as panel_transport
from panel import server
from panel.services import data


class QQTransportPanelTest(unittest.TestCase):
    def setUp(self):
        env_file=_env.TMP/'.env'
        self.old_env=env_file.read_text(encoding='utf-8') if env_file.exists() else ''
        _env.write_env('QQ_TRANSPORT=snowluma\nBOT_QQ=111111\nSNOWLUMA_ACCOUNT_CONFIRMED=false\n')
        self.client=TestClient(server.create_app())
        self.headers={'x-panel-token':server.TOKEN}

    def tearDown(self):
        self.client.close()
        _env.write_env(self.old_env)

    def test_login_state_requires_confirmation_when_detected_account_is_unconfirmed_or_different(self):
        state={'busy':False,'busy_label':'','napcat':True,'health':{'state':'online','user_id':'222222','detail':'connected'},'running':False,'nonebot':False}
        with patch.object(server.napcat,'is_running',return_value=True), patch.object(server.ctl,'state',state):
            response=self.client.get('/api/login',headers=self.headers)
        self.assertEqual(response.status_code,200)
        body=response.json()
        self.assertEqual(body['provider'],'snowluma')
        self.assertEqual(body['state'],'snowluma_confirm')
        self.assertEqual(body['detected_qq'],'222222')
        self.assertFalse(body['confirmed'])
        self.assertIn('127.0.0.1:5099',body['webui'])
        self.assertFalse(body['qr'])

        # A previously confirmed UIN must be re-confirmed if a different QQ is
        # loaded into SnowLuma later; otherwise the browser UI would hide its button.
        _env.write_env('QQ_TRANSPORT=snowluma\nBOT_QQ=111111\nSNOWLUMA_ACCOUNT_CONFIRMED=true\n')
        with patch.object(server.napcat,'is_running',return_value=True), patch.object(server.ctl,'state',state):
            response=self.client.get('/api/login',headers=self.headers)
        self.assertEqual(response.json()['state'],'snowluma_confirm')

    def test_explicit_confirmation_starts_the_bot_only_after_identity_verification(self):
        with patch.object(server, 'confirm_snowluma_account', create=True), \
             patch('panel.services.qq_transport.confirm_account',return_value={'confirmed':True,'uin':'222222','added_furina_profile':True}), \
             patch.object(server.ctl,'start',return_value=True) as start:
            response=self.client.post('/api/qq-transport/confirm-account',headers=self.headers,json={'uin':'222222'})
        self.assertEqual(response.status_code,200)
        self.assertTrue(response.json()['bot_start_requested'])
        start.assert_called_once_with()

    def test_bot_start_refuses_stale_confirmation_when_live_uin_changed(self):
        from panel.services import control
        _env.write_env('QQ_TRANSPORT=snowluma\nBOT_QQ=111111\nSNOWLUMA_ACCOUNT_CONFIRMED=true\n')
        controller = control.Controller()
        live = control.health.Health('online', 'connected', '222222')
        with patch.object(control.napcat, 'is_running', return_value=True), \
             patch.object(control.health, 'query_health', return_value=live), \
             patch.object(control, 'find_bot_processes', side_effect=AssertionError('must not launch')):
            controller._start_work()
        self.assertTrue(any('保持机器人服务停止' in event['text'] for event in controller.events))

    def test_old_direct_account_switch_cannot_bypass_snowluma_account_confirmation(self):
        response=self.client.post('/api/accounts/use',headers=self.headers,json={'qq':'222222'})
        self.assertEqual(response.status_code,400)
        self.assertEqual(data.envfile.get('BOT_QQ',''),'111111')

    def test_snowluma_launcher_passes_only_transport_environment_and_never_auto_accepts(self):
        import json
        import os
        import shutil
        runtime=paths.ROOT/'integrations'/'snowluma-v2.2.1'
        shutil.rmtree(runtime,ignore_errors=True)
        runtime.mkdir(parents=True,exist_ok=True)
        node=runtime/'node.exe';entry=runtime/'index.mjs'
        node.write_bytes(b'fixture-not-executed');entry.write_text('// fixture',encoding='utf-8')
        (runtime/'furina-install.json').write_text(json.dumps({'version':'2.2.1','sha256':__import__('furina.core.qq_transport',fromlist=['SNOWLUMA_RELEASE_SHA256']).SNOWLUMA_RELEASE_SHA256}),encoding='utf-8')
        _env.write_env(f"QQ_TRANSPORT=snowluma\nSNOWLUMA_DIR={runtime}\nSNOWLUMA_NODE={node}\nSNOWLUMA_HTTP_URL=http://127.0.0.1:13000\nSNOWLUMA_HTTP_TOKEN={'a'*43}\nONEBOT_V11_ACCESS_TOKEN={'b'*43}\nSNOWLUMA_WEBUI_BOOTSTRAP_PASSWORD={'p'*40}\n")
        launched={}
        class FakeProcess:pid=1234
        def popen(args,**kwargs):launched.update(args=args,env=kwargs['env'],cwd=kwargs['cwd'],flags=kwargs['creationflags']);return FakeProcess()
        with patch.object(winproc,'IS_WIN',True),patch.object(winproc,'powershell',return_value=''),patch.object(winproc,'port_open',return_value=False),patch.object(panel_transport.subprocess,'Popen',side_effect=popen),patch.dict(os.environ,{'AI_CHAT_LLM_API_KEY':'must-not-enter-snow-env'}):
            result=panel_transport.start()
        self.assertEqual(result.pid,1234)
        self.assertEqual([os.path.normcase(x) for x in launched['args']],
                         [os.path.normcase(str(node)),os.path.normcase(str(entry))])
        self.assertEqual(os.path.normcase(launched['cwd']),os.path.normcase(str(runtime)))
        self.assertEqual(launched['env']['SNOWLUMA_HOOK_AUTOLOAD'],'0')
        self.assertEqual(launched['env']['SNOWLUMA_TELEMETRY'],'0')
        self.assertNotIn('AI_CHAT_LLM_API_KEY',launched['env'])
        self.assertNotIn('SNOWLUMA_HTTP_TOKEN',launched['env'])
        self.assertNotIn('SNOWLUMA_WS_TOKEN',launched['env'])
        self.assertNotIn('SNOWLUMA_ACCEPT_EULA',launched['env'])
        self.assertNotIn('SNOWLUMA_ACCEPT_PRIVACY',launched['env'])
        self.assertNotIn('BOT_QQ',launched['env'])

    def test_transport_login_and_confirmation_apis_require_panel_token(self):
        self.assertEqual(self.client.get('/api/login').status_code,401)
        self.assertEqual(self.client.post('/api/qq-transport/confirm-account',json={'uin':'222222'}).status_code,401)


if __name__=='__main__':unittest.main()

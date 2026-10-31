"""SnowLuma/OneBot transport seam; adapter must not turn its auth into URL/log data."""
from tests import _env  # noqa: F401

import asyncio
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from nonebot.exception import IgnoredException, MockApiException
from furina.core import db, env, paths, qq_transport
from furina.services import napcat_recover
from panel.services import health, napcat


class QQTransportTest(unittest.TestCase):
    def setUp(self):
        p=_env.TMP/'.env'
        self.old=p.read_text(encoding='utf-8') if p.exists() else ''
        _env.write_env('NAPCAT_HTTP_PORT=3000\nNAPCAT_WEBUI_PORT=6099\n')

    def tearDown(self):
        _env.write_env(self.old)

    def test_legacy_install_keeps_napcat_default(self):
        self.assertEqual(qq_transport.provider(),'napcat')
        self.assertEqual(qq_transport.onebot_url(),'http://127.0.0.1:3000')
        self.assertEqual(qq_transport.onebot_headers()['User-Agent'],'FurinaPanel')

    def test_snowluma_endpoint_and_token_are_transport_scoped(self):
        _env.write_env('QQ_TRANSPORT=snowluma\nSNOWLUMA_HTTP_URL=http://127.0.0.1:13000\nSNOWLUMA_HTTP_TOKEN=local-test-secret\nSNOWLUMA_WEBUI_PORT=5099\n')
        self.assertEqual(qq_transport.provider(),'snowluma')
        self.assertEqual(qq_transport.onebot_url(),'http://127.0.0.1:13000')
        self.assertEqual(qq_transport.onebot_headers()['Authorization'],'Bearer local-test-secret')
        self.assertNotIn('local-test-secret',qq_transport.onebot_url())
        self.assertEqual(qq_transport.webui_url(),'http://127.0.0.1:5099')

    def test_health_queries_same_verb_on_selected_transport(self):
        _env.write_env('QQ_TRANSPORT=snowluma\nSNOWLUMA_HTTP_URL=http://127.0.0.1:13000\nSNOWLUMA_HTTP_TOKEN=local-test-secret\nBOT_QQ=123456\n')
        calls=[]
        class Response:
            def __init__(self,data):self.data=data
            def __enter__(self):return self
            def __exit__(self,*_):pass
            def read(self,n):return json.dumps({'status':'ok','retcode':0,'data':self.data}).encode()
        def open_(request,timeout):
            calls.append((request.full_url,dict(request.header_items())))
            if request.full_url.endswith('/get_status'):return Response({'online':True})
            return Response({'user_id':123456})
        with patch('urllib.request.OpenerDirector.open',side_effect=open_):
            status=health.query_health('123456')
        self.assertEqual(status.state,'online')
        self.assertTrue(all(url.startswith('http://127.0.0.1:13000/') for url,_ in calls))
        self.assertTrue(all(headers.get('Authorization')=='Bearer local-test-secret' for _,headers in calls))

    def test_snowluma_mismatch_is_reported_not_auto_fixed(self):
        _env.write_env('QQ_TRANSPORT=snowluma\nSNOWLUMA_HTTP_URL=http://127.0.0.1:13000\nBOT_QQ=123456\n')
        with patch.object(health,'get_data',side_effect=[{'online':True},{'user_id':654321}]):
            result=health.query_health('123456')
        self.assertEqual(result.state,'mismatch')
        self.assertEqual(result.user_id,'654321')

    def test_auto_napcat_recovery_is_impossible_under_snowluma(self):
        _env.write_env('QQ_TRANSPORT=snowluma\nNAPCAT_AUTO_RECOVER=true\n')
        self.assertFalse(napcat_recover.enabled())
        self.assertFalse(napcat_recover.schedule_auto('654321','bot_offline','test'))

    def test_panel_napcat_compatibility_layer_selects_snowluma(self):
        snow = paths.ROOT / "integrations" / "snowluma-v2.2.1"; _env.write_env(f"QQ_TRANSPORT=snowluma\nSNOWLUMA_WEBUI_PORT=5099\nSNOWLUMA_WEBUI_HOST=127.0.0.1\nSNOWLUMA_DIR={snow}\nSNOWLUMA_NODE={snow / 'node.exe'}\n")
        self.assertEqual(napcat.webui_port(),5099)
        self.assertTrue(napcat.webui_url().startswith('http://127.0.0.1:5099'))
        self.assertIsNone(napcat.qrcode_path())
        self.assertEqual(napcat.log_dir(),snow/'logs')

    def test_ws_client_is_reverse_linked_to_nonebot_and_auto_hook_stays_off(self):
        _env.write_env('QQ_TRANSPORT=snowluma\nPORT=8080\nONEBOT_V11_ACCESS_TOKEN=local-ws-token-local-ws-token-1234567890\nSNOWLUMA_HTTP_TOKEN=local-http-token-local-http-token-1234567890\n')
        config=qq_transport.onebot_config()
        self.assertEqual(config['networks']['wsClients'][0]['url'],'ws://127.0.0.1:8080/onebot/v11/ws')
        self.assertEqual(config['networks']['wsClients'][0]['accessToken'],'local-ws-token-local-ws-token-1234567890')
        self.assertTrue(config['networks']['wsClients'][0]['enabled'])
        self.assertEqual(config['networks']['httpServers'][0]['accessToken'],'local-http-token-local-http-token-1234567890')
        runtime=qq_transport.snowluma_runtime_config()
        self.assertFalse(runtime["hookAutoLoad"])

    def test_snowluma_account_guard_blocks_messages_and_outbound_until_confirmed(self):
        from furina.plugins.qq_transport_guard import account_confirmed, _block_unconfirmed_outbound, _blocked, _hold_unconfirmed_event
        _env.write_env('QQ_TRANSPORT=snowluma\nSNOWLUMA_ACCOUNT_CONFIRMED=false\nBOT_QQ=111111\n')
        bot=SimpleNamespace(self_id='222222')
        event=SimpleNamespace(self_id=bot.self_id)
        self.assertFalse(account_confirmed(bot.self_id))
        self.assertTrue(_blocked(event))
        with self.assertRaises(IgnoredException):asyncio.run(_hold_unconfirmed_event(bot,event))
        with self.assertRaises(MockApiException):asyncio.run(_block_unconfirmed_outbound(bot,'send_group_msg',{'message':'hello'}))
        with self.assertRaises(MockApiException):asyncio.run(_block_unconfirmed_outbound(bot,'get_group_list',{}))
        _env.write_env('QQ_TRANSPORT=snowluma\nSNOWLUMA_ACCOUNT_CONFIRMED=true\nBOT_QQ=111111\n')
        with self.assertRaises(MockApiException):asyncio.run(_block_unconfirmed_outbound(bot,'send_group_msg',{'message':'hello'}))
        _env.write_env('QQ_TRANSPORT=snowluma\nSNOWLUMA_ACCOUNT_CONFIRMED=true\nBOT_QQ=222222\n')
        self.assertTrue(account_confirmed(bot.self_id))
        self.assertFalse(account_confirmed(""))
        self.assertFalse(_blocked(SimpleNamespace(self_id=bot.self_id)))
        self.assertTrue(_blocked(SimpleNamespace(self_id='111111')))
        asyncio.run(_block_unconfirmed_outbound(bot,'send_group_msg',{'message':'hello'}))
        asyncio.run(_block_unconfirmed_outbound(bot,'get_group_list',{}))

    def test_snowluma_account_confirmation_is_explicit_and_preserves_existing_profiles(self):
        from panel.services import data
        from panel.services import qq_transport as panel_transport
        old_profiles=db.get('persona','profiles')
        db.put('persona','profiles',[{'qq':'111111','nickname':'旧号','role_id':'furina_black'}])
        _env.write_env('QQ_TRANSPORT=snowluma\nBOT_QQ=111111\nSNOWLUMA_ACCOUNT_CONFIRMED=false\n')
        try:
            with patch.object(panel_transport.health,'query_health',return_value=SimpleNamespace(state='online',user_id='222222')):
                with patch.object(panel_transport.health,'get_data',return_value={'user_id':222222,'nickname':'新机器人'}):
                    result=panel_transport.confirm_account('222222')
            self.assertEqual(data.profiles()[0]['role_id'],'furina_black')
            self.assertEqual(data.profiles()[1],{'qq':'222222','nickname':'新机器人','role_id':'furina'})
            self.assertTrue(qq_transport.env.get_bool('SNOWLUMA_ACCOUNT_CONFIRMED',False))
        finally:
            if old_profiles is None:db.delete('persona','profiles')
            else:db.put('persona','profiles',old_profiles)

    def test_snowluma_config_is_authenticated_loopback_only_and_never_auto_injects(self):
        from panel.services import qq_transport as panel_transport
        runtime=paths.ROOT/'integrations'/'snowluma-v2.2.1'
        runtime.mkdir(parents=True,exist_ok=True)
        (runtime/'node.exe').write_bytes(b'not executed by test')
        (runtime/'index.mjs').write_text('// fixture\n',encoding='utf-8')
        (runtime/'furina-install.json').write_text(json.dumps({'version':'2.2.1','sha256':qq_transport.SNOWLUMA_RELEASE_SHA256}),encoding='utf-8')
        _env.write_env(f"QQ_TRANSPORT=snowluma\nSNOWLUMA_DIR={runtime}\nSNOWLUMA_NODE={runtime/'node.exe'}\nPORT=8080\nSNOWLUMA_WEBUI_PORT=5099\nSNOWLUMA_HTTP_URL=http://127.0.0.1:13000\nSNOWLUMA_HTTP_TOKEN={'a'*43}\nONEBOT_V11_ACCESS_TOKEN={'b'*43}\n")
        node,entry=panel_transport.ensure_snowluma_config()
        config=json.loads((runtime/'config'/'onebot.json').read_text(encoding='utf-8'))
        runtime_config=json.loads((runtime/'config'/'runtime.json').read_text(encoding='utf-8'))
        self.assertEqual(node,runtime/'node.exe')
        self.assertEqual(entry,runtime/'index.mjs')
        self.assertFalse(runtime_config['hookAutoLoad'])
        self.assertEqual(config['networks']['httpServers'][0]['host'],'127.0.0.1')
        self.assertEqual(config['networks']['httpServers'][0]['port'],13000)
        self.assertEqual(config['networks']['wsClients'][0]['url'],'ws://127.0.0.1:8080/onebot/v11/ws')
        self.assertEqual(config['networks']['wsClients'][0]['messageFormat'],'array')
        self.assertFalse(config['networks']['wsClients'][0]['reportSelfMessage'])
        self.assertFalse(config['historySync']['enabled'])
        self.assertFalse(env.get_bool('SNOWLUMA_ACCEPT_EULA',False))
        self.assertFalse(env.get_bool('SNOWLUMA_ACCEPT_PRIVACY',False))

    def test_history_sync_is_opt_in_and_overrides_existing_config_only_when_set(self):
        runtime=paths.ROOT/'integrations'/'snowluma-history-fixture'
        (runtime/'config').mkdir(parents=True,exist_ok=True)
        base=f"QQ_TRANSPORT=snowluma\nSNOWLUMA_DIR={runtime}\nPORT=8080\nSNOWLUMA_HTTP_TOKEN={'a'*43}\nONEBOT_V11_ACCESS_TOKEN={'b'*43}\n"
        _env.write_env(base)
        self.assertIsNone(qq_transport.history_sync_override())
        self.assertFalse(qq_transport.onebot_config()['historySync']['enabled'])
        path=qq_transport.write_snowluma_onebot_config()
        old=json.loads(path.read_text(encoding='utf-8'));old['historySync']={'enabled':True}
        path.write_text(json.dumps(old),encoding='utf-8')
        qq_transport.write_snowluma_onebot_config()
        self.assertTrue(json.loads(path.read_text(encoding='utf-8'))['historySync']['enabled'])  # 没写键：保留现有设置
        _env.write_env(base+'SNOWLUMA_HISTORY_SYNC=false\n')
        qq_transport.write_snowluma_onebot_config()
        self.assertFalse(json.loads(path.read_text(encoding='utf-8'))['historySync']['enabled'])
        _env.write_env(base+'SNOWLUMA_HISTORY_SYNC=true\n')
        self.assertTrue(qq_transport.onebot_config()['historySync']['enabled'])
        qq_transport.write_snowluma_onebot_config()
        self.assertTrue(json.loads(path.read_text(encoding='utf-8'))['historySync']['enabled'])

    def test_snowluma_ws_token_is_promoted_to_nonebot_access_token_before_start(self):
        from panel.services import envfile
        from panel.services import qq_transport as panel_transport
        import os
        runtime = paths.ROOT / "integrations" / "snowluma-token-fixture"
        runtime.mkdir(parents=True, exist_ok=True)
        (runtime / "node.exe").write_bytes(b"fixture")
        (runtime / "index.mjs").write_text("// fixture", encoding="utf-8")
        (runtime / "furina-install.json").write_text(
            json.dumps({"version": "2.2.1", "sha256": qq_transport.SNOWLUMA_RELEASE_SHA256}), encoding="utf-8"
        )
        token = "w" * 43
        _env.write_env(
            f"QQ_TRANSPORT=snowluma\nSNOWLUMA_DIR={runtime}\nSNOWLUMA_NODE={runtime / 'node.exe'}\n"
            f"SNOWLUMA_HTTP_TOKEN={'a' * 43}\nSNOWLUMA_WS_TOKEN={token}\nPORT=8080\n"
        )
        with patch.dict(os.environ, {"ONEBOT_V11_ACCESS_TOKEN": ""}):
            panel_transport.ensure_snowluma_config()
        self.assertEqual(envfile.get("ONEBOT_V11_ACCESS_TOKEN"), token)
        config = json.loads((runtime / "config" / "onebot.json").read_text(encoding="utf-8"))
        self.assertEqual(config["networks"]["wsClients"][0]["accessToken"], token)

    def test_snowluma_refuses_an_unrelated_existing_http_server(self):
        from panel.services import qq_transport as panel_transport
        runtime=paths.ROOT/'integrations'/'snowluma-v2.2.1';runtime.mkdir(parents=True,exist_ok=True)
        (runtime/'node.exe').write_bytes(b'fixture');(runtime/'index.mjs').write_text('//fixture',encoding='utf-8')
        (runtime/'furina-install.json').write_text(json.dumps({'version':'2.2.1','sha256':qq_transport.SNOWLUMA_RELEASE_SHA256}),encoding='utf-8')
        (runtime/'config').mkdir(exist_ok=True)
        (runtime/'config'/'onebot.json').write_text(json.dumps({'networks':{'httpServers':[{'name':'someone-elses-api'}],'wsClients':[]}}),encoding='utf-8')
        _env.write_env(f"QQ_TRANSPORT=snowluma\nSNOWLUMA_DIR={runtime}\nSNOWLUMA_NODE={runtime/'node.exe'}\nSNOWLUMA_HTTP_TOKEN={'a'*43}\nONEBOT_V11_ACCESS_TOKEN={'b'*43}\n")
        with self.assertRaises(FileExistsError):panel_transport.ensure_snowluma_config()

    def test_path_and_endpoint_are_loopback_only(self):
        _env.write_env('QQ_TRANSPORT=snowluma\nSNOWLUMA_HTTP_URL=https://evil.example:13000\n')
        with self.assertRaises(ValueError):qq_transport.onebot_url()
        _env.write_env('QQ_TRANSPORT=snowluma\nSNOWLUMA_HTTP_URL=https://127.0.0.1:13000\n')
        with self.assertRaises(ValueError):qq_transport.onebot_url()
        _env.write_env('QQ_TRANSPORT=snowluma\nSNOWLUMA_HTTP_URL=http://user:pass@127.0.0.1:13000\n')
        with self.assertRaises(ValueError):qq_transport.onebot_url()


if __name__=='__main__':unittest.main()

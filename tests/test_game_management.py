"""Offline checks for the upstream connector boundary; not live game/API acceptance."""
from tests import _env  # noqa: F401
from tests._classic_src import classic_panel_source
import contextlib
import json
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from furina.core import db, permissions
from furina.services import gscore
from panel.services import games
ROOT=Path(__file__).resolve().parents[1]


class UpstreamBoundaryTests(unittest.TestCase):
    def test_no_local_game_parser_or_scheduler_in_connector(self):
        text=(ROOT/'furina/plugins/mihoyo_service/__init__.py').read_text(encoding='utf-8')
        self.assertIn('require("GenshinUID")',text)
        for retired in ('furina.services.mihoyo','on_message(', 'scheduled_job', '_daily_tick', 'render_game_report'):
            self.assertNotIn(retired,text)

    def test_status_never_reads_accounts(self):
        db.put('mihoyo','accounts',{'10001':{'cookies':{'stoken':'DO-NOT-EXPOSE'}}})
        with patch.object(gscore,'processes',return_value=[]),patch('panel.services.winproc.port_open',return_value=False),patch.object(db,'get',side_effect=AssertionError('No account access')):
            result=games.snapshot()
        self.assertEqual(result['engine'],'GsCore')
        self.assertFalse(result['running'])
        self.assertNotIn('accounts',result)
        self.assertNotIn('DO-NOT-EXPOSE',json.dumps(result))
        self.assertEqual(result['console_url'],'http://127.0.0.1:8765/app')

    def test_chat_reservation_uses_upstream_namespaces(self):
        with patch.object(gscore,'_namespace_cache',(gscore.time.monotonic(),('ys','bt','zzz'),('#',))):
            self.assertTrue(gscore.reserved_for_core('#bt帮助'))
            self.assertTrue(gscore.reserved_for_core('ys签到'))
            self.assertFalse(gscore.reserved_for_core('今天吃什么'))
            self.assertTrue(gscore.reserved_for_core('绑定账号'))
            self.assertTrue(gscore.reserved_for_core('#指令帮助'))
            self.assertTrue(gscore.reserved_for_core('更新展柜'))
            self.assertFalse(gscore.reserved_for_core('绑定账号要怎么弄'))
            self.assertTrue(gscore.reserved_for_core('解除绑定'))
            self.assertTrue(gscore.reserved_for_core('绑定设备{"fp":"x","device_id":"y"}'))

    def test_legacy_writes_are_disabled(self):
        for data in ({'auto_sign':True},{'sign_time':'10:00'},{'cookies':{}}):
            with self.assertRaises(ValueError):games.save_settings(data)
            with self.assertRaises(ValueError):games.save_account_settings('10001',data)

    def test_native_ui_hosts_official_console(self):
        text=(ROOT/'panel/classic/games.py').read_text(encoding='utf-8')
        self.assertIn('QWebEngineView',text)
        self.assertIn('gscore.CONSOLE_URL',text)
        self.assertNotIn('render_game_report',text)
        self.assertNotIn('furina.services.mihoyo',text)

    def test_web_ui_is_only_service_entry(self):
        text=(ROOT/'panel/web/games.js').read_text(encoding='utf-8')
        self.assertIn('http://127.0.0.1:8765/app',text)
        self.assertNotIn('/api/games/account',text)
        self.assertNotIn('gmConfig',text)

    def test_permissions_and_navigation_remain(self):
        from panel.services import data
        self.assertIn('game_query',permissions.FEATURES)
        self.assertIn('game_query',[x[0] for x in data.KINDS])
        self.assertIn('游戏管理',classic_panel_source())
        self.assertIn('游戏管理',(ROOT/'panel/web/app.js').read_text(encoding='utf-8'))

    def test_platform_controls_do_not_stop_napcat(self):
        text=(ROOT/'furina/services/gscore.py').read_text(encoding='utf-8')
        self.assertNotIn('napcat.stop',text)
        self.assertNotIn('kill_tree',text)

    def test_foreign_port_is_not_replaced(self):
        with patch.object(gscore,'CORE_PYTHON',Mock(is_file=Mock(return_value=True))),patch.object(gscore,'_start_lock',return_value=contextlib.nullcontext()),patch.object(gscore,'snapshot',return_value={'port_conflict':True}),patch.object(gscore.subprocess,'Popen') as start:
            with self.assertRaises(RuntimeError):gscore.ensure_running()
            start.assert_not_called()

    def test_official_config_has_private_qr_and_no_automatic_updates(self):
        text=(ROOT/'tools/configure_gscore_upstream.py').read_text(encoding='utf-8')
        self.assertIn("'area':'DIRECT'",text)
        self.assertIn("settings.set_config('SchedSignin',False)",text)
        self.assertIn("'AutoUpdateCore'",text)


class NativeConsoleLayoutTests(unittest.TestCase):
    def test_zoom_fits_small_viewport_without_enlarging_large_one(self):
        from panel.classic.games import fitted_zoom
        self.assertEqual(fitted_zoom(1000,660),1.0)
        self.assertAlmostEqual(fitted_zoom(1000,528),0.8)
        self.assertAlmostEqual(fitted_zoom(800,800),0.8)
        self.assertEqual(fitted_zoom(1500,900),1.0)
        self.assertEqual(fitted_zoom(0,0),0.7)

    def test_official_auth_route_reserves_complete_registration_height(self):
        from panel.classic.games import fitted_zoom
        for width,height in [(775,512),(1070,599),(1311,736)]:
            zoom=fitted_zoom(width,height,auth_form=True)
            self.assertGreaterEqual(height/zoom,839)
            self.assertLessEqual(zoom,1.0)

    def test_manual_zoom_is_bounded_and_can_return_to_auto(self):
        from panel.classic.games import GameManagementPage
        model=Mock(_zoom=1.0,_auto_zoom=True)
        GameManagementPage.adjust_zoom(model,99)
        self.assertEqual(model._zoom,1.5)
        self.assertFalse(model._auto_zoom)
        GameManagementPage.adjust_zoom(model,-99)
        self.assertEqual(model._zoom,0.5)
        GameManagementPage.fit_console(model)
        self.assertTrue(model._auto_zoom)

    def test_host_has_opaque_surface_and_size_controls(self):
        text=classic_panel_source()
        self.assertIn('self.setAttribute(Qt.WA_TranslucentBackground, False)',text)
        self.assertIn('self.resize_grip = QSizeGrip(self)',text)
        self.assertIn('self.max_btn.clicked.connect(self._toggle_maximized)',text)
        method=text[text.index('    def _apply_page_mode'):text.index('    def closeEvent',text.index('    def _apply_page_mode'))]
        self.assertLess(method.index('self.detail_panel.layout().setContentsMargins'),method.index('if not force'))

    def test_browser_fallback_is_explicit_user_action(self):
        from panel.classic.games import GameManagementPage
        with patch('panel.classic.games.QDesktopServices.openUrl') as opener:
            GameManagementPage.open_browser(Mock())
        self.assertEqual(opener.call_args.args[0].toString(),gscore.CONSOLE_URL)


class GamesApiTests(unittest.TestCase):
    def test_api_guard_and_retirement(self):
        from fastapi.testclient import TestClient
        from panel import server
        client=TestClient(server.create_app());headers={'X-Panel-Token':server.TOKEN}
        with patch.object(games,'snapshot',return_value={'engine':'GsCore','ready':False}),patch.object(games,'ensure_running',return_value={'ready':True}) as start:
            self.assertEqual(client.get('/api/games').status_code,401)
            self.assertEqual(client.post('/api/games/start',json={}).status_code,401)
            start.assert_not_called()
            self.assertEqual(client.get('/api/games',headers=headers).json()['engine'],'GsCore')
            self.assertEqual(client.post('/api/games/start',headers=headers,json={}).status_code,200)
        self.assertEqual(client.post('/api/games/config',headers=headers,json={'auto_sign':True}).status_code,400)
        self.assertEqual(client.post('/api/games/account/10001',headers=headers,json={'cookies':{}}).status_code,400)
        self.assertEqual(client.get('/api/games/preview/sr',headers=headers).status_code,410)
        db.put('mihoyo','accounts',{'10001':{'cookies':{'stoken':'DO-NOT-DELETE'}}})
        self.assertEqual(client.delete('/api/mihoyo/account/10001',headers=headers).status_code,400)
        self.assertIn('10001',db.get('mihoyo','accounts'))


class PlatformGateTests(unittest.IsolatedAsyncioTestCase):
    async def test_existing_group_denial_is_preserved(self):
        import importlib
        from nonebot.adapters.onebot.v11 import GroupMessageEvent
        from nonebot.exception import IgnoredException
        plugin=importlib.import_module('furina.plugins.mihoyo_service')
        event=GroupMessageEvent.model_construct(group_id=10001,user_id=10002)
        with patch.object(plugin.permissions,'is_Allowed',return_value=False):
            with self.assertRaises(IgnoredException):await plugin._preserve_group_gate(Mock(plugin_name='GenshinUID'),event)
            await plugin._preserve_group_gate(Mock(plugin_name='unrelated_plugin'),event)

"""B站评论区回复：解析、表情过滤、楼中楼、艾特主人、已读记录、开关与面板。全部离线。"""
from tests import _env  # noqa: F401  (must be first)

import asyncio
import time
import unittest
from unittest import mock

from furina.core import db
from furina.services.bilibili import store as bili_store

_BASE = "BOT_QQ=30000002\nAI_CHAT_LLM_API_KEY=x\nAI_CHAT_LLM_BASE_URL=http://127.0.0.1:9\nBILI_REPLY_HOURS=0-24\n"


def _reset():
    for k in ("auth", "day", "halt", "watched", "posts", "meta", "live", "activity", "request", "reply_seen"):
        db.delete(bili_store.NS, k)


def _mod():
    from furina.plugins.bilibili import replies

    return replies


class ReplyParseTest(unittest.TestCase):
    def test_parse_array_and_object(self):
        r = _mod()
        raw = '好的\n[{"id":"1","reply":"哈哈你也太懂了[doge]","like":true,"at_partner":"true"},{"id":"#2","reply":"","like":false}]'
        out = r.parse_replies(raw)
        self.assertEqual([x["id"] for x in out], ["1", "2"])
        self.assertTrue(out[0]["like"] and out[0]["at_partner"])
        self.assertFalse(out[1]["at_partner"])
        self.assertEqual(r.parse_replies('{"replies":[{"id":"3","reply":"嗯"}]}')[0]["id"], "3")
        self.assertEqual(r.parse_replies("坏的"), [])

    def test_clean_reply_keeps_real_emotes_only(self):
        r = _mod()
        allowed = {"[doge]", "[笑哭]"}
        self.assertEqual(r.clean_reply("回复 @某人 :笑死[doge][乱写的]", allowed), "笑死[doge]")
        self.assertEqual(r.clean_reply("@主人 你看[笑哭][笑哭][笑哭][笑哭] https://x.cn", allowed), "你看[笑哭][笑哭][笑哭]")
        self.assertEqual(r.clean_reply("感谢回复~", allowed), "")
        self.assertEqual(r.clean_reply("作为一个AI我觉得", allowed), "")
        self.assertEqual(r.clean_reply("[doge]", allowed), "[doge]")


class ReplyRunTest(unittest.TestCase):
    def setUp(self):
        _reset()
        _env.write_env(_BASE + "BILI_MENTION_NAME=旅行者主人\nBILI_PARTNER_MID=777\n")
        bili_store.save_auth({"SESSDATA": "s", "bili_jct": "j", "DedeUserID": "1"}, uname="芙芙", mid="1")

    def tearDown(self):
        _env.write_env(_BASE)

    def _feed(self):
        now = int(time.time())
        return [
            # 有人直接回了她的评论（楼中楼）
            {"id": "n1", "mid": "42", "name": "路人甲", "oid": 100, "type": 1, "root": 9000, "rpid": 9001, "target": 9000,
             "text": "你这评论笑死我了", "mine": "三分钟那里绷不住了", "title": "猫猫做蛋糕",
             "uri": "https://www.bilibili.com/video/BV1xx411c7mD", "ts": now - 60},
            # 主人本人来回她
            {"id": "n2", "mid": "777", "name": "旅行者主人", "oid": 100, "type": 1, "root": 9000, "rpid": 9002, "target": 9000,
             "text": "老婆又在冲浪", "mine": "三分钟那里绷不住了", "title": "猫猫做蛋糕", "uri": "", "ts": now - 30},
            # 自己的消息、太旧的消息：跳过
            {"id": "n3", "mid": "1", "name": "芙芙", "oid": 100, "type": 1, "root": 9000, "rpid": 9003, "target": 9000,
             "text": "自己", "mine": "", "title": "", "uri": "", "ts": now},
            {"id": "n4", "mid": "43", "name": "老消息", "oid": 100, "type": 1, "root": 0, "rpid": 9004, "target": 0,
             "text": "很久以前", "mine": "", "title": "", "uri": "", "ts": now - 30 * 86400},
        ]

    def _run(self, llm_reply, feed=None):
        r = _mod()
        C = r.Client
        sent = mock.AsyncMock(return_value="555")
        liked = mock.AsyncMock()
        thread = mock.AsyncMock(return_value=[
            {"rpid": 9000, "mid": "1", "user": "芙芙", "text": "三分钟那里绷不住了", "ts": 1},
            {"rpid": 8999, "mid": "777", "user": "旅行者主人", "text": "我也看了", "ts": 2}])
        with mock.patch.object(C, "ensure_buvid", mock.AsyncMock()), \
                mock.patch.object(C, "reply_feed", mock.AsyncMock(return_value=feed if feed is not None else self._feed())), \
                mock.patch.object(C, "reply_thread", thread), mock.patch.object(C, "reply_to", sent), \
                mock.patch.object(C, "like_reply", liked), \
                mock.patch.object(C, "emotes", mock.AsyncMock(return_value=["[doge]", "[星星眼]", "[大哭]"])), \
                mock.patch.object(r.surf, "_llm", mock.AsyncMock(return_value=llm_reply)) as llm, \
                mock.patch.object(r.surf.asyncio, "sleep", mock.AsyncMock()):
            result = asyncio.run(r.run_replies(None, manual=True))
        return result, sent, liked, thread, llm

    def test_reply_with_partner_and_thread(self):
        out = ('[{"id":"1","reply":"主人快来，有人跟我说话了[星星眼]","like":true,"at_partner":true},'
               '{"id":"2","reply":"要你管[doge]","like":false,"at_partner":true}]')
        result, sent, liked, thread, llm = self._run(out)
        self.assertIn("回了 2 条", result)
        prompt = llm.await_args.args[1]
        self.assertIn("【主人】旅行者主人", prompt)          # 认出主人本人
        self.assertIn("这层楼前面", prompt)                  # 带上楼中楼上下文
        self.assertIn("[星星眼]", prompt)                    # 只给真实存在的表情
        self.assertNotIn("TA说：自己", prompt)
        self.assertNotIn("很久以前", prompt)
        thread.assert_awaited_once_with(100, 1, 9000)       # 同一层楼只取一次
        liked.assert_awaited_once_with(100, 1, 9001)
        (a1, k1), (a2, k2) = sent.await_args_list[0], sent.await_args_list[1]
        # 回路人：楼中楼前缀 + 艾特主人
        self.assertEqual(a1[:4], (100, 1, 9000, 9001))
        self.assertEqual(a1[4], "回复 @路人甲 :@旅行者主人 主人快来，有人跟我说话了[星星眼]")
        self.assertEqual(a1[5], {"旅行者主人": 777, "路人甲": 42})
        # 回主人本人：不会再艾特他自己
        self.assertEqual(a2[4], "回复 @旅行者主人 :要你管[doge]")
        self.assertEqual(bili_store.used("replies"), 2)
        posts = [p for p in bili_store.all_posts() if p["kind"] == "reply"]
        self.assertEqual({p["to"] for p in posts}, {"路人甲", "主人"})
        self.assertTrue(any(p.get("bvid") == "BV1xx411c7mD" for p in posts))
        self.assertTrue({"n1", "n2", "n3", "n4"} <= bili_store.reply_seen())
        # 再跑一次：都看过了
        result2, sent2, *_ = self._run(out)
        self.assertEqual(result2, "没有新回复")
        sent2.assert_not_awaited()

    def test_no_partner_configured(self):
        _env.write_env(_BASE)
        out = '[{"id":"1","reply":"真的假的","like":false,"at_partner":true}]'
        feed = self._feed()[:1]
        result, sent, *_rest, llm = self._run(out, feed)
        prompt = llm.await_args.args[1]
        self.assertNotIn("【主人】", prompt)
        self.assertNotIn("at_partner", prompt)
        self.assertEqual(sent.await_args.args[4], "回复 @路人甲 :真的假的")

    def test_root_comment_on_her_dynamic(self):
        feed = [{"id": "d1", "mid": "50", "name": "小王", "oid": 321, "type": 17, "root": 0, "rpid": 7001, "target": 0,
                 "text": "芙芙今天吃了啥", "mine": "", "title": "今天好困", "uri": "https://t.bilibili.com/321", "ts": int(time.time())}]
        result, sent, liked, thread, llm = self._run('[{"id":"1","reply":"草莓蛋糕！","like":true}]', feed)
        thread.assert_not_awaited()
        self.assertEqual(sent.await_args.args[:5], (321, 17, 7001, 7001, "草莓蛋糕！"))

    def test_dry_run_and_risk(self):
        _env.write_env(_BASE + "BILI_DRY_RUN=true\n")
        result, sent, liked, *_ = self._run('[{"id":"1","reply":"嗯嗯","like":true}]', self._feed()[:1])
        sent.assert_not_awaited()
        liked.assert_not_awaited()
        r = _mod()
        _reset()
        bili_store.save_auth({"SESSDATA": "s", "bili_jct": "j"})
        with mock.patch.object(r.Client, "ensure_buvid", mock.AsyncMock()), \
                mock.patch.object(r.Client, "reply_feed", mock.AsyncMock(side_effect=r.RiskControl(-352, "风控"))):
            self.assertEqual(asyncio.run(r.run_replies(None)), "触发风控，已暂停")
        self.assertTrue(bili_store.halted()[0])

    def test_should_check(self):
        r = _mod()
        ok, _ = r.should_check()
        self.assertTrue(ok)
        bili_store.set_meta("reply_next", time.time() + 600)
        self.assertFalse(r.should_check()[0])
        bili_store.set_meta("reply_next", 0)
        _env.write_env(_BASE + "BILI_REPLY_ENABLED=false\n")
        self.assertFalse(r.should_check()[0])
        _env.write_env(_BASE + "BILI_REPLY_DAILY_MAX=1\n")
        bili_store.bump("replies")
        self.assertFalse(r.should_check()[0])


class ReplyPanelTest(unittest.TestCase):
    def setUp(self):
        _reset()
        _env.write_env(_BASE)

    def test_settings_and_overview(self):
        from panel.services import bili_panel, huoren_settings as hs

        keys = {x["key"] for x in hs.get_group("bili")["fields"]}
        for k in ("BILI_REPLY_ENABLED", "BILI_REPLY_DAILY_MAX", "BILI_REPLY_CHECK_MINUTES", "BILI_REPLY_EMOTES",
                  "BILI_REPLY_LIKE", "BILI_AT_PARTNER", "BILI_PARTNER_MID", "BILI_MENTION_NAME", "BILI_REPLY_HOURS"):
            self.assertIn(k, keys)
        bili_store.add_post("reply", "嗯嗯", to="小王", their="你好", uri="https://t.bilibili.com/321")
        d = bili_panel.overview()
        self.assertEqual(d["caps"]["replies"], 0)
        self.assertEqual(d["posts"][0]["url"], "https://t.bilibili.com/321")
        self.assertIn("replies", bili_store.REQUEST_ACTIONS)
        self.assertEqual(bili_panel.request("replies")["request"]["action"], "replies")

    def test_frontend_knows_replies(self):
        from pathlib import Path

        js = (Path(__file__).resolve().parents[1] / "panel" / "web" / "app.js").read_text(encoding="utf-8")
        for needle in ('reply: ["回复"', 'id="blReply"', 'replies: "看回复"', '["replies", "回复"'):
            self.assertIn(needle, js)


class StoryboardAndDmTest(unittest.TestCase):
    def setUp(self):
        _reset()
        _env.write_env(_BASE + "BILI_MENTION_NAME=旅行者主人\nBILI_PARTNER_MID=777\n")
        bili_store.save_auth({"SESSDATA": "s", "bili_jct": "j", "DedeUserID": "1"}, uname="芙芙", mid="1")

    def tearDown(self):
        _env.write_env(_BASE)

    def test_storyboard_snapshot_and_pvdata_and_grid(self):
        import io
        import struct
        from PIL import Image
        from furina.services.bilibili import storyboard

        # 6 小时视频（21600秒）不会被误判成毫秒
        entries = storyboard._snapshot_entries({"index": [10, 3600, 21000]}, duration=21600)
        self.assertEqual(entries, [(10.0, 0), (3600.0, 1), (21000.0, 2)])
        # 毫秒时间戳（超过时长 1.5 倍）自动归一成秒
        entries_ms = storyboard._snapshot_entries({"index": [5000, 15000, 25000]}, duration=30)
        self.assertEqual(entries_ms, [(5.0, 0), (15.0, 1), (25.0, 2)])

        # pvdata 大端 uint16 兜底
        raw_pv = struct.pack(">4H", 3, 12, 24, 36)
        with mock.patch.object(storyboard, "_fetch_bytes", mock.AsyncMock(return_value=raw_pv)):
            pv_entries = asyncio.run(storyboard._entries_from_pvdata({"pvdata": "http://i0.hdslb.com/bfs/pv.bin"}))
        self.assertEqual(pv_entries, [(3.0, 0), (12.0, 1), (24.0, 2), (36.0, 3)])
        self.assertNotIn("Cookie", storyboard._image_headers())

        # 烧录编号与 3×3 拼图
        tile = Image.new("RGB", (480, 270), (60, 90, 140))
        grid_bytes = storyboard._assemble_grid([(tile, float(i * 10)) for i in range(1, 10)])
        self.assertTrue(len(grid_bytes) > 1000)

        # 句末标点截断
        txt = "第一句话很完整。第二句话也很完整！第三句话被截断在半路没有标点符号"
        self.assertEqual(storyboard.truncate_at_sentence(txt, 20), "第一句话很完整。第二句话也很完整！…")

    def test_style_guard_three_layers(self):
        from furina.plugins.bilibili import surf

        self.assertEqual(surf.clean_line("不得不说，这猫跳得比我还高", 60), "这猫跳得比我还高")
        self.assertEqual(surf.clean_line("值得一看！", 60), "")
        self.assertTrue(surf.check_comment_style("有一说一这视频真不错"))
        bili_store.add_post("comment", "三分半那个走位简直离谱笑死我了", bvid="BV1")
        self.assertIn("跟自己以前", surf.check_comment_style("三分半那个走位简直离谱笑死我"))
        hot = [{"text": "老板连夜扛着火车跑路了哈哈哈哈"}]
        self.assertIn("热评撞车", surf.check_comment_style("老板连夜扛着火车跑路了哈哈哈", hot_comments=hot))
        self.assertEqual(surf.check_comment_style("草莓蛋糕居然还能这么切，本芙宁娜学会了", hot_comments=hot), "")

    def test_owner_dm_two_way_and_share_card(self):
        r = _mod()
        from furina.services.bilibili import storyboard

        now = int(time.time())
        fake_msgs = [
            {"key": "k1", "ts": now - 20, "from_me": False, "kind": 7,
             "text": "[分享视频] 芙宁娜手书 / 某UP BV1xx411c7mD", "bvid": "BV1xx411c7mD"},
        ]
        bili_store.set_meta("dm_seen", ["k0"])
        fake_vd = {
            "ok": True, "bvid": "BV1xx411c7mD", "title": "芙宁娜手书", "up": "某UP",
            "duration": "02:30", "desc": "手书动画", "subtitle": "今夜星光闪耀。",
            "storyboard_scenes": "#1(00:15) 芙宁娜站在歌剧院舞台中央；#2(00:45) 灯光亮起。",
            "danmaku": ["太美了"], "hot_comments": ["网友(赞99)：神仙手书"],
        }
        send_dm = mock.AsyncMock(return_value="msg_key_123")
        with mock.patch.object(r.Client, "ensure_buvid", mock.AsyncMock()),                 mock.patch.object(r.Client, "dm_messages", mock.AsyncMock(return_value=fake_msgs)),                 mock.patch.object(r.Client, "send_dm", send_dm),                 mock.patch.object(r, "_partner", mock.AsyncMock(return_value=("旅行者主人", "777"))),                 mock.patch.object(storyboard, "watch_video_detail", mock.AsyncMock(return_value=fake_vd)) as wv,                 mock.patch.object(r.surf, "_llm", mock.AsyncMock(return_value="哼，第一幕歌剧院那个灯光确实很有本芙宁娜的排面！")) as llm,                 mock.patch.object(r.surf, "_pause", mock.AsyncMock()):
            out = asyncio.run(r.check_owner_dm())
            self.assertIn("回了旅行者主人的私信", out)
            wv.assert_awaited_once()
            send_dm.assert_awaited_once_with("777", "哼，第一幕歌剧院那个灯光确实很有本芙宁娜的排面！")
            self.assertIn("3×3画面分镜", llm.await_args.args[1])
            # 第二次轮询无新私信：0 token（不调大模型）
            llm.reset_mock()
            out2 = asyncio.run(r.check_owner_dm())
            self.assertEqual(out2, "")
            llm.assert_not_awaited()




class MiniAppCardShareTest(unittest.IsolatedAsyncioTestCase):
    def test_split_share_bubbles(self):
        from furina.services.bilibili.storyboard import split_share_bubbles

        b1 = split_share_bubbles("这个好诶｜@主人 能不能给我加一个")
        self.assertEqual(b1, ["这个好诶", "@主人 能不能给我加一个"])

        b2 = split_share_bubbles("这个好诶 @主人 能不能给我加一个")
        self.assertEqual(b2, ["这个好诶", "@主人 能不能给我加一个"])

        b3 = split_share_bubbles(["这个好诶 https://www.bilibili.com/video/BV1xx411c7mD", "@主人 能不能给我加一个"])
        self.assertEqual(b3, ["这个好诶", "@主人 能不能给我加一个"])

    def test_surf_clean_line_keeps_at_for_share(self):
        from furina.plugins.bilibili import surf

        self.assertEqual(
            surf.clean_line("这个好诶｜@主人 能不能给我加一个", 60, keep_at=True),
            "这个好诶｜@主人 能不能给我加一个",
        )
        self.assertEqual(
            surf.clean_line("这个好诶 @主人", 60, keep_at=False),
            "这个好诶",
        )

    async def test_send_bili_card_and_bubbles_with_miniapp_ark(self):
        from furina.services.bilibili.storyboard import send_bili_card_and_bubbles

        class FakeBot:
            async def call_api(self, api: str, **kwargs):
                if api == "get_mini_app_ark":
                    return {
                        "app": "com.tencent.miniapp_01",
                        "view": "view_8C8E89B49BE609866298ADDFF2DBABA4",
                        "prompt": f"[QQ小程序]{kwargs.get('desc')}",
                        "meta": {
                            "detail_1": {
                                "appid": "1109937557",
                                "title": kwargs.get("title"),
                                "desc": kwargs.get("desc"),
                                "preview": kwargs.get("picUrl"),
                                "qqdocurl": kwargs.get("jumpUrl"),
                            }
                        },
                    }
                return {}

        sent_msgs = []

        async def fake_sender(msg):
            sent_msgs.append(msg)
            return {"message_id": len(sent_msgs)}

        async def fast_pause(_b):
            return None

        info = {
            "ok": True,
            "bvid": "BV1xx411c7mD",
            "url": "https://www.bilibili.com/video/BV1xx411c7mD",
            "title": "用 OpenClaw 捏出来的 AI 桌宠",
            "desc": "UP主：测试UP",
            "pic": "https://i0.hdslb.com/bfs/archive/test.jpg",
        }
        res = await send_bili_card_and_bubbles(
            FakeBot(),
            fake_sender,
            info,
            comments=["这个好诶", "@主人 能不能给我加一个"],
            owner_qq="200000001",
            pause_fn=fast_pause,
        )
        self.assertTrue(res["ok"])
        self.assertTrue(res["card"])
        self.assertEqual(res["sent"], 3)
        self.assertEqual(len(sent_msgs), 3)
        # 第1条是 json 卡片
        self.assertEqual(sent_msgs[0][0].type, "json")
        self.assertIn("com.tencent.miniapp_01", sent_msgs[0][0].data["data"])
        self.assertIn("用 OpenClaw 捏出来的 AI 桌宠", sent_msgs[0][0].data["data"])
        # 第2条是短评气泡
        self.assertEqual(str(sent_msgs[1]), "这个好诶")
        # 第3条是 @主人 + 短评气泡
        self.assertEqual(sent_msgs[2][0].type, "at")
        self.assertEqual(str(sent_msgs[2][0].data["qq"]), "200000001")
        self.assertIn("能不能给我加一个", str(sent_msgs[2]))


if __name__ == "__main__":
    unittest.main()

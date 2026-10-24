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


if __name__ == "__main__":
    unittest.main()

"""B站：答应关注群友就真关注、逛「大肥鱼」标签、抖S 让主人总结视频。全部离线（B 站接口全是假的）。"""
from tests import _env  # noqa: F401  (must be first)

import asyncio
import unittest
from pathlib import Path
from unittest import mock

from furina.core import db
from furina.services.bilibili import follow, store as bili_store

ROOT = Path(__file__).resolve().parents[1]
_BASE = "BOT_QQ=30000002\nAI_CHAT_LLM_API_KEY=x\nAI_CHAT_LLM_BASE_URL=http://127.0.0.1:9\n"


def _reset():
    for k in ("auth", "day", "halt", "watched", "posts", "meta", "live", "activity", "followed"):
        db.delete(bili_store.NS, k)
    bili_store.save_auth({"SESSDATA": "s", "bili_jct": "j", "DedeUserID": "1"}, uname="芙芙", mid="1")


class FakeCli:
    def __init__(self, *a, **kw):
        self.followed, self.comments = [], []
        FakeCli.last = self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def ensure_buvid(self):
        pass

    async def find_user_mid(self, name):
        return "4242" if name == "小鱼干" else ""

    async def user_card(self, mid):
        return {"mid": str(mid), "name": "小鱼干", "following": str(mid) == "999"}

    async def follow(self, mid):
        self.followed.append(str(mid))

    async def comment(self, aid, text, at=None):
        self.comments.append((text, at))
        return "1"

    async def like(self, aid):
        pass


class ParseTargetTest(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(follow.parse_target("https://space.bilibili.com/12345?spm=1"), ("mid", "12345"))
        self.assertEqual(follow.parse_target("", "我的UID：778899，关注我"), ("mid", "778899"))
        self.assertEqual(follow.parse_target("", "芙芙堂主我的b站名是小鱼干，记得关注"), ("name", "小鱼干"))
        self.assertEqual(follow.parse_target("小鱼干"), ("name", "小鱼干"))
        self.assertEqual(follow.parse_target("", ""), ("", ""))


class FollowTest(unittest.TestCase):
    def setUp(self):
        _reset()
        _env.write_env(_BASE)
        p = mock.patch.object(follow, "Client", FakeCli)
        p.start()
        self.addCleanup(p.stop)

    def run_(self, *a, **kw):
        return asyncio.run(follow.follow_member(*a, **kw))

    def test_follow_by_name_and_once(self):
        r = self.run_("小鱼干", qq="555", gid=1)
        self.assertTrue(r["ok"])
        self.assertEqual(FakeCli.last.followed, ["4242"])
        self.assertEqual(bili_store.used("follows"), 1)
        r = self.run_("4242")
        self.assertTrue(r.get("already"))
        self.assertEqual(FakeCli.last.followed, [])

    def test_daily_cap_and_switch(self):
        _env.write_env(_BASE + "BILI_FOLLOW_DAILY_MAX=1\n")
        self.assertTrue(self.run_("123")["ok"])
        r = self.run_("456")
        self.assertFalse(r["ok"])
        self.assertIn("今天", r["reason"])
        _env.write_env(_BASE + "BILI_FOLLOW_ENABLED=false\n")
        self.assertFalse(self.run_("789")["ok"])

    def test_already_following_dry_run_and_unknown(self):
        self.assertTrue(self.run_("999").get("already"))
        self.assertFalse(self.run_("没这个人")["ok"])
        _env.write_env(_BASE + "BILI_DRY_RUN=true\n")
        r = self.run_("321")
        self.assertTrue(r["ok"] and r.get("note"))
        self.assertEqual(FakeCli.last.followed, [])
        bili_store.halt("测试", 1)
        self.assertFalse(self.run_("321")["ok"])


class KernelToolTest(unittest.TestCase):
    def test_bili_follow_tool(self):
        from furina.plugins.chat import kernel

        st = kernel._state("10009", 559)
        st.unread.append({"seq": 1, "mid": "300", "uid": "555", "name": "阿鱼", "text": "芙芙关注我b站 UID:4242",
                          "ts": 0, "at": True, "reply": "", "image": False, "bot": False})
        ctx = kernel.RunCtx("group", "10009", 559)
        kernel._runs["c" * 32] = ctx
        try:
            with mock.patch.object(follow, "follow_member", mock.AsyncMock(return_value={"ok": True, "name": "阿鱼的号"})) as fm:
                r = asyncio.run(kernel.handle_tool_callback({"run_id": "c" * 32, "name": "bili_follow",
                                                             "arguments": {"message": "m300"}}))
            self.assertTrue(r["ok"])
            self.assertEqual(fm.call_args.args[1], "芙芙关注我b站 UID:4242")
            r = asyncio.run(kernel.handle_tool_callback({"run_id": "c" * 32, "name": "bili_follow",
                                                         "arguments": {"message": "m404"}}))
            self.assertFalse(r["ok"])
        finally:
            kernel._runs.pop("c" * 32, None)
        bridge = (ROOT / "tools/dsh_bridge/furina-bridge.mjs").read_text(encoding="utf-8")
        self.assertIn("['bili_follow'", bridge)
        self.assertIn("bili_follow", kernel.GROUP_RULES if hasattr(kernel, "GROUP_RULES") else
                      (ROOT / "furina/plugins/chat/kernel.py").read_text(encoding="utf-8"))


class SurfExtrasTest(unittest.TestCase):
    def setUp(self):
        _reset()

    def test_roam_filter(self):
        from furina.plugins.bilibili import surf

        found = [{"bvid": "BV1", "title": "今天的饭", "tags": "DeepSeek,大肥鱼"},
                 {"bvid": "BV2", "title": "大肥鱼又吃白饭", "tags": ""},
                 {"bvid": "BV3", "title": "钓鱼", "tags": "鱼"}]
        self.assertEqual([v["bvid"] for v in surf.roam_filter(found, "大肥鱼")], ["BV1", "BV2"])
        _env.write_env(_BASE)
        self.assertEqual(surf.cfg()["roam_tags"], ["大肥鱼"])

    def test_homework_comment_ats_partner_once_a_day(self):
        from furina.plugins.bilibili import surf

        _env.write_env(_BASE + "BILI_MENTION_NAME=旅行者\nBILI_PARTNER_MID=777\nBILI_HOMEWORK_DAILY_MAX=1\n")
        raw = ('{"videos":[{"bvid":"BV1","interest":7,"thought":"干货","like":false,"comment":"讲得好",'
               '"homework":"给我总结成三句话，今晚交"}]}')
        r = surf.parse_round(raw)["videos"]
        self.assertEqual(r[0]["homework"], "给我总结成三句话，今晚交")
        c = surf.cfg()
        by_id = {"BV1": {"bvid": "BV1", "aid": 1, "title": "干货视频", "duration": 100}}
        cli = FakeCli()
        with mock.patch.object(surf, "_pause", mock.AsyncMock()):
            asyncio.run(surf._act(cli, c, r, by_id, None, True))
        self.assertEqual(cli.comments, [("@旅行者 给我总结成三句话，今晚交", {"旅行者": 777})])
        self.assertEqual(bili_store.used("homework"), 1)
        # 今天的作业布置过了：再刷到就只发普通评论
        r2 = [dict(r[0], bvid="BV2")]
        by_id2 = {"BV2": dict(by_id["BV1"], bvid="BV2")}
        with mock.patch.object(surf, "_pause", mock.AsyncMock()):
            asyncio.run(surf._act(cli, c, r2, by_id2, None, True))
        self.assertEqual(cli.comments[-1], ("讲得好", None))

    def test_uid_only_partner_gets_name(self):
        from furina.plugins.bilibili import replies

        _env.write_env(_BASE + "BILI_PARTNER_MID=344\n")
        name, mid = asyncio.run(replies._partner(FakeCli(), replies.cfg()))
        self.assertEqual((name, mid), ("小鱼干", "344"))

    def test_defaults_are_unlimited(self):
        from furina.plugins.bilibili import surf

        _env.write_env(_BASE)
        self.assertEqual(follow.daily_max(), 0)
        self.assertEqual(surf.cfg()["homework_max"], 0)

    def test_settings_defaults_match(self):
        from furina.plugins.bilibili import surf
        from panel.services import huoren_settings as hs

        _env.write_env(_BASE)
        f = {x[0]: x for x in hs.BILI}
        c = surf.cfg()
        self.assertEqual(f["BILI_ROAM_TAGS"][3], "大肥鱼")
        self.assertEqual(f["BILI_ROAM_PROBABILITY"][3], c["roam_prob"])
        self.assertEqual(f["BILI_HOMEWORK_DAILY_MAX"][3], c["homework_max"])
        self.assertEqual(f["BILI_FOLLOW_DAILY_MAX"][3], follow.daily_max())
        self.assertEqual(f["BILI_FOLLOW_ENABLED"][3], follow.enabled())
        sec = [k for t, _d, keys in hs.SECTIONS["bili"] for k in keys]
        for k in ("BILI_ROAM_TAGS", "BILI_HOMEWORK_ENABLED", "BILI_FOLLOW_ENABLED", "BILI_FOLLOW_DAILY_MAX"):
            self.assertIn(k, sec)


if __name__ == "__main__":
    unittest.main()

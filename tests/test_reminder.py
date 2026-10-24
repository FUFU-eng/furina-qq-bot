"""定时提醒离线测试（沿用旧项目 tests/test_reminder.py，改为导入 v2 模块）。"""
import asyncio
from datetime import datetime,timedelta
import json
from pathlib import Path
import sys
import tempfile
import unittest
from tests import _env  # noqa: F401,E402
from furina.services.reminder.domain import CST,Clarify,make_proposal,next_occurrence,from_model
from furina.services.reminder.parser import local_create,management,candidate,model_messages,parse_model_reply
from furina.services.reminder.settings import load,save,DEFAULTS
from furina.services.reminder.store import Store
from furina.services.reminder.engine import tick

NOW=datetime(2026,9,21,10,0,tzinfo=CST)


class ParserTests(unittest.TestCase):
    def test_relative(self):
        for t,seconds in [('20分钟后提醒我收菜',1200),('请半小时后叫我喝水',1800),
                          ('提醒我一小时二十分钟后收菜',4800),('一个半小时以后通知我开会',5400),
                          ('明天晚上8点提醒我看电影',34*3600)]:
            p=local_create(t,NOW);self.assertIsNotNone(p,t);self.assertEqual(p.next_at,NOW.timestamp()+seconds,t)

    def test_content(self):
        p=local_create('给我在20分钟后提醒我收菜',NOW)
        self.assertEqual(p.text,'收菜');self.assertEqual(p.kind,'once')
        self.assertEqual(local_create('20分钟后提醒我去掉多余文件',NOW).text,'去掉多余文件')

    def test_daily(self):
        p=local_create('每天上午9点提醒我喝水',NOW)
        self.assertEqual(p.kind,'daily');self.assertEqual(p.clock,'09:00')
        self.assertEqual(datetime.fromtimestamp(p.next_at,CST),datetime(2026,9,22,9,tzinfo=CST))

    def test_weekly(self):
        p=local_create('每周日下午3点提醒我整理照片',NOW)
        self.assertEqual((p.kind,p.clock,p.weekdays),('weekly','15:00',(6,)))
        self.assertEqual(datetime.fromtimestamp(p.next_at,CST),datetime(2026,9,27,15,tzinfo=CST))

    def test_workdays(self):
        p=local_create('每个工作日上午8点提醒我打卡',NOW)
        self.assertEqual(p.weekdays,(0,1,2,3,4))

    def test_specific_date(self):
        p=local_create('2026年10月1日20:30提醒我看电影',NOW)
        self.assertEqual(datetime.fromtimestamp(p.next_at,CST),datetime(2026,10,1,20,30,tzinfo=CST))

    def test_next_week(self):
        p=local_create('下周一上午9点叫我交作业',NOW)
        self.assertEqual(datetime.fromtimestamp(p.next_at,CST),datetime(2026,9,28,9,tzinfo=CST))

    def test_half_clock(self):
        p=local_create('明天晚上八点半喊我上线',NOW)
        self.assertEqual(datetime.fromtimestamp(p.next_at,CST).hour,20)
        self.assertEqual(datetime.fromtimestamp(p.next_at,CST).minute,30)

    def test_ambiguous_no_guess(self):
        for t in ['明天8点提醒我喝水','昨天晚上8点提醒我喝水','2026年2月30日20:00提醒我吃饭','每天8点提醒我喝水']:
            with self.assertRaises(Clarify,msg=t):local_create(t,NOW)

    def test_negation(self):
        with self.assertRaises(Clarify):local_create('不要提醒我明天晚上8点喝水',NOW)

    def test_unsupported_needs_model(self):
        for t in ['有空的时候叫我喝水','下下周一上午8点提醒我打卡',
                  '每周一和周三晚上8点提醒我收菜','三月十五日晚上8点提醒我开会']:
            self.assertIsNone(local_create(t,NOW),t)

    def test_monthly_parsed_locally(self):
        # 每月提醒已支持本地解析（HELP：每月15号上午9点提醒我交账单）
        p=local_create('每个月15号20:00提醒我交费',NOW)
        self.assertEqual((p.kind,p.monthday,p.clock,p.text),('monthly',15,'20:00','交费'))

    def test_routing(self):
        for t in ['来两张原神的图','来两张图','搜图','强化','你好']:
            self.assertFalse(candidate(t))
        self.assertFalse(candidate('好的'))
        # 她刚问过「是几点叫你吧」：回个「好的」「对」就算答应；没问过就不拦
        self.assertTrue(candidate('好的',True))
        self.assertTrue(candidate('对的',True))
        self.assertTrue(candidate('确认提醒',True))
        self.assertTrue(candidate('过一会儿叫我收菜'))
        self.assertTrue(candidate('20分钟后给我发条消息说该喝水了'))
        self.assertFalse(candidate('明天会下雨吗告诉我'))

    def test_manage(self):
        self.assertEqual(management('帮我取消提醒12'),('cancel',12))
        self.assertEqual(management('删除第12号提醒'),('cancel',12))
        self.assertEqual(management('帮我把提醒12取消'),('cancel',12))
        self.assertEqual(management('取消刚才那个提醒'),('cancel_latest',None))
        self.assertEqual(management('看看我的提醒'),('list',None))

    def test_model_validation(self):
        good={'action':'create','target':'self','kind':'once','at':'2026-09-21T12:00:00+08:00','text':'吃饭'}
        self.assertEqual(from_model(good,NOW).text,'吃饭')
        for key,value in [('target','other'),('at','2026-09-21T12:00:00'),('at','2020-01-01T00:00:00+08:00'),('kind','hourly')]:
            with self.assertRaises(Clarify):from_model(dict(good,**{key:value}),NOW)
        with self.assertRaises(Clarify):parse_model_reply('not json',NOW)

    def test_dynamic_date_and_schema(self):
        msg=model_messages('提醒我开会',NOW)
        self.assertIn(NOW.isoformat(),msg[1]['content'])
        next_year=NOW.replace(year=2027)
        self.assertIn('2027',model_messages('x',next_year)[1]['content'])
        with self.assertRaises(Clarify):from_model({'action':'clarify','question':'几点？'},NOW)

    def test_midnight_and_cross_year(self):
        now=datetime(2026,12,31,23,30,tzinfo=CST)
        p=local_create('明天凌晨0点提醒我看烟花',now)
        self.assertEqual(datetime.fromtimestamp(p.next_at,CST),datetime(2027,1,1,0,0,tzinfo=CST))
        with self.assertRaises(Clarify):local_create('明天凌晨12点提醒我吃饭',NOW)

    def test_every_week_preserved_after_send(self):
        p=make_proposal('x','weekly',NOW,clock='11:00',weekdays=[0,2])
        after=datetime(2026,9,21,11,1,tzinfo=CST)
        nxt=next_occurrence(p.schedule(),after)
        self.assertEqual(datetime.fromtimestamp(nxt,CST),datetime(2026,9,23,11,0,tzinfo=CST))

    def test_min_delay_and_horizon(self):
        for seconds in (1,29,367*86400):
            with self.assertRaises(Clarify):make_proposal('x','once',NOW,NOW+timedelta(seconds=seconds))


class SettingsTests(unittest.TestCase):
    def test_defaults_and_reload(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'cfg.json';self.assertFalse(load(p)['enabled'])
            save(dict(DEFAULTS,enabled=True),p);self.assertTrue(load(p)['enabled'])
            self.assertEqual(load(p)['grace_minutes'],30)
            with self.assertRaises(ValueError):save(dict(DEFAULTS,grace_minutes=31),p)


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)/'tasks.db';self.s=Store(self.path)
        self.p=make_proposal('喝水','once',NOW,NOW+timedelta(minutes=20))
    def tearDown(self):self.tmp.cleanup()
    def create(self,source='1',gid='g',uid='u',limit=10):return self.s.create(gid,uid,self.p,NOW,source,limit)

    def test_persistence_and_duplicate_event(self):
        row,new=self.create();self.assertTrue(new)
        other=Store(self.path);row2,new=other.create('g','u',self.p,NOW,'1')
        self.assertFalse(new);self.assertEqual(row['id'],row2['id']);self.assertEqual(len(other.list_own('g','u')),1)

    def test_owner_and_group_isolation(self):
        row,_=self.create()
        self.assertEqual(self.s.list_own('g','someone'),[])
        self.assertIsNone(self.s.cancel('g','someone',row['id'],NOW))
        self.assertIsNone(self.s.cancel('else','u',row['id'],NOW))
        self.assertIsNotNone(self.s.cancel('g','u',row['id'],NOW))

    def test_atomic_quota(self):
        self.create(limit=1)
        with self.assertRaises(Clarify):self.create('2',limit=1)
        self.create('3',uid='v',limit=1)

    def test_claim_and_cancel_race(self):
        row,_=self.create();self.assertTrue(self.s.claim(row,NOW));self.assertFalse(self.s.claim(row,NOW))
        self.s.cancel('g','u',row['id'],NOW)
        self.assertFalse(self.s.claimed(row));self.assertFalse(self.s.advance(row,NOW,'sent'))

    def test_orphan_no_replay(self):
        row,_=self.create();self.s.claim(row,NOW)
        self.s.recover(NOW+timedelta(minutes=6))
        self.assertEqual(self.s.list_own('g','u')[0]['last_status'],'unknown_after_restart')
        self.assertEqual(self.s.due(NOW+timedelta(hours=1)),[])


class HumanlikeTests(unittest.TestCase):
    """群里「十二点记得提醒我打脚」：别机械地问上午还是晚上、别给「明天晚上8点」这种例子（聊天那边会照抄成「八点」）。"""

    def test_nearest_clock_without_day(self):
        now=datetime(2026,10,23,11,10,tzinfo=CST)
        p=local_create('十二点记得提醒我打脚',now)
        self.assertEqual(datetime.fromtimestamp(p.next_at,CST),datetime(2026,10,23,12,0,tzinfo=CST))
        self.assertEqual(p.text,'打脚')
        p=local_create('十二点提醒我打脚',datetime(2026,10,23,13,0,tzinfo=CST))
        self.assertEqual(datetime.fromtimestamp(p.next_at,CST),datetime(2026,10,24,0,0,tzinfo=CST))
        self.assertEqual(datetime.fromtimestamp(local_create('8点提醒我收菜',now).next_at,CST).hour,20)

    def test_tonight_twelve_is_midnight(self):
        now=datetime(2026,10,23,11,10,tzinfo=CST)
        for t in ('今晚12点提醒我睡觉','晚上十二点提醒我打脚','晚上十二点记得提醒我打脚'):
            self.assertEqual(datetime.fromtimestamp(local_create(t,now).next_at,CST),datetime(2026,10,24,0,0,tzinfo=CST),t)

    def test_ask_period_like_a_person_and_fill_in(self):
        from furina.services.reminder.parser import NeedPeriod,add_period
        with self.assertRaises(NeedPeriod) as cm:local_create('明天8点提醒我喝水',NOW)
        self.assertEqual(str(cm.exception),'早上8点还是晚上8点呀')
        self.assertNotIn('例如',str(cm.exception))
        p=local_create(add_period('明天8点提醒我喝水','晚上'),NOW)
        self.assertEqual(datetime.fromtimestamp(p.next_at,CST),datetime(2026,9,22,20,0,tzinfo=CST))

    def test_delivery_sounds_like_a_friend(self):
        from furina.services.reminder.engine import say
        import random as _r
        for i in range(5):
            t=say('打脚',0,0,_r.Random(i))
            self.assertIn('打脚',t);self.assertNotIn('#',t);self.assertNotIn('北京时间',t)


class EngineTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.s=Store(Path(self.tmp.name)/'tasks.db');self.sent=[]
    def tearDown(self):self.tmp.cleanup()
    def add(self,kind='once'):
        p=make_proposal('吃饭',kind,NOW,NOW+timedelta(minutes=1),clock='10:01',weekdays=(0,))
        return self.s.create('g','u',p,NOW,'1')[0]
    async def send(self,uid,text):self.sent.append((uid,text))
    async def test_on_time_once(self):
        self.add();at=NOW+timedelta(minutes=1)
        await tick(self.s,at,{'g':self.send},lambda g:True)
        await tick(self.s,at,{'g':self.send},lambda g:True)
        self.assertEqual(len(self.sent),1);self.assertEqual(self.sent[0][0],'u')
    async def test_late_30_minutes(self):
        self.add();await tick(self.s,NOW+timedelta(minutes=31),{'g':self.send},lambda g:True)
        self.assertEqual(len(self.sent),1);self.assertIn('迟到',self.sent[0][1])
    async def test_past_grace_skips_once(self):
        self.add();await tick(self.s,NOW+timedelta(minutes=31,seconds=1),{'g':self.send},lambda g:True)
        self.assertEqual(self.sent,[]);self.assertEqual(self.s.list_own('g','u')[0]['last_status'],'skipped_late')
    async def test_recurring_skips_backlog(self):
        self.add('daily');at=NOW+timedelta(days=4,hours=2)
        await tick(self.s,at,{'g':self.send},lambda g:True)
        row=self.s.list_own('g','u')[0]
        self.assertEqual(self.sent,[]);self.assertEqual(row['state'],'active');self.assertGreater(row['next_at'],at.timestamp())
    async def test_offline_then_online(self):
        self.add();await tick(self.s,NOW+timedelta(minutes=2),{},lambda g:True)
        self.assertEqual(self.s.list_own('g','u')[0]['state'],'active')
        await tick(self.s,NOW+timedelta(minutes=3),{'g':self.send},lambda g:True)
        self.assertEqual(len(self.sent),1)
    async def test_permission_disabled(self):
        self.add();await tick(self.s,NOW+timedelta(minutes=2),{'g':self.send},lambda g:False)
        self.assertEqual(self.sent,[])
    async def test_unknown_no_replay(self):
        self.add();calls=[]
        async def uncertain(*args):calls.append(1);raise TimeoutError()
        at=NOW+timedelta(minutes=1)
        await tick(self.s,at,{'g':uncertain},lambda g:True)
        await tick(self.s,at,{'g':uncertain},lambda g:True)
        self.assertEqual(calls,[1]);self.assertEqual(self.s.list_own('g','u')[0]['last_status'],'unknown_delivery')
    async def test_clock_moves_while_sending(self):
        self.add();at=NOW+timedelta(minutes=1)
        await tick(self.s,at,{'g':self.send},lambda g:True,clock=lambda:at+timedelta(hours=1))
        self.assertEqual(self.sent,[])


if __name__=='__main__':unittest.main()

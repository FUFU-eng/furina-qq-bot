"""Pixiv 离线测试（沿用旧项目 tests/test_pixiv.py，改为导入 v2 模块）。"""
import asyncio
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta

from tests import _env  # noqa: F401,E402  (临时数据目录)
from furina.services.pixiv import settings
from furina.services.pixiv.schedule import board_for, rank_bounds, due_batches, CST
from furina.core.pixiv_intent import parse_request, queries_for
from furina.services.pixiv.storage import Store
from furina.services.pixiv.delivery import send_work, SendUncertain
from furina.services.pixiv.provider import Client, PixivError, check_image_url


class CalendarTests(unittest.TestCase):
    def test_daily(self):
        # 日榜约北京时间 11 点才公布前一天的榜，9 点批次固定发“前天榜”（schedule.board_for）
        b = board_for(date(2026, 9, 21))
        self.assertEqual((b.mode, b.date), ('daily', date(2026, 9, 19)))

    def test_weekly(self):
        # 周日固定查上周日所在周的周榜（本周榜周一中午才公布）
        b = board_for(date(2026, 9, 20))
        self.assertEqual((b.mode, b.date), ('weekly', date(2026, 9, 13)))

    def test_monthly(self):
        b = board_for(date(2026, 9, 15))
        self.assertEqual((b.mode, b.date), ('monthly', date(2026, 8, 31)))

    def test_collision(self):
        self.assertEqual(board_for(date(2026, 11, 15)).mode, 'weekly')
        b = board_for(date(2026, 11, 16))
        self.assertEqual((b.mode, b.date), ('monthly', date(2026, 10, 31)))

    def test_year_and_leap(self):
        self.assertEqual(board_for(date(2027, 1, 15)).date, date(2026, 12, 31))
        self.assertEqual(board_for(date(2028, 3, 15)).date, date(2028, 2, 29))

    def test_ranks(self):
        self.assertEqual([rank_bounds(i) for i in range(5)], [(1,30),(31,60),(61,90),(91,120),(121,150)])

    def test_no_catchup(self):
        now = datetime(2026, 9, 21, 9, 0, 20, tzinfo=CST)
        self.assertEqual(due_batches(now, now - timedelta(hours=1), settings.DEFAULTS['times']), [0])
        self.assertEqual(due_batches(now, now - timedelta(seconds=10), settings.DEFAULTS['times']), [])
        self.assertEqual(due_batches(now + timedelta(minutes=2), now - timedelta(hours=1), settings.DEFAULTS['times']), [])


class IntentTests(unittest.TestCase):
    def test_named(self):
        for text, query, count in [('给我来两张原神的图','原神',2), ('来10张神里绫华的图','神里绫华',10),
                                   ('发一张芙宁娜的图片','芙宁娜',1), ('给我发张原神图','原神',1)]:
            r = parse_request(text)
            self.assertIsNotNone(r, text)
            self.assertEqual((r.query,r.count),(query,count), text)

    def test_eagle(self):
        for text in ['来几张图','来两张图','发张图','来个表情包','来张好看的图','原神今天更新了']:
            self.assertIsNone(parse_request(text), text)

    def test_limits(self):
        r = parse_request('来二十张原神的图')
        self.assertEqual(r.count,10); self.assertTrue(r.clamped)
        for _ in range(10):
            self.assertIn(parse_request('来几张绫华的图').count, [1,2,3])

    def test_alias(self):
        self.assertIn('神里綾華',queries_for('绫华'))
        self.assertEqual(queries_for('未知角色'), ['未知角色'])
        self.assertEqual(queries_for('某角色', {'某角色':['タグ']}),['タグ','某角色'])


class SettingsTests(unittest.TestCase):
    def test_default_off(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertFalse(settings.load(Path(d)/'none.json')['enabled'])

    def test_save_reload(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'settings.json'; settings.save(dict(settings.DEFAULTS),p)
            self.assertEqual(settings.load(p)['times'],settings.DEFAULTS['times'])

    def test_bad_time(self):
        for times in [['09:00']*5, ['25:00','13:00','16:00','20:00','23:00']]:
            with self.assertRaises(ValueError):settings.validate(dict(settings.DEFAULTS,times=times))

    def test_corrupt(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'bad.json';p.write_text('{')
            with self.assertRaises(ValueError):settings.load(p)


class StoreTests(unittest.TestCase):
    def test_claim_once(self):
        with tempfile.TemporaryDirectory() as d:
            a=Store(d);b=Store(d)
            self.assertTrue(a.claim('same'))
            self.assertFalse(b.claim('same'))
            a.finish('same','completed','done')
            self.assertEqual(b.recent()[0][1],'completed')

    def test_own_cache_only(self):
        with tempfile.TemporaryDirectory() as d:
            store=Store(Path(d)/'store'); p=store.temp_dir(); (p/'x').write_bytes(b'123')
            other=Path(d)/'qq';other.mkdir();(other/'file').write_text('keep')
            store.cleanup(age_seconds=0)
            self.assertEqual(store.usage(),0)
            self.assertTrue((other/'file').exists())
            with self.assertRaises(ValueError):store.remove_temp(other)


def work_bytes(store):
    """work-* 临时目录（每次发送的工作副本）占用；asset-* 是跨群复用的原图缓存，按设计保留 3 小时/配额内。"""
    total = 0
    for d in store.cache.glob('work-*'):
        total += sum(p.stat().st_size for p in d.rglob('*') if p.is_file())
    return total


class FakeClient:
    def __init__(self, pages=1, fail=False):self.n=pages;self.fail=fail
    async def pages(self, pid):return [f'https://i.pximg.net/{i}.jpg' for i in range(self.n)]
    async def download(self,url,path,max_bytes):
        path.write_bytes(b'original')
        if self.fail:raise PixivError('download failed')
        return 8


class DeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_multipage_merged_forward(self):
        with tempfile.TemporaryDirectory() as d:
            store=Store(d);calls=[]
            async def send(text,paths,**meta):
                calls.append((text,len(paths),meta))
                self.assertTrue(all(p.read_bytes()==b'original' for p in paths))
            result=await send_work(FakeClient(3),store,{'id':'1','pageCount':3,'userName':'author'},send,
                                   dict(settings.DEFAULTS,interval_seconds=0))
            self.assertEqual(result,3);self.assertEqual(len(calls),1);self.assertIn('PID：1',calls[0][0]);self.assertIn('author',calls[0][0])
            self.assertEqual(work_bytes(store),0)
            self.assertEqual(calls[0][2], {'forward':True,'start_page':1,'total_pages':3})

    async def test_large_work_keeps_all_pages(self):
        with tempfile.TemporaryDirectory() as d:
            calls=[];store=Store(d)
            async def send(text,paths,**meta):calls.append(len(paths))
            await send_work(FakeClient(105),store,{'id':'1','pageCount':105},send,dict(settings.DEFAULTS,interval_seconds=0))
            self.assertEqual(calls,[99,6]);self.assertEqual(work_bytes(store),0)

    async def test_single_page_stays_normal(self):
        with tempfile.TemporaryDirectory() as d:
            store=Store(d); calls=[]
            async def send(text,paths,**meta):calls.append((len(paths),meta))
            await send_work(FakeClient(),store,{'id':'1'},send,dict(settings.DEFAULTS,interval_seconds=0))
            self.assertEqual(calls,[(1,{})]);self.assertEqual(work_bytes(store),0)

    async def test_sixteen_pages_one_forward(self):
        with tempfile.TemporaryDirectory() as d:
            store=Store(d); calls=[]
            async def send(text,paths,**meta):calls.append((len(paths),meta))
            await send_work(FakeClient(16),store,{'id':'1','pageCount':16},send,dict(settings.DEFAULTS,interval_seconds=0))
            self.assertEqual(len(calls),1);self.assertEqual(calls[0][0],16)
            self.assertTrue(calls[0][1]['forward']);self.assertEqual(work_bytes(store),0)

    async def test_uncertain_no_replay_cleanup(self):
        with tempfile.TemporaryDirectory() as d:
            calls=[];store=Store(d)
            async def send(text,paths,**meta):calls.append(1);raise TimeoutError()
            with self.assertRaises(SendUncertain):
                await send_work(FakeClient(),store,{'id':'1'},send,dict(settings.DEFAULTS,interval_seconds=0))
            self.assertEqual(calls,[1])
            # 超时不等于没发出：QQ 可能仍在上传，工作副本按设计保留到过期清扫，且绝不重发
            self.assertGreater(work_bytes(store),0)
            store.cleanup(age_seconds=0)
            self.assertEqual(work_bytes(store),0)

    async def test_download_error_cleanup(self):
        with tempfile.TemporaryDirectory() as d:
            store=Store(d)
            async def send(text,paths,**meta):self.fail('must not send')
            with self.assertRaises(PixivError):
                await send_work(FakeClient(fail=True),store,{'id':'1'},send,settings.DEFAULTS)
            self.assertEqual(store.usage(),0)

    async def test_revocation(self):
        with tempfile.TemporaryDirectory() as d:
            store=Store(d)
            async def send(text,paths,**meta):self.fail('must not send')
            with self.assertRaises(PixivError):
                await send_work(FakeClient(),store,{'id':'1'},send,settings.DEFAULTS,allowed=lambda:False)
            self.assertEqual(store.usage(),0)


class ProviderTests(unittest.IsolatedAsyncioTestCase):
    def test_image_host(self):
        self.assertTrue(check_image_url('https://i.pximg.net/a.png'))
        for url in ['http://i.pximg.net/a','https://evilpximg.net/a','http://127.0.0.1/a']:
            with self.assertRaises(PixivError):check_image_url(url)

    async def test_wrong_board_date(self):
        client=Client()
        async def fake(*args):return {'date':'20260919','mode':'daily','contents':[]}
        client.json=fake
        try:
            with self.assertRaises(PixivError):await client.ranking(board_for(date(2026,9,21)))
        finally:await client.close()

    async def test_missing_ranks_not_substituted(self):
        client=Client()
        async def fake(*args):return {'date':'20260920','mode':'daily','contents':[{'rank':1,'illust_id':1}]}
        client.json=fake
        try:
            with self.assertRaises(PixivError):await client.ranking(board_for(date(2026,9,21)))
        finally:await client.close()

    async def test_ranking_404_means_unpublished(self):
        client=Client()
        async def fake(*args):raise PixivError('Pixiv HTTP 404')
        client.json=fake
        try:
            with self.assertRaisesRegex(PixivError,'榜单尚未公布'):await client.ranking(board_for(date(2026,9,21)))
        finally:await client.close()

    async def test_ranking_tolerates_deleted_rank(self):
        client=Client()
        async def fake(path,params):
            page=params.get('p',1)
            ranks=list(range((page-1)*50+1,page*50+1))
            if page==3:ranks.remove(140)
            return {'date':params['date'],'mode':'daily','contents':[{'rank':r,'illust_id':r} for r in ranks]}
        client.json=fake
        try:
            rows=await client.ranking(board_for(date(2026,9,21)))
            got=[r['rank'] for r in rows]
            self.assertEqual(len(rows),149)
            self.assertEqual(got,sorted(got))
            self.assertNotIn(140,got)
        finally:await client.close()

    async def test_board_cache_accepts_149(self):
        with tempfile.TemporaryDirectory() as d:
            store=Store(Path(d)/'store');calls=[]
            class FakeBoard:mode='daily';date=date(2026,9,20);key='daily-20260920';label='test'
            class CachedClient:
                async def ranking(self,board):
                    calls.append(1)
                    return [{'rank':r,'id':str(r)} for r in range(1,151) if r!=140]
            first=await store.ranking(CachedClient(),FakeBoard())
            second=await store.ranking(CachedClient(),FakeBoard())
            self.assertEqual((len(first),len(second),len(calls)),(149,149,1))


class SearchTests(unittest.IsolatedAsyncioTestCase):
    async def test_exact_translation_only(self):
        c=Client();queries=[]
        async def fake(path,params):
            queries.append(params['word'])
            return {'body':{'illustManga':{'data':[{'id':'1','illustType':0,'xRestrict':0}]},
                            'tagTranslation':{'公式タグ':{'zh':'测试角色'},'其他角色':{'zh':'无关角色'}}}}
        async def detail(pid):return {'id':pid,'bookmarkCount':10,'viewCount':100}
        c.json=fake;c.detail=detail
        try:
            works=await c.search(['测试角色'],1)
            self.assertEqual(queries,['测试角色','公式タグ'])
            self.assertEqual(len(works),1)
        finally:await c.close()

    async def test_cancel_cleanup(self):
        with tempfile.TemporaryDirectory() as d:
            store=Store(d)
            async def send(text,paths,**meta):raise asyncio.CancelledError()
            with self.assertRaises(asyncio.CancelledError):
                await send_work(FakeClient(),store,{'id':'1'},send,settings.DEFAULTS)
            self.assertEqual(work_bytes(store),0)


if __name__=='__main__':unittest.main()

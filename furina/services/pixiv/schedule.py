"""Explicit board dates; all wall-clock scheduling uses UTC+8."""
from dataclasses import dataclass
from datetime import datetime, date, timedelta, timezone

CST = timezone(timedelta(hours=8))
NAMES = {'daily': '日榜', 'weekly': '周榜', 'monthly': '月榜'}


@dataclass(frozen=True)
class Board:
    mode: str
    date: date

    @property
    def key(self):
        return f'{self.mode}-{self.date:%Y%m%d}'

    @property
    def label(self):
        return f'{self.date:%Y-%m-%d} 插画{NAMES[self.mode]}'


def board_for(day):
    fifteenth = day.replace(day=15)
    monthly_day = 16 if fifteenth.weekday() == 6 else 15
    if day.day == monthly_day:
        return Board('monthly', day.replace(day=1) - timedelta(days=1))
    if day.weekday() == 6:
        # 周榜每周一中午 JST 才公布上周榜：周日要“昨天所在周”必 404，固定查上周日（上周榜）。
        return Board('weekly', day - timedelta(days=7))
    # Pixiv 每天约 12:00 JST（北京时间 11 点）才公布前一天的日榜：
    # 要“昨天榜”则 9 点批次必 404 空等，所以每天固定发“前天榜”，9 点准时有货。
    return Board('daily', day - timedelta(days=2))


def slots_for(day, times):
    return [datetime.combine(day, datetime.strptime(t, '%H:%M').time(), CST) for t in times]


def rank_bounds(batch):
    if not 0 <= batch < 5:
        raise ValueError('batch must be 0..4')
    return batch * 30 + 1, (batch + 1) * 30


def due_batches(now, started, times, grace=60):
    now = now.astimezone(CST)
    return [i for i, at in enumerate(slots_for(now.date(), times))
            if started <= at <= now and (now - at).total_seconds() <= grace]

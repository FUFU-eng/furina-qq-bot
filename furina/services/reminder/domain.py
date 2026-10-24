"""Validated schedules. Times are always UTC+8 and ownership is never model-supplied."""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import calendar
import re

CST = timezone(timedelta(hours=8))
WEEK_NAMES = '一二三四五六日'


class Clarify(ValueError):
    pass


@dataclass(frozen=True)
class Proposal:
    text: str
    kind: str
    next_at: float
    clock: str = ''
    weekdays: tuple = ()  # 0 = Monday
    monthday: int = 0  # 1..31; nonexistent days skipped, 0 means not monthly

    def schedule(self):
        return {'kind':self.kind,'clock':self.clock,'weekdays':list(self.weekdays),'monthday':self.monthday}

    def describe(self):
        at=datetime.fromtimestamp(self.next_at,CST).strftime('%Y-%m-%d %H:%M:%S')
        cadence = '一次性'
        if self.kind=='daily':cadence=f'每天 {self.clock}'
        if self.kind=='weekly':cadence='每周'+ '、'.join(WEEK_NAMES[d] for d in self.weekdays)+f' {self.clock}'
        if self.kind=='monthly':cadence=f'每月{self.monthday}日 {self.clock}（该月无此日期则跳过）'
        return f'{cadence}\n下次：{at}（北京时间）\n内容：{self.text}'


def next_occurrence(schedule, after):
    if schedule['kind']=='once':return None
    if schedule['kind'] not in ('daily','weekly','monthly'):raise Clarify('未知的重复提醒类型')
    hh,mm=map(int,schedule['clock'].split(':'))
    if schedule['kind']=='monthly':
        day=schedule.get('monthday')
        if type(day) is not int or not 1<=day<=31:raise Clarify('请明确每月几号（1–31）')
        local=after.astimezone(CST)
        for offset in range(25):
            index=local.year*12+local.month-1+offset;year,month0=divmod(index,12)
            if day>calendar.monthrange(year,month0+1)[1]:continue
            candidate=datetime(year,month0+1,day,hh,mm,tzinfo=CST)
            if candidate>after:return candidate.timestamp()
        raise Clarify('无法计算下次每月提醒')
    at=after.astimezone(CST).replace(hour=hh,minute=mm,second=0,microsecond=0)
    for offset in range(8):
        candidate=at+timedelta(days=offset)
        if candidate>after and (schedule['kind']=='daily' or candidate.weekday() in schedule['weekdays']):
            return candidate.timestamp()
    raise Clarify('无法计算下一次提醒时间')


def make_proposal(text, kind, now, at=None, clock='', weekdays=(), monthday=0):
    if not isinstance(text,str) or not text.strip() or len(text.strip())>200:
        raise Clarify('请提供 1–200 字的提醒内容。')
    if any(ord(c)<32 and c not in '\n\t' for c in text):raise Clarify('提醒内容包含无效字符。')
    if kind not in ('once','daily','weekly','monthly'):raise Clarify('目前支持一次性、每天、每周和每月提醒。')
    if kind=='once':
        if not isinstance(at,datetime) or at.tzinfo is None:raise Clarify('请给出明确的提醒时间。')
        seconds=(at-now).total_seconds()
        if seconds<30:raise Clarify('时间已经过去或太近，请设置至少 30 秒之后的时间。')
        if seconds>366*86400:raise Clarify('一次性提醒最多提前一年，请换一个更近的日期。')
        return Proposal(text.strip(),kind,at.timestamp())
    if not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d',clock):raise Clarify('请明确几点几分。')
    if kind=='weekly':
        if not isinstance(weekdays,(list,tuple)) or not weekdays or any(type(d) is not int or d not in range(7) for d in weekdays):
            raise Clarify('请明确星期几。')
        weekdays=tuple(sorted(set(weekdays)))
    else:weekdays=()
    if kind=='monthly':
        if type(monthday) is not int or not 1<=monthday<=31:raise Clarify('请明确每月几号（1–31）。')
    else:monthday=0
    schedule={'kind':kind,'clock':clock,'weekdays':weekdays,'monthday':monthday}
    return Proposal(text.strip(),kind,next_occurrence(schedule,now),clock,weekdays,monthday)


def from_model(data, now):
    if not isinstance(data,dict):raise Clarify('没能确定提醒时间，请换一种说法。')
    if data.get('action')!='create':
        raise Clarify(str(data.get('question') or '请告诉我具体何时、提醒你做什么。')[:180])
    if data.get('target')!='self':raise Clarify('我只能提醒发起请求的你，不能替你提醒其他群友。')
    kind=data.get('kind')
    at=None
    if kind=='once':
        try:
            at=datetime.fromisoformat(data.get('at',''))
        except (ValueError,TypeError):raise Clarify('没有解析出有效日期，请明确日期和时间。') from None
        if at.tzinfo is None or at.utcoffset()!=timedelta(hours=8):
            raise Clarify('请使用北京时间（UTC+8）。')
    return make_proposal(data.get('text'),kind,now,at,data.get('clock',''),data.get('weekdays',()),data.get('monthday',0))

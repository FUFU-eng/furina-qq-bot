"""Transport-independent due delivery. One attempt per occurrence, never blind replay."""
import asyncio
import random
import re
from datetime import datetime
from .domain import CST


def say(what,late=0,planned=0,rng=random):
    """到点时她说的话：像朋友叫你，不是系统通知。"""
    what=re.sub(r'^该|了$','',(what or '').strip()) or (what or '').strip()
    if late>=60:
        at=datetime.fromtimestamp(planned,CST).strftime('%H:%M') if planned else ''
        return rng.choice([f'啊我迟到了{max(1,int(late//60))}分钟才叫你，{at}该{what}了',
                           f'晚了点，我迟到了{max(1,int(late//60))}分钟，该{what}了'])
    return rng.choice([f'到点啦，该{what}了',f'{what}的时间到了',f'喂，该{what}了',f'时间到，{what}'])


async def tick(store,now,senders,allowed,grace_minutes=30,clock=None):
    clock=clock or (lambda: now)
    store.recover(now)
    for row in store.due(now):
        current=clock()
        late=current.timestamp()-row['next_at']
        if late>grace_minutes*60:
            store.advance(row,current,'skipped_late',expected='active')
            continue
        send=senders.get(row['gid'])
        if send is None or not allowed(row['gid']):
            continue  # Remains pending until online/authorized, within the grace window.
        if not store.claim(row,current):continue
        if not store.claimed(row):continue  # User cancelled after claim.
        if not allowed(row['gid']):
            store.advance(row,current,'skipped_disabled')
            continue
        text=say(row['text'],late,row['next_at'])
        try:
            await asyncio.wait_for(send(row['uid'],text),timeout=25)
        except asyncio.CancelledError:
            store.advance(row,clock(),'unknown_interrupted')
            raise
        except Exception:
            # An API exception doesn't prove a message wasn't delivered.
            store.advance(row,clock(),'unknown_delivery')
        else:
            store.advance(row,clock(),'sent_late' if late>=60 else 'sent')

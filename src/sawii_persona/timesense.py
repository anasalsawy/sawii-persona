"""The persona's sense of time.

Two clocks, both measured, never invented:
  * gap   - time passed since the persona's last reply (and the user's last message)
  * spent - time spent inside the current request, live to the second

A sees both at the start of every turn and can look at the running clock at
any moment through the ``check_clock`` tool.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timezone

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None  # type: ignore


def fmt_duration(seconds: float) -> str:
    seconds = max(0, int(round(seconds)))
    d, rem = divmod(seconds, 86400)
    h, rem = divmod(rem, 3600)
    m, s = divmod(rem, 60)
    if d:
        return f"{d}d {h}h {m:02d}m {s:02d}s"
    if h:
        return f"{h}h {m:02d}m {s:02d}s"
    if m:
        return f"{m}m {s:02d}s"
    return f"{s}s"


def local_dt(ts: float, tz: str) -> datetime:
    zone = None
    if ZoneInfo is not None:
        try:
            zone = ZoneInfo(tz or "UTC")
        except Exception:
            zone = None
    return datetime.fromtimestamp(ts, tz=zone or timezone.utc)


def part_of_day(hour: int) -> str:
    if 5 <= hour < 12:
        return "morning"
    if 12 <= hour < 17:
        return "afternoon"
    if 17 <= hour < 21:
        return "evening"
    return "night"


@dataclass
class Clock:
    started_at: float
    last_reply_at: float
    last_user_at: float
    tz: str = "UTC"
    last_exchange_seconds: float | None = None

    @property
    def spent(self) -> float:
        return time.time() - self.started_at

    @property
    def gap_reply(self) -> float | None:
        return (self.started_at - self.last_reply_at) if self.last_reply_at else None

    @property
    def gap_user(self) -> float | None:
        return (self.started_at - self.last_user_at) if self.last_user_at else None

    def snapshot(self) -> dict:
        now = time.time()
        dt = local_dt(now, self.tz)
        return {
            "now_iso": dt.isoformat(timespec="seconds"),
            "local_time": dt.strftime("%A %d %B %Y, %H:%M:%S"),
            "part_of_day": part_of_day(dt.hour),
            "since_your_last_reply_seconds": round(self.gap_reply, 1) if self.gap_reply is not None else None,
            "since_their_last_message_seconds": round(self.gap_user, 1) if self.gap_user is not None else None,
            "this_request_running_seconds": round(now - self.started_at, 2),
        }

    def block(self) -> str:
        snap = self.snapshot()
        lines = [f"[time] It is {snap['local_time']} ({snap['part_of_day']}, {self.tz})."]
        if self.gap_reply is None:
            lines.append("[time] This is the first time you two have talked.")
        else:
            lines.append(f"[time] Since your last reply: {fmt_duration(self.gap_reply)}.")
            if self.gap_user is not None and abs(self.gap_user - self.gap_reply) > 5:
                lines.append(f"[time] Since their previous message: {fmt_duration(self.gap_user)}.")
            if self.last_exchange_seconds:
                lines.append(f"[time] Your last answer took {fmt_duration(self.last_exchange_seconds)} to give.")
        lines.append(f"[time] This request has been open {fmt_duration(self.spent)} so far "
                     "(the clock keeps running; call check_clock to look again).")
        return "\n".join(lines)


def describe_gap(seconds: float | None) -> str:
    if seconds is None:
        return "first meeting"
    if seconds < 120:
        return "mid-conversation"
    if seconds < 3600:
        return "a short pause"
    if seconds < 6 * 3600:
        return "a few hours apart"
    if seconds < 36 * 3600:
        return "about a day apart"
    if seconds < 8 * 86400:
        return "several days apart"
    return "a long time apart"


def in_quiet_hours(ts: float, tz: str, spec: str) -> bool:
    """spec 'HH:MM-HH:MM' in the persona's local time; may wrap midnight."""
    if not spec or "-" not in spec:
        return False
    try:
        a, b = spec.split("-", 1)
        ah, am = (int(x) for x in a.split(":"))
        bh, bm = (int(x) for x in b.split(":"))
    except ValueError:
        return False
    dt = local_dt(ts, tz)
    cur = dt.hour * 60 + dt.minute
    start, end = ah * 60 + am, bh * 60 + bm
    if start == end:
        return False
    return start <= cur < end if start < end else (cur >= start or cur < end)

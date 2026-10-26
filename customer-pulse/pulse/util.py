"""Small stdlib helpers: atomic JSON writes, a run lock, date maths and Kusto value decoding."""
import base64
import binascii
import datetime as dt
import json
import os
import tempfile
import time


def atomic_write_text(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", dir=os.path.dirname(path))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def atomic_write_json(path, payload, indent=None):
    atomic_write_text(path, json.dumps(payload, indent=indent, sort_keys=indent is not None, default=str))


def read_json(path, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default


class LockBusy(RuntimeError):
    pass


class RunLock:
    """Exclusive lock file. A lock older than `stale_seconds` is assumed abandoned and replaced."""

    def __init__(self, path, stale_seconds=2 * 3600, now=time.time):
        self.path = path
        self.stale_seconds = stale_seconds
        self.now = now
        self.held = False

    def acquire(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        for _ in range(2):
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            except FileExistsError:
                try:
                    age = self.now() - os.path.getmtime(self.path)
                except FileNotFoundError:
                    continue
                if age > self.stale_seconds:
                    try:
                        os.unlink(self.path)
                    except FileNotFoundError:
                        pass
                    continue
                holder = read_json(self.path, {}) or {}
                raise LockBusy(f"Another Customer Pulse step is running (pid {holder.get('pid')}, "
                               f"started {holder.get('started')}). Lock: {self.path}")
            with os.fdopen(fd, "w") as f:
                json.dump({"pid": os.getpid(), "started": iso_now()}, f)
            self.held = True
            return self
        raise LockBusy(f"Could not take the lock {self.path}")

    def release(self):
        if self.held:
            try:
                os.unlink(self.path)
            except FileNotFoundError:
                pass
            self.held = False

    def __enter__(self):
        return self.acquire()

    def __exit__(self, *exc):
        self.release()


def iso_now():
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def parse_ymd(s):
    """'20261025' or '2026-10-25' (optionally with a time part) -> date."""
    if s is None:
        return None
    if isinstance(s, dt.date) and not isinstance(s, dt.datetime):
        return s
    s = str(s)
    if len(s) == 8 and s.isdigit():
        return dt.date(int(s[:4]), int(s[4:6]), int(s[6:8]))
    return dt.date.fromisoformat(s[:10])


def ymd(d):
    return d.isoformat() if d else None


_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def human_day(s, year=False):
    """'2026-10-25' -> 'Oct 25' (or 'Oct 25, 2026'), matching the canvas. Unparseable values are returned unchanged."""
    try:
        d = parse_ymd(s)
    except (TypeError, ValueError):
        return s
    if d is None:
        return s
    return f"{_MONTHS[d.month - 1]} {d.day}, {d.year}" if year else f"{_MONTHS[d.month - 1]} {d.day}"


def days_between(a, b):
    return (parse_ymd(b) - parse_ymd(a)).days


def date_range(start, end):
    d = parse_ymd(start)
    end = parse_ymd(end)
    while d <= end:
        yield d
        d += dt.timedelta(days=1)


def decode_dynamic(v):
    """The Revenue MCP returns Kusto dynamic columns base64-encoded; decode them when they are."""
    if isinstance(v, str) and v and v[0] not in "[{" and len(v) % 4 == 0:
        try:
            return json.loads(base64.b64decode(v, validate=True))
        except (binascii.Error, ValueError, UnicodeDecodeError):
            return v
    if isinstance(v, str) and v[:1] in "[{":
        try:
            return json.loads(v)
        except ValueError:
            return v
    return v


def num(v):
    """Coerce a Kusto scalar to float, keeping None for missing values."""
    if v is None or v == "":
        return None
    if isinstance(v, bool):
        return float(v)
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def truthy(v):
    if isinstance(v, str):
        return v.strip().lower() in ("true", "1", "yes", "y")
    return bool(v)

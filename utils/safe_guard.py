# coding=utf-8
"""
防风控护栏：限速 + 风控退避 + 每日上限。宁可慢，也要稳。

1. 全局限速（throttle）
   所有发往抖音数据接口（*.douyin.com）的请求都要先过这道闸：
   - 每两次请求之间随机间隔（默认 3~6 秒，不规律，更像真人）
   - 每分钟 / 每小时请求数上限
   - 每请求几十次，随机"休息"一两分钟
   - 每日请求总量上限（跨多次运行累计，次日自动清零）
   登录（passport/sso）和视频图片 CDN 下载不受限。

2. 风控退避（guarded）
   只读接口遇到风控（人机验证、空响应、挑战页）或网络错误时：
   暂停 → 重试，等待时间逐次翻倍（默认 5 → 10 → 20 分钟），
   并在之后一段时间自动放慢节奏；仍失败就抛出 RiskStop，
   由上层保存进度后安全停止。
   发私信、评论、点赞等"写操作"绝不自动重试，避免重复发送。

所有参数都可以在 .env 里改，见 .env.example 的「防风控」部分。
"""
import json
import os
import random
import threading
import time
from collections import deque
from datetime import date
from functools import wraps
from urllib.parse import urlparse

from loguru import logger

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COUNTER_FILE = os.path.join(ROOT_DIR, "datas", ".request_counter.json")


class RiskStop(BaseException):
    """风控或额度导致必须停止。

    刻意继承 BaseException 而不是 Exception：项目里不少地方用
    ``except Exception`` 跳过单条失败，这个信号必须能穿透它们，
    一路传到最外层，由爬虫保存进度后停下。
    """


class DailyLimitReached(RiskStop):
    pass


# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------
def _range(name, default):
    raw = (os.getenv(name) or default).strip()
    try:
        if "-" in raw:
            lo, hi = raw.split("-", 1)
            lo, hi = float(lo), float(hi)
            return (min(lo, hi), max(lo, hi))
        v = float(raw)
        return (v, v)
    except ValueError:
        logger.warning(f"{name}={raw!r} 格式不对，使用默认值 {default}")
        return _range("__unset__", default)


def _int(name, default):
    try:
        return int(float(os.getenv(name) or default))
    except ValueError:
        logger.warning(f"{name} 格式不对，使用默认值 {default}")
        return int(default)


class _Config:
    def __init__(self):
        self.enabled = (os.getenv("DY_SAFE_MODE") or "1").strip().lower() not in ("0", "false", "no", "off")
        self.interval = _range("DY_REQ_INTERVAL", "3-6")
        self.per_min = _int("DY_REQ_PER_MIN", "10")
        self.per_hour = _int("DY_REQ_PER_HOUR", "300")
        self.per_day = _int("DY_REQ_PER_DAY", "2000")
        self.rest_every = _range("DY_REST_EVERY", "40-60")
        self.rest_seconds = _range("DY_REST_SECONDS", "90-240")
        self.risk_backoff = _int("DY_RISK_BACKOFF", "300")
        self.risk_max_retry = _int("DY_RISK_MAX_RETRY", "3")


_cfg = None


def config():
    global _cfg
    if _cfg is None:
        _cfg = _Config()
        if _cfg.enabled:
            logger.info(
                f"防风控已开启：间隔 {_cfg.interval[0]:g}~{_cfg.interval[1]:g}s，"
                f"≤{_cfg.per_min}次/分，≤{_cfg.per_hour}次/时，≤{_cfg.per_day}次/天"
            )
        else:
            logger.warning("防风控已关闭（DY_SAFE_MODE=0），请求不限速，账号风险自负")
    return _cfg


def reset_config():
    """测试或修改环境变量后重新读取配置。"""
    global _cfg
    _cfg = None


# ---------------------------------------------------------------------------
# 限速
# ---------------------------------------------------------------------------
class _Throttle:
    def __init__(self):
        self.lock = threading.Lock()
        self.last = 0.0
        self.minute = deque()
        self.hour = deque()
        self.since_rest = 0
        self.next_rest = None
        self.slow = 1.0          # 风控后的放慢倍数，1 = 正常
        self.ok_streak = 0
        self.day_date = None
        self.day_count = 0

    # ---- 每日计数（落盘，跨运行累计） ----
    def _load_day(self):
        today = date.today().isoformat()
        if self.day_date == today:
            return
        self.day_date, self.day_count = today, 0
        try:
            with open(COUNTER_FILE, encoding="utf-8") as f:
                data = json.load(f)
            if data.get("date") == today:
                self.day_count = int(data.get("count", 0))
        except (OSError, ValueError):
            pass

    def _save_day(self):
        try:
            os.makedirs(os.path.dirname(COUNTER_FILE), exist_ok=True)
            with open(COUNTER_FILE, "w", encoding="utf-8") as f:
                json.dump({"date": self.day_date, "count": self.day_count}, f)
        except OSError:
            pass

    @staticmethod
    def _sleep(seconds, reason):
        if seconds <= 0:
            return
        if seconds >= 10:
            logger.info(f"⏳ {reason}，等待 {seconds:.0f} 秒…")
        time.sleep(seconds)

    def wait(self):
        cfg = config()
        with self.lock:
            self._load_day()
            if cfg.per_day and self.day_count >= cfg.per_day:
                raise DailyLimitReached(
                    f"今日请求已达上限 {cfg.per_day} 次（DY_REQ_PER_DAY）。"
                    f"进度已保存，明天再运行会从断点继续。"
                )

            if self.next_rest is None:
                self.next_rest = random.randint(*map(int, cfg.rest_every))
            if self.since_rest >= self.next_rest:
                self._sleep(random.uniform(*cfg.rest_seconds), f"已连续请求 {self.since_rest} 次，休息一下")
                self.since_rest = 0
                self.next_rest = random.randint(*map(int, cfg.rest_every))

            now = time.time()
            gap = random.uniform(*cfg.interval) * self.slow
            self._sleep(self.last + gap - now, "请求间隔")

            for q, window, cap, name in ((self.minute, 60, cfg.per_min, "每分钟"),
                                         (self.hour, 3600, cfg.per_hour, "每小时")):
                now = time.time()
                while q and now - q[0] >= window:
                    q.popleft()
                if cap and len(q) >= cap:
                    self._sleep(q[0] + window - now + random.uniform(1, 5), f"达到{name}上限 {cap} 次")
                    now = time.time()
                    while q and now - q[0] >= window:
                        q.popleft()

            now = time.time()
            self.last = now
            self.minute.append(now)
            self.hour.append(now)
            self.since_rest += 1
            self.day_count += 1
            self._save_day()

    def on_success(self):
        with self.lock:
            self.ok_streak += 1
            if self.slow > 1 and self.ok_streak >= 30:
                self.slow = max(1.0, self.slow / 2)
                self.ok_streak = 0
                logger.info(f"运行平稳，节奏恢复为 ×{self.slow:g}")

    def on_risk(self):
        with self.lock:
            self.ok_streak = 0
            self.slow = min(self.slow * 2, 4.0)
            logger.warning(f"已触发风控，之后请求放慢为 ×{self.slow:g}")


_throttle = _Throttle()


def _is_guarded_host(url):
    host = (urlparse(str(url)).hostname or "").lower()
    if not (host == "douyin.com" or host.endswith(".douyin.com")):
        return False  # CDN（douyinvod / douyinpic）等不限
    if "passport" in host or host.startswith("sso."):
        return False  # 登录流程有自己的节奏，不打断
    return True


def throttle(url):
    """http_client 每次发请求前调用。"""
    if not config().enabled or not _is_guarded_host(url):
        return
    _throttle.wait()


def today_count():
    with _throttle.lock:
        _throttle._load_day()
        return _throttle.day_count


# ---------------------------------------------------------------------------
# 风控退避
# ---------------------------------------------------------------------------
_RISK_WORDS = ("人机验证", "二次身份验证", "空响应", "acrawler", "非 JSON", "风控")


def classify(err):
    """返回 'risk' / 'network' / None（不处理，原样抛出）。"""
    msg = str(err)
    if isinstance(err, RuntimeError) and any(w in msg for w in _RISK_WORDS):
        return "risk"
    try:
        from curl_cffi.requests import exceptions as cffi_exc
        if isinstance(err, cffi_exc.RequestException):
            return "network"
    except Exception:
        pass
    if isinstance(err, (ConnectionError, TimeoutError)):
        return "network"
    return None


GUIDE = (
    "建议：1) 先停一段时间（几小时）再跑；"
    "2) 在 Safari 打开 www.douyin.com 刷一下，如果出现滑块/验证码就手动完成；"
    "3) 把 .env 里的 DY_REQ_INTERVAL 调大、DY_REQ_PER_MIN 调小。"
    "再次运行会从断点继续，不会从头开始。"
)


def guarded(fn):
    """只读接口的保护：风控/网络错误时退避重试，仍失败抛 RiskStop。"""
    if getattr(fn, "_safe_guarded", False):
        return fn

    @wraps(fn)
    def wrapper(*args, **kwargs):
        cfg = config()
        if not cfg.enabled:
            return fn(*args, **kwargs)
        for attempt in range(cfg.risk_max_retry + 1):
            try:
                result = fn(*args, **kwargs)
                _throttle.on_success()
                return result
            except RiskStop:
                raise
            except Exception as err:
                kind = classify(err)
                if kind is None:
                    raise
                if kind == "risk":
                    _throttle.on_risk()
                if attempt >= cfg.risk_max_retry:
                    raise RiskStop(f"{fn.__name__} 连续 {attempt + 1} 次失败，已安全停止：{err}\n{GUIDE}") from err
                if kind == "risk":
                    wait = cfg.risk_backoff * (2 ** attempt)
                else:
                    wait = 30 * (attempt + 1)
                wait *= random.uniform(0.9, 1.2)
                label = "触发风控" if kind == "risk" else "网络错误"
                logger.warning(
                    f"{fn.__name__} {label}（第 {attempt + 1}/{cfg.risk_max_retry} 次重试）：{err}\n"
                    f"   暂停 {wait / 60:.1f} 分钟后重试，可随时按 Ctrl+C 中断，下次会从断点继续"
                )
                time.sleep(wait)

    wrapper._safe_guarded = True
    return wrapper


# 只读、按"一页"请求的底层接口。自动翻页的 get_xxx_all / search_some_xxx
# 内部会调用它们，所以不用重复包。写操作（私信、评论、点赞、收藏、发弹幕）
# 刻意不在名单里：它们失败后不能自动重发。
READ_METHODS = (
    "get_user_work_info", "get_work_info", "get_user_info",
    "get_work_out_comment", "get_work_inner_comment",
    "search_general_work", "search_video_work", "search_user", "search_live",
    "get_user_favorite", "get_collect_list",
    "get_user_follower_list", "get_user_following_list",
    "get_notice_list", "get_feed",
    "get_live_info", "get_webcast_detail", "get_live_room_enter",
    "get_live_production", "get_live_production_detail",
    "get_product_comments", "get_product_comment_counter",
    "get_live_contribution_rank", "get_live_thousand_ticket_rank",
    "get_live_linkmic_list", "get_live_pk_contribution_rank",
)


def install(api_cls):
    for name in READ_METHODS:
        raw = api_cls.__dict__.get(name)
        if raw is None:
            continue
        func = raw.__func__ if isinstance(raw, staticmethod) else raw
        setattr(api_cls, name, staticmethod(guarded(func)))
    return api_cls

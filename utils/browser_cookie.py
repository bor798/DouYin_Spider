# coding=utf-8
"""
自动获取抖音登录 Cookie，免去每次 F12 手动复制。

两种来源（.env 里用 DY_AUTO_COOKIE 选择）：

1. profile（推荐，默认）
   用 Playwright 启动一个"专用浏览器"，登录状态保存在项目下的
   .dy_browser_profile/ 目录里。
   - 第一次：弹出浏览器窗口，你扫码/手机号登录一次即可；
   - 之后：后台无界面打开抖音，自动读取最新 Cookie（含 UIFID）写回 .env。
   只要这个专用浏览器的登录没过期，就再也不用手动操作。

2. safari / chrome / edge / firefox / brave / chromium
   直接读取你日常浏览器里已登录的抖音 Cookie（需要 pip install browser-cookie3）。
   Safari（macOS）不加密，但需要给运行脚本的终端开"完全磁盘访问权限"：
   系统设置 → 隐私与安全性 → 完全磁盘访问权限 → 打开"终端"（或 iTerm / PyCharm / VS Code）。
   注意：Windows 上新版 Chrome/Edge 启用了 App-Bound 加密，第三方程序经常读不出来，
   这种情况请改用 profile 模式或 firefox。

命令行用法：
    python get_cookie.py              # 默认 profile 模式
    python get_cookie.py --login      # 强制弹出窗口重新登录（换号时用）
    python get_cookie.py --source chrome
"""
import os
import time

from loguru import logger

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROFILE_DIR = os.path.join(ROOT_DIR, ".dy_browser_profile")
HOME_URL = "https://www.douyin.com/"
LOGIN_KEYS = ("sessionid", "sessionid_ss")
# 换了账号后这些凭证与旧会话绑定，继续用会让创作者/私信接口失败
SESSION_BOUND_KEYS = ("DY_TICKET", "DY_TS_SIGN", "DY_CLIENT_CERT",
                      "DY_PRIVATE_KEY", "DY_DTRAIT_BLOB", "DY_SESSION_DTRAIT")
BROWSER_SOURCES = ("safari", "chrome", "edge", "firefox", "brave", "chromium")


class CookieFetchError(RuntimeError):
    pass


def _is_logged_in(cookies: dict) -> bool:
    return any(cookies.get(k) for k in LOGIN_KEYS)


def _to_cookie_str(cookies: dict) -> str:
    return "; ".join(f"{k}={v}" for k, v in cookies.items())


def _parse_cookie_str(cookie_str: str) -> dict:
    out = {}
    for token in (cookie_str or "").split(";"):
        name, sep, value = token.strip().partition("=")
        if name and sep:
            out[name] = value
    return out


# --------------------------------------------------------------------------
# 来源 1：Playwright 专用浏览器（持久化登录）
# --------------------------------------------------------------------------
def _launch(p, headless: bool):
    kwargs = dict(
        user_data_dir=PROFILE_DIR,
        headless=headless,
        viewport={"width": 1280, "height": 800},
        locale="zh-CN",
        args=["--disable-blink-features=AutomationControlled"],
    )
    # 优先用本机安装的 Chrome，更不容易被风控；没有就退回 Playwright 自带 Chromium
    for channel in ("chrome", "msedge", None):
        try:
            if channel:
                return p.chromium.launch_persistent_context(channel=channel, **kwargs)
            return p.chromium.launch_persistent_context(**kwargs)
        except Exception as err:
            last = err
    raise CookieFetchError(
        f"无法启动浏览器：{last}\n请先执行：pip install playwright && playwright install chromium"
    )


def _douyin_cookies(ctx) -> dict:
    cookies = {}
    for c in ctx.cookies(HOME_URL):
        cookies[c["name"]] = c["value"]
    return cookies


def _wait_uifid(ctx, timeout=15):
    """UIFID 由页面 JS 写入，等它出现，否则作品/搜索接口会报 Uifid Not Found。"""
    end = time.time() + timeout
    while time.time() < end:
        cookies = _douyin_cookies(ctx)
        if cookies.get("UIFID"):
            return cookies
        time.sleep(1)
    logger.warning("未等到 UIFID cookie，部分接口可能被风控；可稍后再运行一次 get_cookie.py")
    return _douyin_cookies(ctx)


def fetch_from_profile(force_login=False, login_timeout=300, allow_interactive=True) -> str:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise CookieFetchError(
            "缺少 playwright，请执行：pip install playwright && playwright install chromium"
        )

    with sync_playwright() as p:
        # 先无界面试一次：已登录就直接拿
        if not force_login and os.path.isdir(PROFILE_DIR):
            ctx = _launch(p, headless=True)
            try:
                page = ctx.pages[0] if ctx.pages else ctx.new_page()
                page.goto(HOME_URL, wait_until="domcontentloaded", timeout=60000)
                cookies = _douyin_cookies(ctx)
                if _is_logged_in(cookies):
                    cookies = _wait_uifid(ctx)
                    logger.info("已从专用浏览器读取到登录 Cookie")
                    return _to_cookie_str(cookies)
                logger.warning("专用浏览器中的抖音登录已失效，需要重新登录")
            finally:
                ctx.close()

        if not allow_interactive:
            raise CookieFetchError("专用浏览器未登录，请先运行：python get_cookie.py")

        # 弹出窗口让用户登录一次
        ctx = _launch(p, headless=False)
        try:
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            page.goto(HOME_URL, wait_until="domcontentloaded", timeout=60000)
            logger.info(f"请在弹出的浏览器窗口中登录抖音（{login_timeout} 秒内），登录后会自动继续…")
            end = time.time() + login_timeout
            while time.time() < end:
                if _is_logged_in(_douyin_cookies(ctx)):
                    break
                time.sleep(2)
            else:
                raise CookieFetchError("等待登录超时")
            # 登录后刷新一次，让页面把 UIFID 等补全
            page.reload(wait_until="domcontentloaded", timeout=60000)
            cookies = _wait_uifid(ctx)
            logger.info("登录成功，登录状态已保存，下次将自动获取")
            return _to_cookie_str(cookies)
        finally:
            ctx.close()


# --------------------------------------------------------------------------
# 来源 2：读取本机日常浏览器
# --------------------------------------------------------------------------
def fetch_from_browser(source: str) -> str:
    try:
        import browser_cookie3
    except ImportError:
        raise CookieFetchError("缺少 browser-cookie3，请执行：pip install browser-cookie3")
    loader = getattr(browser_cookie3, source, None)
    if loader is None:
        raise CookieFetchError(f"不支持的浏览器：{source}")
    try:
        jar = loader(domain_name="douyin.com")
    except PermissionError as err:
        if source == "safari":
            raise CookieFetchError(
                f"没有权限读取 Safari Cookie：{err}\n"
                "请打开 系统设置 → 隐私与安全性 → 完全磁盘访问权限，"
                "把你运行脚本用的终端（终端 / iTerm / PyCharm / VS Code）打开，然后重启该终端再试。"
            )
        raise CookieFetchError(f"读取 {source} Cookie 失败：{err}")
    except Exception as err:
        if source == "safari":
            raise CookieFetchError(f"读取 Safari Cookie 失败：{err}（Safari 只在 macOS 上可用）")
        raise CookieFetchError(
            f"读取 {source} Cookie 失败：{err}\n"
            "（Windows 新版 Chrome/Edge 常见，请关闭该浏览器后重试，或改用 DY_AUTO_COOKIE=profile）"
        )
    cookies = {}
    # 父域在前、子域在后，子域同名值覆盖
    for c in sorted(jar, key=lambda c: len(c.domain or "")):
        if c.domain.lstrip(".") in ("douyin.com", "www.douyin.com"):
            cookies[c.name] = c.value
    if not _is_logged_in(cookies):
        raise CookieFetchError(f"{source} 中没有找到已登录的抖音 Cookie，请先在该浏览器登录 www.douyin.com")
    if not cookies.get("UIFID"):
        logger.warning("该浏览器 Cookie 中没有 UIFID，作品/搜索接口可能被风控")
    return _to_cookie_str(cookies)


# --------------------------------------------------------------------------
# 写回 .env
# --------------------------------------------------------------------------
def resolve_env_path(env_path=None) -> str:
    if env_path:
        return env_path
    if os.path.exists(".env"):
        return os.path.abspath(".env")
    return os.path.join(ROOT_DIR, ".env")


def fetch_cookie(source="profile", force_login=False, allow_interactive=True) -> str:
    source = (source or "profile").strip().lower()
    if source in ("1", "true", "yes", "on", "profile", "auto"):
        return fetch_from_profile(force_login=force_login, allow_interactive=allow_interactive)
    if source in BROWSER_SOURCES:
        return fetch_from_browser(source)
    raise CookieFetchError(f"DY_AUTO_COOKIE 取值无效：{source}（可选 profile / {' / '.join(BROWSER_SOURCES)}）")


def refresh_env_cookie(source="profile", env_path=None, force_login=False,
                       allow_interactive=True) -> str:
    """获取最新 Cookie 并写入 .env 的 DY_COOKIES，返回 cookie 字符串。"""
    from dotenv import dotenv_values, set_key

    env_path = resolve_env_path(env_path)
    if not os.path.exists(env_path):
        open(env_path, "a", encoding="utf-8").close()

    cookie_str = fetch_cookie(source, force_login=force_login,
                              allow_interactive=allow_interactive)

    old = dotenv_values(env_path)
    old_sid = _parse_cookie_str(old.get("DY_COOKIES") or "").get("sessionid")
    new_sid = _parse_cookie_str(cookie_str).get("sessionid")
    if old_sid and new_sid and old_sid != new_sid:
        # 换了会话：旧的 ticket/私钥等属于旧会话，清掉以免混用
        for key in SESSION_BOUND_KEYS:
            if old.get(key):
                set_key(env_path, key, "")
            os.environ.pop(key, None)
        logger.info("检测到登录会话已变化，已清空旧会话绑定的 DY_TICKET 等凭证")

    set_key(env_path, "DY_COOKIES", cookie_str)
    os.environ["DY_COOKIES"] = cookie_str
    logger.info(f"DY_COOKIES 已更新 → {env_path}")
    return cookie_str

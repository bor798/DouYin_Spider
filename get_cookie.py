# coding=utf-8
"""
一键获取抖音登录 Cookie 并写入 .env

    python get_cookie.py                 # 专用浏览器：首次弹窗登录，之后全自动
    python get_cookie.py --login         # 换号 / 登录失效时，强制弹窗重新登录
    python get_cookie.py --source safari # 直接读取 Safari 里的登录（也可 chrome / edge / firefox）
"""
import argparse
import sys

from utils.browser_cookie import CookieFetchError, refresh_env_cookie


def main():
    parser = argparse.ArgumentParser(description="自动获取抖音登录 Cookie 并写入 .env")
    parser.add_argument("--source", default="profile",
                        help="profile(默认) / safari / chrome / edge / firefox / brave / chromium")
    parser.add_argument("--login", action="store_true", help="强制弹出浏览器重新登录")
    parser.add_argument("--env", default=None, help=".env 路径，默认项目根目录")
    args = parser.parse_args()
    try:
        cookie = refresh_env_cookie(args.source, env_path=args.env, force_login=args.login)
    except CookieFetchError as err:
        print(f"\n获取失败：{err}")
        sys.exit(1)
    print(f"\n完成，共 {len(cookie.split('; '))} 个 cookie 字段，已写入 DY_COOKIES。")


if __name__ == "__main__":
    main()

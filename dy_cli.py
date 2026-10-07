# coding=utf-8
"""
命令行入口：一条命令完成一次爬取，方便 AI 助手（OpenClaw 等）或脚本调用。

    python dy_cli.py comments --url <作品链接> [--max 100] [--no-reply] [--max-reply 20]
    python dy_cli.py comments --url <链接1> --url <链接2> ...      # 批量
    python dy_cli.py user     --url <用户主页链接> [--save excel]
    python dy_cli.py search   --query 关键词 [--num 20] [--sort 0] [--time 0] [--type 0]
    python dy_cli.py works    --url <作品链接1> --url <作品链接2> ...
    python dy_cli.py status                                        # 查看今日额度、未完成任务

约定（给程序/AI 看）：
  - 日志输出到 stderr；stdout 只在最后打印一行 JSON 结果，形如
      {"ok": true, "task": "comments", "count": 120, "json": "...", "excel": "...", ...}
  - 除 Excel 外，同时保存一份 .json（字段精简，适合交给 AI 分析）
  - 退出码：0 成功；2 触发风控已安全停止（有部分数据）；3 今日额度用完；1 其它错误
  - 防风控限速、断点续爬全部自动生效；中断后用同样的参数再运行即可续爬
  - 只包含"读取"类功能，不会发私信、评论、点赞
"""
import argparse
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.abspath(__file__))
os.chdir(ROOT)
sys.path.insert(0, ROOT)

EXIT_OK, EXIT_ERR, EXIT_RISK, EXIT_QUOTA = 0, 1, 2, 3

# 交给 AI 的精简字段（去掉头像、视频地址这类对分析没用、又很占篇幅的字段）
COMMENT_FIELDS = ("comment_id", "level", "parent_cid", "reply_to", "text", "digg_count",
                  "reply_count", "create_time", "ip_location", "author_digged", "nickname")
WORK_FIELDS = ("work_id", "work_url", "work_type", "title", "digg_count", "comment_count",
               "collect_count", "share_count", "admire_count", "topics", "create_time",
               "nickname", "user_id", "follower_count", "ip_location")


def _emit(result, code):
    print(json.dumps(result, ensure_ascii=False), flush=True)
    sys.exit(code)


def _slim(rows, fields):
    out = []
    for r in rows:
        item = {k: r.get(k) for k in fields if k in r}
        if "create_time" in item and isinstance(item["create_time"], (int, float)):
            item["create_time"] = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(item["create_time"]))
        out.append(item)
    return out


def _write_json(rows, fields, name):
    path = os.path.join(ROOT, "datas", "json_datas", f"{name}.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(_slim(rows, fields), f, ensure_ascii=False, indent=1)
    return path


def _excel(name):
    return os.path.join(ROOT, "datas", "excel_datas", f"{name}.xlsx")


def _comment_stats(rows):
    tops = sorted((r for r in rows if r.get("level") == "一级"),
                  key=lambda r: r.get("digg_count") or 0, reverse=True)[:3]
    ips = {}
    for r in rows:
        ip = r.get("ip_location") or "未知"
        ips[ip] = ips.get(ip, 0) + 1
    return {
        "top_comments": [{"text": r.get("text", "")[:60], "digg": r.get("digg_count")} for r in tops],
        "top_ip": sorted(ips.items(), key=lambda x: -x[1])[:5],
    }



def resolve_url(text):
    """接受各种写法：完整链接、App 分享出来的短链 v.douyin.com/xxx、
    或者带一堆文字的整段分享口令，统一转成 www.douyin.com 的标准链接。"""
    import re
    m = re.search(r"https?://[^\s，。]+", str(text))
    url = m.group(0).rstrip("/") + "/" if m else str(text).strip()
    if re.match(r"https?://v\.douyin\.com/", url):
        from utils import http_client
        resp = http_client.get(url, allow_redirects=False, timeout=20)
        loc = resp.headers.get("Location") or resp.headers.get("location") or ""
        if not loc:
            raise ValueError(f"短链解析失败：{url}")
        url = loc
    vid = re.search(r"/(?:video|note|slides)/(\d+)", url) or re.search(r"modal_id=(\d+)", url)
    if vid:
        return f"https://www.douyin.com/video/{vid.group(1)}"
    uid = re.search(r"/user/([\w-]+)", url)
    if uid:
        return f"https://www.douyin.com/user/{uid.group(1)}"
    return url


def build_parser():
    p = argparse.ArgumentParser(prog="dy_cli.py", description="抖音爬虫命令行（只读，自带防风控与断点续爬）")
    sub = p.add_subparsers(dest="task", required=True)

    c = sub.add_parser("comments", help="爬作品评论（可传多个 --url 批量）")
    c.add_argument("--url", action="append", required=True, help="作品链接，可重复")
    c.add_argument("--max", type=int, default=100, help="每个作品最多多少条一级评论，0=全部（默认 100）")
    c.add_argument("--no-reply", action="store_true", help="不爬楼中楼回复（更快）")
    c.add_argument("--max-reply", type=int, default=20, help="每条评论最多多少条回复，0=全部（默认 20）")

    u = sub.add_parser("user", help="爬某个用户的全部作品")
    u.add_argument("--url", required=True, help="用户主页链接")
    u.add_argument("--save", default="excel", choices=["excel", "all", "media", "media-video", "media-image"],
                   help="保存方式（默认 excel，只要数据不下视频）")

    s = sub.add_parser("search", help="按关键词搜索作品")
    s.add_argument("--query", required=True)
    s.add_argument("--num", type=int, default=20, help="要多少个作品（默认 20）")
    s.add_argument("--sort", default="0", choices=["0", "1", "2"], help="0 综合 1 最多点赞 2 最新")
    s.add_argument("--time", default="0", choices=["0", "1", "7", "180"], help="0 不限 1 一天 7 一周 180 半年")
    s.add_argument("--duration", default="", choices=["", "0-1", "1-5", "5-10000"], help="视频时长（分钟）")
    s.add_argument("--range", default="0", choices=["0", "1", "2", "3"], help="0 不限 1 看过 2 没看过 3 关注的人")
    s.add_argument("--type", default="0", choices=["0", "1", "2"], help="0 不限 1 视频 2 图文")
    s.add_argument("--save", default="excel", choices=["excel", "all", "media", "media-video", "media-image"])

    w = sub.add_parser("works", help="爬指定的几个作品")
    w.add_argument("--url", action="append", required=True, help="作品链接，可重复")
    w.add_argument("--name", default="", help="excel 文件名（默认自动生成）")
    w.add_argument("--save", default="excel", choices=["excel", "all", "media", "media-video", "media-image"])

    sub.add_parser("status", help="查看今日已用请求数、未完成的断点任务")
    return p


def cmd_status():
    from utils.safe_guard import config, today_count
    from utils.checkpoint import CHECKPOINT_DIR
    cfg = config()
    pending = sorted(f[:-5] for f in os.listdir(CHECKPOINT_DIR) if f.endswith(".json")) \
        if os.path.isdir(CHECKPOINT_DIR) else []
    _emit({"ok": True, "task": "status", "today_requests": today_count(), "daily_limit": cfg.per_day,
           "safe_mode": cfg.enabled, "unfinished_tasks": pending}, EXIT_OK)


def run(args):
    from utils.common_util import init
    import main as spider_main  # 复用 Data_Spider（含断点续爬）
    from utils.safe_guard import today_count

    if getattr(args, "url", None):
        if isinstance(args.url, list):
            args.url = [resolve_url(u) for u in args.url]
        else:
            args.url = resolve_url(args.url)
    auth, base_path = init()
    spider = spider_main.Data_Spider()
    result = {"ok": True, "task": args.task}

    if args.task == "comments":
        from dy_apis.douyin_api import parse_aweme_id
        items = []
        for url in args.url:
            aweme_id, _ = parse_aweme_id(url)
            rows = spider.spider_work_comments(
                auth, url, base_path, max_comments=args.max, with_reply=not args.no_reply,
                max_reply_per_comment=args.max_reply)
            name = f"评论_{aweme_id}"
            items.append({"work_url": url, "count": len(rows),
                          "json": _write_json(rows, COMMENT_FIELDS, name), "excel": _excel(name),
                          **_comment_stats(rows)})
        result.update(items[0] if len(items) == 1 else {"items": items})
        result["count"] = sum(i["count"] for i in items)

    elif args.task == "user":
        rows = spider.spider_user_all_work(auth, args.url, base_path, args.save)
        name = args.url.split("/")[-1].split("?")[0]
        result.update(count=len(rows), json=_write_json(rows, WORK_FIELDS, f"用户_{name}"),
                      excel=_excel(name) if args.save in ("excel", "all") else None)

    elif args.task == "search":
        rows = spider.spider_some_search_work(auth, args.query, args.num, base_path, args.save,
                                              args.sort, args.time, args.duration, args.range, args.type)
        result.update(count=len(rows), json=_write_json(rows, WORK_FIELDS, f"搜索_{args.query}"),
                      excel=_excel(args.query) if args.save in ("excel", "all") else None)

    elif args.task == "works":
        name = args.name or f"作品_{time.strftime('%Y%m%d_%H%M%S')}"
        rows = spider.spider_some_work(auth, args.url, base_path, args.save, name)
        result.update(count=len(rows), json=_write_json(rows, WORK_FIELDS, name),
                      excel=_excel(name) if args.save in ("excel", "all") else None)

    result["today_requests"] = today_count()
    _emit(result, EXIT_OK)


def main():
    args = build_parser().parse_args()
    if args.task == "status":
        cmd_status()
    from utils.safe_guard import RiskStop, DailyLimitReached
    try:
        run(args)
    except DailyLimitReached as e:
        _emit({"ok": False, "task": args.task, "reason": "daily_limit", "message": str(e),
               "resume": "明天用同样的参数再运行，会从断点继续"}, EXIT_QUOTA)
    except RiskStop as e:
        _emit({"ok": False, "task": args.task, "reason": "risk_control", "message": str(e),
               "partial_data": "已爬到的数据保存在 datas/excel_datas/ 下文件名带 _未完成 的 excel",
               "resume": "过几个小时、在 Safari 打开抖音完成验证后，用同样的参数再运行"}, EXIT_RISK)
    except KeyboardInterrupt:
        _emit({"ok": False, "task": args.task, "reason": "interrupted",
               "resume": "用同样的参数再运行，会从断点继续"}, EXIT_RISK)
    except SystemExit:
        raise
    except Exception as e:
        _emit({"ok": False, "task": args.task, "reason": "error",
               "message": f"{type(e).__name__}: {e}"}, EXIT_ERR)


if __name__ == "__main__":
    main()

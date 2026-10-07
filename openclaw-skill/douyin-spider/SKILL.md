---
name: douyin-spider
description: 爬取抖音（Douyin）公开数据并交给 AI 分析——作品评论（含楼中楼）、某个用户的全部作品、关键词搜索结果、指定作品详情。当用户发来抖音链接/分享口令，或要求"爬评论""分析这个视频的评论""看看这个博主的作品数据""搜一下抖音上关于 xx 的视频"时使用。只读，不会发私信、评论或点赞。
metadata: { "openclaw": { "os": ["darwin"] } }
---

# 抖音爬虫（douyin-spider）

本机项目路径：`~/DouYin_Spider`，统一通过启动器 `~/DouYin_Spider/dy.sh` 调用（它会自动进入项目目录并使用项目的 Python 环境）。

## 命令

```bash
# 1. 作品评论（最常用）。--url 可以直接用用户发来的整段分享口令、短链 v.douyin.com/xxx 或完整链接
~/DouYin_Spider/dy.sh comments --url "<作品链接或分享口令>" --max 100 --max-reply 20
#    --max N         最多 N 条一级评论（默认 100，0=全部）
#    --no-reply      不爬楼中楼回复（快很多）
#    --max-reply N   每条评论最多 N 条回复（默认 20）
#    多个作品：重复写 --url，例如 --url A --url B

# 2. 某个用户的全部作品数据（默认只存数据不下载视频）
~/DouYin_Spider/dy.sh user --url "<用户主页链接或分享口令>"

# 3. 关键词搜索作品
~/DouYin_Spider/dy.sh search --query "关键词" --num 20 --sort 0 --time 0 --type 0
#    --sort 0 综合 / 1 最多点赞 / 2 最新      --time 0 不限 / 1 一天 / 7 一周 / 180 半年
#    --type 0 不限 / 1 视频 / 2 图文           --duration "" / 0-1 / 1-5 / 5-10000（分钟）

# 4. 指定的几个作品的详情数据
~/DouYin_Spider/dy.sh works --url "<链接1>" --url "<链接2>"

# 5. 查看今日已用请求数、有没有中断待续的任务
~/DouYin_Spider/dy.sh status
```

## 读取结果

- 日志在 stderr；**stdout 最后一行是 JSON 结果**，例如：
  `{"ok": true, "task": "comments", "count": 132, "json": "/Users/.../datas/json_datas/评论_xxx.json", "excel": "...xlsx", "top_comments": [...], "top_ip": [...]}`
- 分析时读取 `json` 字段指向的文件（字段已精简：评论文本、点赞数、时间、IP 属地、层级、回复对象等；作品数据含标题、点赞/评论/收藏/分享数、话题、发布时间、作者粉丝数）。
- `excel` 是给用户自己看的表格，告诉用户路径即可，不需要你去读。
- 退出码：`0` 成功；`2` 触发抖音风控已安全停止（`ok:false, reason:"risk_control"`）；`3` 今日请求额度用完；`1` 其它错误（看 `message`）。

## 耗时（重要）

程序自带防风控限速：每次请求间隔 3~6 秒、每分钟最多 10 次，**这是故意的，不要尝试绕过**。粗略估算：
- 评论：每 5 条一级评论约 1 次请求，楼中楼另算。`--max 100 --max-reply 20` 通常 5~20 分钟。
- 用户作品 / 搜索：每 10~20 个作品约 1 次请求。

预计超过 2 分钟的任务，在后台运行并定期查看，避免执行超时：

```bash
mkdir -p ~/DouYin_Spider/datas/logs
nohup ~/DouYin_Spider/dy.sh comments --url "<链接>" --max 200 > ~/DouYin_Spider/datas/logs/job.out 2> ~/DouYin_Spider/datas/logs/job.log &
# 查看进度：tail -n 3 ~/DouYin_Spider/datas/logs/job.log
# 完成后结果 JSON 在：tail -n 1 ~/DouYin_Spider/datas/logs/job.out
```

开始前先告诉用户大概要多久；运行中可以把进度日志简要告诉用户。

## 规则（必须遵守）

1. **只读**。这个技能只用于读取数据。不要调用项目里发私信、发评论、点赞、收藏、发弹幕等任何写操作，也不要修改 `main.py`。
2. **不要绕过防风控**：不要设置 `DY_SAFE_MODE=0`，不要调低 `.env` 里的间隔或调高上限，不要并行运行多个爬取任务。只有用户明确要求时才可以改这些参数，并提醒他账号风险。
3. **遇到风控（退出码 2）就停下**，把 `message` 和 `resume` 告诉用户，不要自动重试或换参数再跑。已爬到的数据在文件名带 `_未完成` 的 excel 里，可以先拿这部分做分析。
4. **断点续爬**：任务中断后，用**完全相同的参数**再运行就会从断点继续，不会重复请求。
5. **量大先确认**：用户没说数量时用默认值；超过 500 条一级评论、`--max 0`（全部）、或要爬多个用户时，先和用户确认。
6. **隐私**：分析时关注内容和整体趋势（观点分布、情绪、高频话题、IP 分布等）。除非用户明确需要，不要逐个罗列评论者昵称，不要尝试把评论者和真实身份关联。
7. 如果报错提示 cookie 无效、未登录、缺少 UIFID：请用户在 Safari 打开 www.douyin.com 确认已登录并刷新一下页面，然后在终端运行 `cd ~/DouYin_Spider && source venv/bin/activate && python get_cookie.py --source safari`。

## 分析建议

拿到 JSON 后，按用户的问题分析。常见角度：
- 评论：主要观点和情绪倾向、被点赞最多的评论代表什么声音、高频关键词、争议点、IP 属地分布、作者回复/点赞了哪些评论。
- 作品：哪些作品数据最好、点赞/评论/收藏比例、发布时间规律、话题标签、爆款共同点。

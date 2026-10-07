#!/bin/bash
# 把爬虫安装成 OpenClaw 技能：复制 openclaw-skill/douyin-spider 到 ~/.agents/skills/，
# 并把技能里的项目路径改成本项目的实际位置。装完后在 OpenClaw 里开一个新对话即可使用。
set -e
cd "$(dirname "$0")"
PROJECT="$(pwd)"
DEST="$HOME/.agents/skills/douyin-spider"
mkdir -p "$DEST"
sed "s#~/DouYin_Spider#${PROJECT}#g" openclaw-skill/douyin-spider/SKILL.md > "$DEST/SKILL.md"
chmod +x dy.sh
echo "✅ 已安装到 $DEST/SKILL.md"
echo "   项目路径：$PROJECT"
echo "   在 OpenClaw 里新开一个对话，发一个抖音链接试试，例如：帮我爬这个视频的评论并分析 <链接>"

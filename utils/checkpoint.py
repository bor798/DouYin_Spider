# coding=utf-8
"""
断点续爬：把爬取进度存成 json，中断（风控停止 / Ctrl+C / 断网 / 关机）后
再次运行同一个任务，会从上次停下的地方继续，不会从头再来。

进度文件在 datas/checkpoints/ 下，任务完成后自动删除。
想强制从头开始：删掉对应的 json 文件即可。
"""
import hashlib
import json
import os
import re

from loguru import logger

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CHECKPOINT_DIR = os.path.join(ROOT_DIR, "datas", "checkpoints")


def make_key(*parts):
    """把任意参数变成一个稳定、可做文件名的 key。"""
    raw = "|".join(str(p) for p in parts)
    readable = re.sub(r"[^\w一-鿿-]+", "_", str(parts[0]))[:40] if parts else "task"
    return f"{readable}_{hashlib.md5(raw.encode('utf-8')).hexdigest()[:8]}"


class Checkpoint:
    def __init__(self, kind, key):
        os.makedirs(CHECKPOINT_DIR, exist_ok=True)
        self.path = os.path.join(CHECKPOINT_DIR, f"{kind}_{key}.json")

    def load(self):
        try:
            with open(self.path, encoding="utf-8") as f:
                state = json.load(f)
            logger.info(f"发现未完成的进度，从断点继续：{os.path.basename(self.path)}")
            return state
        except FileNotFoundError:
            return None
        except (OSError, ValueError) as err:
            logger.warning(f"进度文件损坏，将从头开始：{err}")
            return None

    def save(self, state):
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False)
        os.replace(tmp, self.path)  # 原子替换，写到一半断电也不会损坏旧进度

    def clear(self):
        for p in (self.path, self.path + ".tmp"):
            try:
                os.remove(p)
            except FileNotFoundError:
                pass

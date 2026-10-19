"""原始数据存储：JSONL 落地 + seen URL 增量去重 + 断点续爬。

设计决策（方案第四章）：解析规则一定会迭代，原文必须落地（可重解析不重爬）。
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

RAW_DIR = Path("data/raw/crawled")
SEEN_PATH = RAW_DIR / "seen_urls.json"


@dataclass
class RawAnnouncement:
    """一条原始公告（解析前的完整落地形态）。"""

    url: str
    title: str
    publish_time: str
    region: str
    purchaser_from_list: str   # 列表页自带的采购人（可能为"详情见公告正文"）
    body_text: str             # 详情页正文纯文本
    crawled_at: str            # ISO 时间戳


class RawStore:
    """JSONL 追加存储 + 已爬 URL 集合。"""

    def __init__(self, raw_dir: Path = RAW_DIR):
        self.raw_dir = Path(raw_dir)
        self.raw_dir.mkdir(parents=True, exist_ok=True)
        self.jsonl_path = self.raw_dir / "announcements.jsonl"
        self.seen_path = self.raw_dir / "seen_urls.json"
        self._seen: set[str] = self._load_seen()

    # ---- 增量去重 -----------------------------------------------------------

    def is_seen(self, url: str) -> bool:
        return url in self._seen

    def unseen(self, items: list) -> list:
        """过滤掉已爬过的条目（保持原顺序）。"""
        return [it for it in items if not self.is_seen(it.url)]

    def _load_seen(self) -> set[str]:
        if self.seen_path.exists():
            return set(json.loads(self.seen_path.read_text(encoding="utf-8")))
        return set()

    def _save_seen(self) -> None:
        self.seen_path.write_text(
            json.dumps(sorted(self._seen), ensure_ascii=False, indent=1), encoding="utf-8"
        )

    # ---- 存取 ---------------------------------------------------------------

    def save(self, raw: RawAnnouncement) -> None:
        """追加一条到 JSONL 并登记 URL（同 URL 幂等：重复 save 会先查重）。"""
        if self.is_seen(raw.url):
            return
        with self.jsonl_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(asdict(raw), ensure_ascii=False) + "\n")
        self._seen.add(raw.url)
        self._save_seen()

    def count(self) -> int:
        if not self.jsonl_path.exists():
            return 0
        return sum(1 for _ in self.jsonl_path.open(encoding="utf-8"))

    def load_all(self) -> list[RawAnnouncement]:
        """读回全部原始公告（解析阶段用）。"""
        if not self.jsonl_path.exists():
            return []
        out = []
        for line in self.jsonl_path.open(encoding="utf-8"):
            d = json.loads(line)
            out.append(RawAnnouncement(**d))
        return out

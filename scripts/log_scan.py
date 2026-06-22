#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
log_scan.py

用途：
    扫描日志文件最近 N 行，统计关键错误字样，例如 ERROR、脱机、死锁。

典型用法：
    python log_scan.py --file app.log --keyword ERROR --lines 4000
    python log_scan.py --file "D:\logs" --lines 4000
    python log_scan.py --file "D:\logs\Logs_*.txt*" --lines 4000
    python log_scan.py --file "D:\logs" --keyword ERROR --keyword 脱机 --keyword 死锁 --lines 4000
"""

from __future__ import annotations

import argparse
import glob
import os
import sys
from collections import Counter, deque
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


DEFAULT_KEYWORDS = ["ERROR", "脱机", "死锁"]


@dataclass
class LogLine:
    file: Path
    line_no: int
    text: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="扫描日志最近 N 行，统计关键错误字样。"
    )
    parser.add_argument(
        "--file",
        required=True,
        help=(
            "日志文件路径、日志目录，或通配符路径。"
            "例如 app.log、D:\\logs、D:\\logs\\Logs_*.txt*"
        ),
    )
    parser.add_argument(
        "--keyword",
        action="append",
        default=None,
        help=(
            "要扫描的关键字。可以写多次。"
            "不写时默认扫描：ERROR、脱机、死锁。"
        ),
    )
    parser.add_argument(
        "--lines",
        type=int,
        default=4000,
        help="只扫描最后多少行。默认 4000 行。",
    )
    parser.add_argument(
        "--pattern",
        default="Logs_*.txt*",
        help=(
            "当 --file 是目录时，用这个规则匹配日志文件。"
            "默认 Logs_*.txt*。"
        ),
    )
    parser.add_argument(
        "--show",
        type=int,
        default=20,
        help="最多展示多少条命中的原始日志。默认 20 条。设为 0 则不展示。",
    )
    parser.add_argument(
        "--case-sensitive",
        action="store_true",
        help="区分大小写。默认不区分大小写，所以 error / ERROR 都能匹配。",
    )
    return parser.parse_args()


def split_keywords(raw_keywords: list[str] | None) -> list[str]:
    """
    支持两种写法：
        --keyword ERROR --keyword 脱机
        --keyword ERROR,脱机,死锁
    """
    if not raw_keywords:
        return DEFAULT_KEYWORDS

    keywords: list[str] = []
    for item in raw_keywords:
        for part in item.split(","):
            keyword = part.strip()
            if keyword:
                keywords.append(keyword)

    return keywords or DEFAULT_KEYWORDS


def find_log_files(file_arg: str, pattern: str) -> list[Path]:
    """
    --file 支持三种情况：
    1. 单个文件
    2. 文件夹
    3. 通配符路径，例如 D:\\logs\\Logs_*.txt*
    """
    p = Path(file_arg)

    if p.is_file():
        return [p]

    if p.is_dir():
        files = [x for x in p.glob(pattern) if x.is_file()]
        return sort_files(files)

    # 当作通配符处理
    files = [Path(x) for x in glob.glob(file_arg) if Path(x).is_file()]
    return sort_files(files)


def sort_files(files: Iterable[Path]) -> list[Path]:
    """
    滚动日志一般需要从旧到新读取，最后再取最近 N 行。
    这里优先按修改时间排序，修改时间相同再按文件名排序。
    """
    return sorted(files, key=lambda x: (x.stat().st_mtime, x.name))


def detect_encoding(path: Path) -> str:
    """
    Windows 服务器上的中文日志常见编码：
    - UTF-8 / UTF-8 BOM
    - UTF-16
    - GBK / GB18030

    这里先看 BOM，再依次尝试几种常见编码。
    """
    sample = path.read_bytes()[:4096]

    if sample.startswith(b"\xef\xbb\xbf"):
        return "utf-8-sig"
    if sample.startswith(b"\xff\xfe") or sample.startswith(b"\xfe\xff"):
        return "utf-16"

    for encoding in ("utf-8", "gb18030", "utf-16"):
        try:
            sample.decode(encoding)
            return encoding
        except UnicodeDecodeError:
            continue

    # 实在判断不了就用 UTF-8 替换非法字符，保证脚本不中断。
    return "utf-8"


def iter_lines(path: Path) -> Iterable[LogLine]:
    encoding = detect_encoding(path)

    with path.open("r", encoding=encoding, errors="replace") as f:
        for line_no, line in enumerate(f, start=1):
            yield LogLine(
                file=path,
                line_no=line_no,
                text=line.rstrip("\r\n"),
            )


def collect_recent_lines(files: list[Path], line_limit: int) -> deque[LogLine]:
    recent_lines: deque[LogLine] = deque(maxlen=line_limit)

    for file in files:
        try:
            for item in iter_lines(file):
                recent_lines.append(item)
        except OSError as e:
            print(f"警告：无法读取文件 {file}：{e}", file=sys.stderr)

    return recent_lines


def contains(text: str, keyword: str, case_sensitive: bool) -> bool:
    if case_sensitive:
        return keyword in text
    return keyword.casefold() in text.casefold()


def scan_lines(
    lines: Iterable[LogLine],
    keywords: list[str],
    case_sensitive: bool,
) -> tuple[Counter[str], list[tuple[LogLine, list[str]]]]:
    counter: Counter[str] = Counter()
    hits: list[tuple[LogLine, list[str]]] = []

    for item in lines:
        matched_keywords = [
            keyword for keyword in keywords
            if contains(item.text, keyword, case_sensitive)
        ]

        if matched_keywords:
            for keyword in matched_keywords:
                counter[keyword] += 1
            hits.append((item, matched_keywords))

    return counter, hits


def format_summary(line_count: int, counter: Counter[str], keywords: list[str]) -> str:
    total = sum(counter.values())

    if total == 0:
        return f"最近 {line_count} 行中未发现 " + "、".join(keywords)

    parts = [f"{counter[keyword]} 条 {keyword}" for keyword in keywords if counter[keyword] > 0]
    return f"最近 {line_count} 行中发现 " + "，".join(parts)


def main() -> int:
    args = parse_args()

    if args.lines <= 0:
        print("--lines 必须大于 0", file=sys.stderr)
        return 2

    if args.show < 0:
        print("--show 不能小于 0", file=sys.stderr)
        return 2

    keywords = split_keywords(args.keyword)
    files = find_log_files(args.file, args.pattern)

    if not files:
        print("没有找到日志文件。", file=sys.stderr)
        print("请检查 --file 路径，或者在目录模式下检查 --pattern。", file=sys.stderr)
        return 1

    recent_lines = collect_recent_lines(files, args.lines)
    counter, hits = scan_lines(recent_lines, keywords, args.case_sensitive)

    print(f"扫描文件数：{len(files)}")
    print(f"扫描范围：最近 {len(recent_lines)} 行 / 设定 {args.lines} 行")
    print(format_summary(len(recent_lines), counter, keywords))

    if args.show > 0 and hits:
        print()
        print(f"最近命中明细，最多展示 {args.show} 条：")
        for item, matched_keywords in hits[-args.show:]:
            matched = ",".join(matched_keywords)
            print(f"[{item.file.name}:{item.line_no}][{matched}] {item.text}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

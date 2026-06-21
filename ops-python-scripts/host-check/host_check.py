#!/usr/bin/env python3
"""
host_check.py

用途：
1. local 模式：在脚本运行所在的 Linux/WSL 主机上执行只读巡检命令。
2. sample 模式：读取从生产服务器导出的命令输出文件，生成巡检报告。

示例：
python3 host_check.py local --service cron --port 20050
python3 host_check.py sample --sample-dir samples/10.120.81.79 --service lwops_agentd --port 20050
"""

import argparse
import datetime as dt
import os
import re
import socket
import subprocess
from pathlib import Path
from typing import Optional, Tuple


KEYWORDS = ["ERROR", "Error", "error", "FAILED", "Failed", "failed", "timeout", "refused", "critical", "CRITICAL"]


def now_str() -> str:
    return dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def filename_time_str() -> str:
    return dt.datetime.now().strftime("%Y%m%d_%H%M%S")


def run_command(command: list[str], timeout: int = 10) -> Tuple[bool, str]:
    """
    执行本地 Linux 命令。
    返回：(是否成功, 输出文本)
    """
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )

        output = ""
        if result.stdout:
            output += result.stdout
        if result.stderr:
            output += "\n[stderr]\n" + result.stderr

        if result.returncode == 0:
            return True, output.strip()
        return False, f"command failed, returncode={result.returncode}\n{output.strip()}"

    except FileNotFoundError:
        return False, f"command not found: {command[0]}"
    except subprocess.TimeoutExpired:
        return False, f"command timeout after {timeout}s: {' '.join(command)}"
    except Exception as e:
        return False, f"unexpected error: {e}"


def check_disk_local() -> str:
    ok, output = run_command(["df", "-h"])
    return format_section("DISK: df -h", ok, output)


def check_memory_local() -> str:
    ok, output = run_command(["free", "-h"])
    return format_section("MEMORY: free -h", ok, output)


def check_load_local() -> str:
    ok, output = run_command(["uptime"])
    return format_section("LOAD: uptime", ok, output)


def check_port_local(port: int) -> str:
    """
    端口检查优先使用 ss -tlnp。
    如果 ss 不可用，退回到 socket 方式测试 127.0.0.1:port 是否可连接。
    """
    ok, output = run_command(["ss", "-tlnp"])
    if ok:
        lines = []
        found = False
        for line in output.splitlines():
            if f":{port}" in line:
                lines.append(line)
                found = True

        if found:
            return format_section(f"PORT: {port}", True, "\n".join(lines))
        return format_section(f"PORT: {port}", False, f"port {port} is not listening")

    # fallback: socket check
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=3):
            return format_section(f"PORT: {port}", True, f"127.0.0.1:{port} is reachable")
    except Exception as e:
        return format_section(f"PORT: {port}", False, f"ss failed, socket check also failed: {e}")


def check_service_local(service: str) -> str:
    ok, output = run_command(["systemctl", "status", service, "--no-pager"], timeout=10)

    # systemctl status 对 inactive/failed 服务常返回非 0；这里仍然保留输出，便于排查。
    if ok:
        return format_section(f"SERVICE: {service}", True, output)

    return format_section(
        f"SERVICE: {service}",
        False,
        output + "\n\n提示：如果你在 WSL 中运行，systemd 可能不可用；这不代表脚本失败，代表当前环境不支持该检查。"
    )


def format_section(title: str, ok: bool, content: str) -> str:
    status = "OK" if ok else "WARN"
    return f"""## {title}
status: {status}

```text
{content if content else "(no output)"}
```
"""


def read_sample_file(sample_dir: Path, filename: str) -> Tuple[bool, str]:
    path = sample_dir / filename
    if not path.exists():
        return False, f"sample file not found: {path}"

    try:
        return True, path.read_text(encoding="utf-8", errors="replace").strip()
    except Exception as e:
        return False, f"failed to read {path}: {e}"


def check_disk_sample(sample_dir: Path) -> str:
    ok, output = read_sample_file(sample_dir, "df.txt")
    return format_section("DISK SAMPLE: df.txt", ok, output)


def check_memory_sample(sample_dir: Path) -> str:
    ok, output = read_sample_file(sample_dir, "free.txt")
    return format_section("MEMORY SAMPLE: free.txt", ok, output)


def check_load_sample(sample_dir: Path) -> str:
    ok, output = read_sample_file(sample_dir, "uptime.txt")
    return format_section("LOAD SAMPLE: uptime.txt", ok, output)


def check_port_sample(sample_dir: Path, port: int) -> str:
    ok, output = read_sample_file(sample_dir, "ss.txt")
    if not ok:
        return format_section(f"PORT SAMPLE: {port}", False, output)

    matched_lines = [line for line in output.splitlines() if f":{port}" in line]
    if matched_lines:
        return format_section(f"PORT SAMPLE: {port}", True, "\n".join(matched_lines))

    return format_section(f"PORT SAMPLE: {port}", False, f"port {port} not found in ss.txt")


def check_service_sample(sample_dir: Path, service: str) -> str:
    ok, output = read_sample_file(sample_dir, "systemctl.txt")
    if not ok:
        return format_section(f"SERVICE SAMPLE: {service}", False, output)

    active_hint = parse_systemctl_active_line(output)
    extra = f"\n\nparsed active line: {active_hint}" if active_hint else ""

    # 只做轻量判断，不强行代替真实 systemctl 结论。
    if "Active: active (running)" in output:
        return format_section(f"SERVICE SAMPLE: {service}", True, output + extra)

    return format_section(f"SERVICE SAMPLE: {service}", False, output + extra)


def parse_systemctl_active_line(output: str) -> Optional[str]:
    for line in output.splitlines():
        if "Active:" in line:
            return line.strip()
    return None


def scan_log_sample(sample_dir: Path, lines: int = 300) -> str:
    """
    可选：如果 sample_dir 里有 log.txt，就扫描最近 N 行关键字。
    没有 log.txt 不算失败。
    """
    path = sample_dir / "log.txt"
    if not path.exists():
        return format_section("LOG SAMPLE: log.txt", True, "log.txt not provided, skipped")

    try:
        all_lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        target_lines = all_lines[-lines:]
        hits = []
        for line in target_lines:
            if any(keyword in line for keyword in KEYWORDS):
                hits.append(line)

        if hits:
            return format_section(
                "LOG SAMPLE: keyword scan",
                False,
                f"found {len(hits)} suspicious lines in last {lines} lines:\n" + "\n".join(hits[-50:])
            )

        return format_section("LOG SAMPLE: keyword scan", True, f"no suspicious keyword found in last {lines} lines")
    except Exception as e:
        return format_section("LOG SAMPLE: keyword scan", False, f"failed to scan log.txt: {e}")


def build_report_header(mode: str, service: Optional[str], port: Optional[int], sample_dir: Optional[Path]) -> str:
    return f"""# Host Check Report

generated_at: {now_str()}
mode: {mode}
service: {service if service else "(not provided)"}
port: {port if port else "(not provided)"}
sample_dir: {sample_dir if sample_dir else "(not used)"}

---
"""


def save_report(report: str, output_dir: Path, prefix: str = "host_check") -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"{prefix}_{filename_time_str()}.md"
    path.write_text(report, encoding="utf-8")
    return path


def run_local(args: argparse.Namespace) -> str:
    sections = [
        build_report_header("local", args.service, args.port, None),
        check_disk_local(),
        check_memory_local(),
        check_load_local(),
    ]

    if args.port:
        sections.append(check_port_local(args.port))

    if args.service:
        sections.append(check_service_local(args.service))

    return "\n".join(sections)


def run_sample(args: argparse.Namespace) -> str:
    sample_dir = Path(args.sample_dir)

    sections = [
        build_report_header("sample", args.service, args.port, sample_dir),
        check_disk_sample(sample_dir),
        check_memory_sample(sample_dir),
        check_load_sample(sample_dir),
    ]

    if args.port:
        sections.append(check_port_sample(sample_dir, args.port))

    if args.service:
        sections.append(check_service_sample(sample_dir, args.service))

    sections.append(scan_log_sample(sample_dir, lines=args.log_lines))

    return "\n".join(sections)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Local/sample Linux host health check script."
    )

    subparsers = parser.add_subparsers(dest="mode", required=True)

    local_parser = subparsers.add_parser("local", help="Run checks on current local Linux/WSL host.")
    local_parser.add_argument("--service", help="service name, e.g. cron, ssh, lwops_agentd")
    local_parser.add_argument("--port", type=int, help="listening port to check, e.g. 20050")
    local_parser.add_argument("--output-dir", default="reports", help="directory to save report")

    sample_parser = subparsers.add_parser("sample", help="Read exported command outputs from sample directory.")
    sample_parser.add_argument("--sample-dir", required=True, help="directory containing df.txt/free.txt/uptime.txt/ss.txt/systemctl.txt")
    sample_parser.add_argument("--service", help="service name, e.g. lwops_agentd")
    sample_parser.add_argument("--port", type=int, help="listening port to check, e.g. 20050")
    sample_parser.add_argument("--log-lines", type=int, default=300, help="last N lines to scan in optional log.txt")
    sample_parser.add_argument("--output-dir", default="reports", help="directory to save report")

    return parser.parse_args()


def main() -> None:
    args = parse_args()

    if args.mode == "local":
        report = run_local(args)
    elif args.mode == "sample":
        report = run_sample(args)
    else:
        raise ValueError(f"unknown mode: {args.mode}")

    output_path = save_report(report, Path(args.output_dir))
    print(report)
    print(f"\nReport saved to: {output_path}")


if __name__ == "__main__":
    main()

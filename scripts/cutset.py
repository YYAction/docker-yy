import re
import csv
from pathlib import Path
from collections import defaultdict

raw = Path('export.txt').read_text(encoding='utf-8', errors='replace')

# Layer 1+2: 按 host header 切分，提取元信息
# 实际正则要看真实文件，下面这个是猜的
header_re = re.compile(r'^(\d+\.\d+\.\d+\.\d+)-(\S+)\s*\|\s*(SUCCESS|FAILED).*?>>', re.MULTILINE)

hosts = []  # [{'ip':..., 'hostname':..., 'status':..., 'body':...}, ...]
# TODO: 用 header_re.finditer 切出每个 host 的起止位置，body 是相邻两个 header 之间的内容

# Layer 3: 切 LISTEN / PROCESS / DOCKER 三段
def split_sections(body):
    sections = {}
    for name in ('LISTEN', 'PROCESS', 'DOCKER'):
        m = re.search(rf'---{name}---\n(.*?)(?=---|\Z)', body, re.DOTALL)
        sections[name] = m.group(1) if m else ''
    return sections

# Layer 4a: 解析 ss -tlnp 的输出，提端口
listen_re = re.compile(r':(\d+)\s+.*?users:\(\("([^"]+)"', re.DOTALL)
# ss 输出大概长这样: LISTEN 0 128 0.0.0.0:3306 *:* users:(("mysqld",pid=1234,fd=21))

# 端口 → 服务字典
PORT_SERVICE = {
    22: 'SSH',
    80: 'HTTP', 443: 'HTTPS',
    3306: 'MySQL', 6379: 'Redis',
    5672: 'RabbitMQ', 15672: 'RabbitMQ-Web',
    9876: 'RocketMQ-NameServer',
    10909: 'RocketMQ-Broker-VIP', 10911: 'RocketMQ-Broker', 10912: 'RocketMQ-HA',
    61616: 'ActiveMQ-OpenWire', 8161: 'ActiveMQ-Web',
    8848: 'Nacos', 9848: 'Nacos-gRPC',
    9000: 'Minio', 9001: 'Minio-Console',
    27017: 'MongoDB',
    8888: '后端 webapi',  # 图上标的
    # 不在表里的就标"未知端口"，让自己后面查
}

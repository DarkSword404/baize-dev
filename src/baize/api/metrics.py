"""B-20: Prometheus 指标端点 —— 暴露 /metrics 供 Prometheus 抓取。

不依赖 prometheus_client 库，手动输出 Prometheus exposition格式文本，
避免引入额外依赖。
"""

from __future__ import annotations

import time
from collections import defaultdict

__all__ = ["inc_counter", "set_gauge", "render_metrics"]

# 全局计数器（进程内）
_counters: dict[str, float] = defaultdict(float)
_gauges: dict[str, float] = defaultdict(float)
_started = time.time()


def inc_counter(name: str, value: float = 1.0) -> None:
    """递增计数器。"""
    _counters[name] += value


def set_gauge(name: str, value: float) -> None:
    """设置 gauge 值。"""
    _gauges[name] = value


def render_metrics() -> str:
    """渲染为 Prometheus exposition 格式文本。"""
    lines: list[str] = []
    uptime = time.time() - _started

    # uptime
    lines.append("# HELP baize_uptime_seconds 进程运行时间")
    lines.append("# TYPE baize_uptime_seconds gauge")
    lines.append(f"baize_uptime_seconds {uptime:.1f}")

    # counters
    for name, val in sorted(_counters.items()):
        lines.append(f"# TYPE {name} counter")
        lines.append(f"{name} {val}")

    # gauges
    for name, val in sorted(_gauges.items()):
        lines.append(f"# TYPE {name} gauge")
        lines.append(f"{name} {val}")

    return "\n".join(lines) + "\n"

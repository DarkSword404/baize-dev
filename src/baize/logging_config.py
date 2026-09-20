"""B-21: 结构化日志 + 文件轮转配置。

提供 JSON 格式日志（便于 ELK/Loki 采集）和按大小轮转的文件 handler。
通过环境变量控制:
- BAIZE_LOG_FORMAT: "json" | "text"（默认 text）
- BAIZE_LOG_FILE: 日志文件路径（为空则仅输出到 stderr）
- BAIZE_LOG_MAX_BYTES: 单文件最大字节（默认 10MB）
- BAIZE_LOG_BACKUP_COUNT: 保留日志文件数（默认 5）
"""

from __future__ import annotations

import json
import logging
import logging.handlers
import os
import sys
from datetime import datetime

__all__ = ["JsonFormatter", "setup_logging"]


class JsonFormatter(logging.Formatter):
    """JSON 行格式日志 formatter。"""

    def format(self, record: logging.LogRecord) -> str:
        log_entry = {
            "ts": datetime.utcnow().isoformat() + "Z",
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            log_entry["exc"] = self.formatException(record.exc_info)
        if record.extra:
            log_entry.update(record.extra)
        return json.dumps(log_entry, ensure_ascii=False)


def setup_logging() -> None:
    """配置全局日志（幂等，多次调用安全）。"""
    fmt = os.environ.get("BAIZE_LOG_FORMAT", "text")
    log_file = os.environ.get("BAIZE_LOG_FILE", "")
    max_bytes = int(os.environ.get("BAIZE_LOG_MAX_BYTES", str(10 * 1024 * 1024)))
    backup_count = int(os.environ.get("BAIZE_LOG_BACKUP_COUNT", "5"))

    root = logging.getLogger()
    # 避免重复配置
    if getattr(root, "_baize_configured", False):
        return
    root._baize_configured = True  # type: ignore[attr-defined]

    if fmt == "json":
        formatter = JsonFormatter()
    else:
        formatter = logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )

    handlers: list[logging.Handler] = []

    # stderr handler
    sh = logging.StreamHandler(sys.stderr)
    sh.setFormatter(formatter)
    handlers.append(sh)

    # 文件轮转 handler
    if log_file:
        os.makedirs(os.path.dirname(log_file) or ".", exist_ok=True)
        fh = logging.handlers.RotatingFileHandler(
            log_file, maxBytes=max_bytes, backupCount=backup_count, encoding="utf-8",
        )
        fh.setFormatter(formatter)
        handlers.append(fh)

    root.handlers = handlers
    root.setLevel(getattr(logging, os.environ.get("BAIZE_LOG_LEVEL", "INFO").upper(), logging.INFO))

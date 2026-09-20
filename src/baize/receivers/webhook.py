"""
Webhook Receiver — 基于 FastAPI 的 HTTP 数据接收端点
"""

import asyncio
import hmac
import logging
import os
from typing import Optional
from fastapi import HTTPException, Request, Response
from .manager import ReceiverManager
from .store import ReceiverStore

logger = logging.getLogger("baize.receivers.webhook")

# 允许进入 metadata 的非敏感请求头（避免 Authorization/Cookie 等泄露）
_SAFE_HEADERS = {
    "content-type",
    "content-length",
    "user-agent",
    "host",
    "x-forwarded-for",
    "x-real-ip",
    "x-baize-webhook-key",
}

# 显式事件 ID：若出现在 query 参数中，提升到 metadata 顶层，
# 供收件箱按"告警自带 ID"计算幂等指纹并展示（见 inbox.compute_fingerprint）。
_EVENT_ID_QUERY_KEYS = ("alert_id", "event_id")

# B-16: webhook 缓冲 - 处理管道忙/未启动时，事件进入 asyncio.Queue 等待重投，
# 不再直接返回 503 丢弃上游告警（旧版会让 SIEM/告警系统以为被拒绝而重试或丢失）。
# 队列在首次 handle_webhook 调用时惰性创建（需运行中的事件循环）。
_webhook_spool: "Optional[asyncio.Queue]" = None


def _get_spool() -> "asyncio.Queue":
    """B-16: 取（或惰性创建）webhook 缓冲队列。"""
    global _webhook_spool
    if _webhook_spool is None:
        _webhook_spool = asyncio.Queue()
    return _webhook_spool


async def _drain_spool(manager: ReceiverManager) -> None:
    """B-16: 管道可用时，重投缓冲队列里的事件。

    仅在 accept_webhook 成功后调用，尽力排空缓冲；仍不可用的事件
    回到队列尾部等待下次重投，避免一次 drain 阻塞。
    """
    spool = _get_spool()
    # 最多处理队列当前长度条事件，避免无限循环
    n = spool.qsize()
    for _ in range(n):
        try:
            item = spool.get_nowait()
        except asyncio.QueueEmpty:
            return
        receiver_id, data, content_type, source, metadata = item
        try:
            ok = manager.accept_webhook(
                receiver_id=receiver_id,
                data=data,
                content_type=content_type,
                source=source,
                metadata=metadata,
            )
        except Exception:  # noqa: BLE001
            logger.exception("webhook 缓冲重投异常，事件回到队列")
            ok = False
        if not ok:
            # 仍不可用：回到队列尾部等待下次重投
            await spool.put(item)
            return


def _validate_webhook_key(request: Request) -> None:
    """若配置了 BAIZE_WEBHOOK_API_KEY，则要求请求携带匹配密钥。

    未配置时保持匿名访问，向后兼容外部系统（SIEM 等）推送。
    """
    expected = os.getenv("BAIZE_WEBHOOK_API_KEY", "")
    if not expected:
        return
    supplied = request.headers.get("X-Baize-Webhook-Key") or request.query_params.get(
        "key", ""
    )
    if not supplied or not hmac.compare_digest(supplied, expected):
        raise HTTPException(status_code=401, detail="无效的 Webhook 密钥")


async def handle_webhook(request: Request, path: str) -> Response:
    """
    通用 Webhook 处理器
    路由 /api/v1/hook/{path} → 查找匹配 path 的 webhook 接收器
    """
    _validate_webhook_key(request)
    store = ReceiverStore.get_instance()
    manager = ReceiverManager.get()

    # 查找匹配此 webhook_path 的接收器
    receiver_id: Optional[str] = None
    for cfg in store.list_all():
        if cfg.kind == "webhook" and cfg.enabled and cfg.webhook_path == path:
            receiver_id = cfg.id
            break

    if receiver_id is None:
        # 也尝试直接用 path 作为 receiver_id
        cfg = store.get(path)
        if cfg and cfg.kind == "webhook" and cfg.enabled:
            receiver_id = path

    if receiver_id is None:
        return Response(content="receiver not found", status_code=404)

    # 读取原始数据
    content_type = request.headers.get("content-type", "application/octet-stream")
    raw = await request.body()

    # 元数据（仅保留白名单请求头，避免敏感头进入下游）
    source = request.client.host if request.client else "unknown"
    metadata = {
        "method": request.method,
        "headers": {
            k: v for k, v in request.headers.items() if k.lower() in _SAFE_HEADERS
        },
        "query_params": dict(request.query_params),
        "path": path,
    }
    # 显式事件 ID 提升到顶层（alert_id/event_id），供指纹幂等与列表展示使用
    for key in _EVENT_ID_QUERY_KEYS:
        value = metadata["query_params"].get(key)
        if value:
            metadata.setdefault(key, value)

    accepted = manager.accept_webhook(
        receiver_id=receiver_id,
        data=raw,
        content_type=content_type,
        source=source,
        metadata=metadata,
    )

    if accepted:
        # B-16: 管道可用，趁机把缓冲队列里积压的事件重投出去
        try:
            await _drain_spool(manager)
        except Exception:  # noqa: BLE001
            logger.warning("webhook 缓冲 drain 失败", exc_info=True)
        return Response(
            content='{"status":"accepted","receiver_id":"' + receiver_id + '"}',
            status_code=202,
            media_type="application/json",
        )
    else:
        # B-16: 处理管道忙/未启动 — 缓冲而非丢弃
        # 旧版直接返回 503 让上游丢数据；这里把事件入 asyncio.Queue，
        # 等管道恢复后由后续任意一次成功的 accept_webhook 触发 drain 重投。
        try:
            spool = _get_spool()
            await spool.put((receiver_id, raw, content_type, source, metadata))
        except Exception:  # noqa: BLE001
            logger.exception("webhook 事件入缓冲队列失败，回退到 503")
            return Response(
                content='{"status":"rejected","reason":"buffer failed"}',
                status_code=503,
                media_type="application/json",
            )
        return Response(
            content='{"status":"buffered","receiver_id":"' + receiver_id + '"}',
            status_code=202,
            media_type="application/json",
        )

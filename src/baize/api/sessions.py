"""Baize 会话管理（独立实现）。

管理对话会话及其消息历史，支持创建、读取、删除。
会话数据持久化到 ``~/.baize/sessions/``。
"""

from __future__ import annotations

import json
import secrets
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from baize.config import DEFAULT_BAIZE_DIR
from baize.pentest.blackboard import Blackboard

SESSION_DIR = DEFAULT_BAIZE_DIR / "sessions"


@dataclass
class SessionMessage:
    role: str
    content: str
    timestamp: str
    # 供后续扩展：token 用量、工具调用等


@dataclass
class Session:
    id: str
    agent: Optional[str]
    model: Optional[str]
    stateful: bool
    created_at: str
    updated_at: str
    pattern: Optional[str] = None
    browser_collab: bool = False
    messages: list[dict] = field(default_factory=list)
    # 协作模式（黑板驱动）：目标范围 + 成功条件
    scope: str = ""
    goal: str = ""
    # 运行时黑板，不在 __init__ 签名里（由 SessionManager 注入或恢复）
    blackboard: Optional[Blackboard] = field(default=None, repr=False)

    @property
    def history_length(self) -> int:
        return len(self.messages)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "agent": self.agent,
            "model": self.model,
            "stateful": self.stateful,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "history_length": self.history_length,
            "history": self.messages,
            "metadata": {},
            "pattern": self.pattern,
            "browser_collab": self.browser_collab,
            "scope": self.scope,
            "goal": self.goal,
            "blackboard": self.blackboard.snapshot() if self.blackboard else None,
        }


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class SessionManager:
    """会话的创建、读取、持久化。"""

    def __init__(self, directory: Path | None = None) -> None:
        self._dir = directory or SESSION_DIR
        self._dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._sessions: dict[str, Session] = {}
        self._load_all()

    def _load_all(self) -> None:
        if not self._dir.exists():
            return
        for f in self._dir.glob("*.json"):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                session = Session(
                    id=data["id"],
                    agent=data.get("agent"),
                    model=data.get("model"),
                    stateful=data.get("stateful", True),
                    created_at=data.get("created_at", ""),
                    updated_at=data.get("updated_at", ""),
                    pattern=data.get("pattern"),
                    browser_collab=data.get("browser_collab", False),
                    messages=data.get("messages", []),
                    scope=data.get("scope", ""),
                    goal=data.get("goal", ""),
                )
                # 恢复黑板（如有）
                bb_data = data.get("blackboard")
                if bb_data:
                    session.blackboard = Blackboard.from_dict(bb_data)
                self._sessions[session.id] = session
            except (json.JSONDecodeError, OSError, KeyError):
                continue

    def _save(self, session: Session) -> None:
        payload = {
            "id": session.id,
            "agent": session.agent,
            "model": session.model,
            "stateful": session.stateful,
            "created_at": session.created_at,
            "updated_at": session.updated_at,
            "pattern": session.pattern,
            "browser_collab": session.browser_collab,
            "messages": session.messages,
            "scope": session.scope,
            "goal": session.goal,
            "blackboard": session.blackboard.to_dict() if session.blackboard else None,
        }
        f = self._dir / f"{session.id}.json"
        tmp = f.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(f)

    def create_session(
        self,
        agent: Optional[str] = None,
        model: Optional[str] = None,
        stateful: bool = True,
        pattern: Optional[str] = None,
        browser_collab: bool = False,
        scope: str = "",
        goal: str = "",
    ) -> Session:
        session = Session(
            id=secrets.token_hex(12),
            agent=agent,
            model=model,
            stateful=stateful,
            created_at=_now(),
            updated_at=_now(),
            pattern=pattern,
            browser_collab=browser_collab,
            scope=scope,
            goal=goal,
        )
        # 协作模式：有 scope/goal 即初始化黑板（agent 可留空，由黑板动态派发）
        if scope or goal:
            session.blackboard = Blackboard(
                session_id=session.id, scope=scope, goal=goal,
            )
        with self._lock:
            self._sessions[session.id] = session
            self._save(session)
        return session

    def get_session(self, session_id: str) -> Session | None:
        return self._sessions.get(session_id)

    def list_sessions(self) -> list[Session]:
        return sorted(
            self._sessions.values(), key=lambda s: s.updated_at, reverse=True
        )

    def delete_session(self, session_id: str) -> bool:
        with self._lock:
            session = self._sessions.pop(session_id, None)
            if session is None:
                return False
            f = self._dir / f"{session_id}.json"
            if f.exists():
                f.unlink()
            return True

    def append_message(
        self,
        session_id: str,
        role: str,
        content: str,
        extra: Optional[dict] = None,
    ) -> Session | None:
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                return None
            msg: dict = {
                "role": role,
                "content": content,
                "timestamp": _now(),
            }
            if extra:
                msg.update(extra)
            session.messages.append(msg)
            session.updated_at = _now()
            self._save(session)
            return session

    def get_messages(self, session_id: str) -> list[dict]:
        session = self._sessions.get(session_id)
        return session.messages if session else []

    def save_assistant_draft(
        self,
        session_id: str,
        content: str,
        reasoning_trace: str = "",
        *,
        finished: bool = False,
    ) -> Session | None:
        """增量保存/定稿 assistant 回复草稿。

        流式执行期间反复调用：同一条草稿消息被原地更新，保证任务中断、
        客户端断连或服务异常时，已产出的正文与工具轨迹不会全部丢失
        （旧实现只在流正常结束时落盘，中断后前端只剩用户消息）。

        - 首次调用创建 ``draft=True`` 的 assistant 消息；
        - finished=True 时去掉 draft 标志（正式回复定稿）。
        """
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                return None
            draft: Optional[dict] = None
            for m in reversed(session.messages):
                if m.get("role") == "assistant" and m.get("draft"):
                    draft = m
                    break
                # 只回看本轮（遇到上一条正式消息就停止）
                if m.get("role") in ("user", "assistant"):
                    break
            if draft is None:
                draft = {
                    "role": "assistant",
                    "content": "",
                    "timestamp": _now(),
                    "draft": True,
                }
                session.messages.append(draft)
            draft["content"] = content or ""
            # 工具/思考轨迹可能很长，只保留尾部窗口供回放排障
            if reasoning_trace:
                draft["reasoning_trace"] = reasoning_trace[-8000:]
            if finished:
                draft.pop("draft", None)
            session.updated_at = _now()
            self._save(session)
            return session

    def reset_messages(self, session_id: str) -> bool:
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                return False
            session.messages = []
            session.updated_at = _now()
            self._save(session)
            return True

    def set_model(self, session_id: str, model: str) -> bool:
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                return False
            session.model = model or None
            session.updated_at = _now()
            self._save(session)
            return True

    def set_browser_collab(self, session_id: str, enabled: bool) -> bool:
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                return False
            session.browser_collab = bool(enabled)
            session.updated_at = _now()
            self._save(session)
            return True

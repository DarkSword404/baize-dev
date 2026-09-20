"""X-02: RBAC 角色权限控制 —— 基于 API Key 的角色鉴权。

角色层级:
- admin: 全部权限（管理容器、查看所有会话、系统配置）
- operator: 执行渗透任务、查看自己的会话
- viewer: 只读访问（查看会话、报告）

权限矩阵在 _PERMISSIONS 中定义。
"""

from __future__ import annotations

from enum import Enum
from typing import Optional

__all__ = ["Role", "has_permission"]


class Role(str, Enum):
    ADMIN = "admin"
    OPERATOR = "operator"
    VIEWER = "viewer"


# 权限矩阵: action -> 允许的角色集合
_PERMISSIONS: dict[str, set[Role]] = {
    # 会话管理
    "session:create": {Role.ADMIN, Role.OPERATOR},
    "session:delete": {Role.ADMIN, Role.OPERATOR},
    "session:list": {Role.ADMIN, Role.OPERATOR, Role.VIEWER},
    "session:read": {Role.ADMIN, Role.OPERATOR, Role.VIEWER},
    "session:chat": {Role.ADMIN, Role.OPERATOR},
    # 容器
    "container:bind": {Role.ADMIN, Role.OPERATOR},
    "container:unbind": {Role.ADMIN, Role.OPERATOR},
    "container:list": {Role.ADMIN, Role.OPERATOR, Role.VIEWER},
    # 报告
    "report:read": {Role.ADMIN, Role.OPERATOR, Role.VIEWER},
    "report:export": {Role.ADMIN, Role.OPERATOR},
    # 工具
    "tool:list": {Role.ADMIN, Role.OPERATOR, Role.VIEWER},
    "tool:manage": {Role.ADMIN},
    # 系统
    "system:config": {Role.ADMIN},
    "system:health": {Role.ADMIN, Role.OPERATOR, Role.VIEWER},
}


def has_permission(role: Optional[str], action: str) -> bool:
    """检查角色是否有权限执行某操作。

    Args:
        role: 角色名（字符串）。None 时默认 viewer。
        action: 操作名称（如 "session:create"）。
    """
    try:
        r = Role(role) if role else Role.VIEWER
    except ValueError:
        r = Role.VIEWER
    allowed = _PERMISSIONS.get(action)
    if allowed is None:
        return False
    return r in allowed

"""
Parallel 节点执行器 — 并发执行多个子节点。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from baize.orchestration.state import PipelineState, NodeRunRecord
from baize.orchestration.node_types import PipelineNode, ParallelBranch
from baize.orchestration.nodes.base import BaseNodeExecutor

logger = logging.getLogger(__name__)


class ParallelNodeExecutor(BaseNodeExecutor):
    """并行执行多个子分支 —— 借鉴 Prefect task.map() 和 LangGraph Send。"""

    node_type = "parallel"

    async def execute(self, node: PipelineNode, state: PipelineState) -> dict[str, Any]:
        updates: dict[str, Any] = self._record_start(node, state)

        try:
            branches = node.parallel_branches
            executed: list[dict[str, Any]] = []

            async def run_single_branch(branch: ParallelBranch, branch_idx: int) -> dict[str, Any]:
                """执行单个并行分支。"""
                child_node = branch.node
                if child_node is None:
                    return {
                        "branch_id": branch.node_id,
                        "status": "skipped",
                        "reason": "no node definition",
                    }
                executor = get_executor(child_node.type)
                result = await executor.execute(child_node, state)
                # 读取子节点状态：子节点失败时分支也标记为 failed，
                # 由父节点统一做失败传播（B-33）
                child_nodes = (result or {}).get("nodes") or {}
                child_rec = child_nodes.get(child_node.id, {}) if isinstance(child_nodes, dict) else {}
                branch_status = "failed" if child_rec.get("status") == "failed" else "completed"
                return {
                    "branch_id": branch.node_id,
                    "node_id": child_node.id,
                    "status": branch_status,
                    "result": result,
                }

            # 并发执行所有分支
            tasks = [run_single_branch(b, i) for i, b in enumerate(branches)]
            executed = await asyncio.gather(*tasks, return_exceptions=True)

            # 收集结果
            results = []
            for item in executed:
                if isinstance(item, Exception):
                    results.append({"status": "failed", "error": str(item)})
                else:
                    results.append(item)

            # 合并子节点的 nodes / dialog 记录（dialog 走 operator.add 累加）
            nodes = dict(state.get("nodes", {}))
            dialog_entries: list[dict[str, Any]] = []
            for item in executed:
                if isinstance(item, Exception) or not item.get("node_id"):
                    continue
                result_updates = item.get("result", {})
                if not isinstance(result_updates, dict):
                    continue
                sub_nodes = result_updates.get("nodes", {})
                if isinstance(sub_nodes, dict):
                    nodes.update(sub_nodes)
                sub_dialog = result_updates.get("dialog")
                if isinstance(sub_dialog, list):
                    dialog_entries.extend(sub_dialog)

            # B-33: 并行失败传播 —— 任一分支失败时把失败传播到父节点
            failed_branches = [r for r in results if r.get("status") == "failed"]
            if failed_branches:
                err_summary = "; ".join(
                    f"{r.get('branch_id', '?')}: {r.get('error', '子节点失败')}"
                    for r in failed_branches
                )
                logger.warning(
                    "Parallel 节点 '%s' 有 %d/%d 个分支失败，传播失败到父节点",
                    node.id, len(failed_branches), len(branches),
                )
                updates.update(self._record_failed(node, state, f"并行分支失败: {err_summary}"))
                updates["nodes"] = nodes
                if dialog_entries:
                    updates["dialog"] = dialog_entries
                # 失败分支标记：若配置了 error_target 则路由到失败分支
                updates["_err_route"] = node.id if getattr(node, "error_target", "") else ""
                updates["route"] = ""
                return updates

            data = {"branches": results, "total": len(branches), "completed": len(results)}
            updates.update(self._record_done(node, state, f"{len(results)} 个分支执行完毕", data))
            updates["nodes"] = nodes
            if dialog_entries:
                updates["dialog"] = dialog_entries
            updates["route"] = ""

        except Exception as e:
            logger.exception(f"Parallel 节点 '{node.id}' 执行失败")
            updates.update(self._record_failed(node, state, str(e)))
            updates["route"] = ""

        return updates


def get_executor(node_type: str) -> BaseNodeExecutor:
    """根据节点类型获取对应的执行器实例（带缓存）。"""
    from baize.orchestration.nodes.agent import AgentNodeExecutor
    from baize.orchestration.nodes.decision import DecisionNodeExecutor
    from baize.orchestration.nodes.ai_decision import AIDecisionNodeExecutor
    from baize.orchestration.nodes.confirm import ConfirmNodeExecutor
    from baize.orchestration.nodes.transform import TransformNodeExecutor
    from baize.orchestration.nodes.subpipeline import SubpipelineNodeExecutor
    from baize.orchestration.nodes.receiver import ReceiverNodeExecutor
    from baize.orchestration.nodes.datatransformer import DataTransformerNodeExecutor
    from baize.orchestration.nodes.end import EndNodeExecutor

    _registry = {
        "agent": AgentNodeExecutor,
        "decision": DecisionNodeExecutor,
        "ai_decision": AIDecisionNodeExecutor,
        "parallel": ParallelNodeExecutor,
        "confirm": ConfirmNodeExecutor,
        "transform": TransformNodeExecutor,
        "subpipeline": SubpipelineNodeExecutor,
        "receiver": ReceiverNodeExecutor,
        "datatransformer": DataTransformerNodeExecutor,
        "end": EndNodeExecutor,
    }
    cls = _registry.get(node_type)
    if cls is None:
        raise ValueError(f"未知节点类型: {node_type}")
    return cls()

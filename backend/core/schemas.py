"""
schemas.py

"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Dict, List, Optional
from .utils import gen_id, utc_now
from pydantic import BaseModel, Field, ConfigDict
from backend.app.butler.call_solver import AuthLevel


class _Schema(BaseModel):
    """
    所有 Schema 的公共配置：
    - extra="forbid"：禁止“偷偷多传字段”，避免契约被悄悄污染
    - validate_assignment=True：对象属性被重新赋值时也会校验（更利于调试）
    """
    model_config = ConfigDict(
        extra="forbid",
        validate_assignment=True,
        protected_namespaces=()
    )


# ----------------------------
# 枚举：状态 / 工作类型
# ----------------------------
class Status(str, Enum):
    """
    统一状态
    """
    DONE = "done"                        # 结束
    ERROR = "error"                      # 执行出错
    FAILED = "failed"                      # 执行失败
    RUNNING = "running"                        # 进行中
    WAITING = "waiting"                        # 等待中


class Component(str, Enum):
    """
    身份名称
    """
    BUTLER = "butler"
    SOLVER = "solver"
    WORKER = "worker"
    USER = "user"
    UNKNOWN = "unknown"
    SYSTEM = "system"
    AUDITOR = "auditor"

class SendType(str, Enum):
    """
    消息发送类型
    """
    UPWARD = "upward"     # 向上发送，通常是 Butler -> Solver -> Worker
    DOWNWARD = "downward" # 向下发送，通常是 Worker -> Solver -> Butler
    SELF = "self"   # 横向发送，通常是同级
    USER = "user"   # 直接向用户发送，心跳消息，请求权限或Solver汇报等


#消息类别
class MessageType(str, Enum):
    MESSAGE = "message"
    HEARTBEAT = "heartbeat"
    PERMISSION = "permission"
    REPORT = "report"
    EXTRA = "extra"


#身份类别
class MessageRole(str, Enum):
    USER = "user"
    TOOL = "tool"
    ASSISTANT = "assistant"


class NodeStatus(str, Enum):
    WAITING = "waiting"     # 等待反馈
    RUNNING = "running"     # 运行中
    COMPLETED = "completed" # 成功完成
    FAILED = "failed"       # 最终失败

# ----------------------------
# task node
# ----------------------------

class WorkerAttempt(_Schema):
    """被 Auditor 动作切分的单次执行子概览"""
    iteration: int = Field(default=0, description="该次尝试在 Worker 中循环迭代次数")
    model: Optional[str] = Field(default=None, description="本次执行使用的模型")
    cost: float = Field(default=0.0, description="本次尝试的花费")
    latency_s: float = Field(default=0.0, description="本次尝试的耗时")
    attribution: Optional[bool] = Field(default=None, description="Auditor 失败归因")
    audit_feedback: Optional[str] = Field(default=None, description="Auditor 给出的审核意见或报错信息")

class TaskNode(_Schema):
    """统一的任务节点（无论是 Solver 的根节点还是 Worker 的子节点）"""
    node_id: str = Field(description="节点的唯一标识（根节点为 solve_id，子节点为 work_id）")
    parent_id: Optional[str] = Field(default=None, description="上级节点的 ID，根节点为 None")
    goal: str = Field(description="该节点的目标描述")
    skill_name: str = Field(default="unknown", description="Skill 名称")
    tool_call_id: Optional[str] = Field(default=None, description="产生该任务节点的 LLM tool_call_id，便于追溯")
    pending_works: List[str] = Field(default_factory=list, description="当前所有待完成的子节点 ID")
    
    # --- 总概览信息 ---
    status: NodeStatus = Field(default=NodeStatus.WAITING, description="节点的总体状态")
    total_cost: float = Field(default=0.0, description="该节点及其所有子节点的总花费")
    total_latency_s: float = Field(default=0.0, description="该节点的总耗时")
    
    # --- 循环与审计信息 (Worker专用) ---
    attempts: List[WorkerAttempt] = Field(default_factory=list, description="历次执行与审计记录的子概览")
    
    # --- 拓扑关联 (配合 TaskManager 扁平化，仅存储 ID) ---
    children_ids: List[str] = Field(default_factory=list, description="下级子节点的 node_id 列表")


class Task(_Schema):
    """任务总上下文容器（仅做元数据与树的入口）"""
    solve_id: str = Field(description="总任务的唯一标识")
    channel_id: str = Field(description="用户在哪个渠道发起的任务")
    root_node_id: str = Field(description="根节点的 node_id（通常与 solve_id 相同）")


# ----------------------------
# 附件 Artifact
# ----------------------------

class ArtifactRef(_Schema):
    artifact_id: str = Field(default_factory=lambda: gen_id("art_"))
    description: str | None = Field(default=None)
    name: str | None = Field(default=None)
    uri: str
    mime: str | None = Field(default=None, description="可选：MIME 类型，如 text/plain, application/pdf")

# ----------------------------
# 消息: Message
# ----------------------------

class Message(_Schema):
    sender_id: str | None = Field(default=None, description="可选：本ctx ID")
    send_type: SendType 
    content: str
    tool_call_id: Optional[str] = Field(default=None, description="可选：关联的 LLM tool_call_id")
    sender: Component = Field(default=Component.USER, description="发送者身份")
    receiver_id: str | None = Field(default=None, description="可选：向下发送时的目标 ID")
    created_at: datetime = Field(default_factory=utc_now)
    message_role: MessageRole = Field(default=MessageRole.USER, description="消息角色")
    message_type: MessageType = Field(default=MessageType.MESSAGE, description="消息类型")
    artifacts: list[ArtifactRef] = Field(default_factory=list)
    data: dict | None = Field(default=None, description="可选：附加数据")
    status: Status = Field(default=Status.DONE, description="消息状态")


# ----------------------------
# Context：上下文
# ----------------------------

class Context(_Schema):
    status: Status = Field(default=Status.RUNNING, description="消息状态")
    owner_id: str 
    owner: Component = Field(default=Component.BUTLER, description="上下文所属组件")
    packets: list[Message] = Field(default_factory=list, description="关键通信包列表")
    complexity: float = Field(default=1.0, description="可选：当前任务难度")
    permission_type: AuthLevel = Field(default=AuthLevel.SMART, description="权限类型")
    work_dir: str | None = Field(default=None, description="可选：当前工作目录路径")
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    model_id: str | None = Field(default=None, description="可选：当前上下文使用的 LLM 配置 ID")

    def update_timestamp(self):
        self.updated_at = utc_now()

    def add_packet(self, packet: Message):
        self.packets.append(packet)
        self.update_timestamp()


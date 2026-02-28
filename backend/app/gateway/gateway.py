import os
import json
from dataclasses import dataclass, field
import time
from typing import Dict, List, Optional, Tuple
from backend.core.task_manager import TaskManager
from backend.core.schemas import Message, Context, MessageRole, MessageType, NodeStatus, SendType, Status, Component, Task, TaskNode
from backend.core.log import get_logger, log_message, log_event
import queue
import threading
from backend.llm.llm_config import LLMConfig


logger = get_logger("gateway")

# ----------------------------
# 1) handle 返回结构
# ----------------------------
@dataclass
class HandleResult:
    receiver_id: Optional[str]
    status: str               # "OK" / "REJECTED"
    reason: Optional[str] = None

# ----------------------------
# 2) Context Store (内存缓存 + 本地文件落盘)
# ----------------------------
@dataclass
class InMemoryContextStore:
    store: Dict[Tuple[Component, str], Context] = field(default_factory=dict)
    _lock: threading.RLock = field(default_factory=threading.RLock)
    task_manager: TaskManager = None # 需要注入以解析 work_id 到 solve_id 的映射

    def _get_effective_owner(self, owner: Component) -> Component:
        """核心修复：USER 和 BUTLER 逻辑上是同一个会话实体，强行合并缓存键"""
        if owner == Component.USER:
            return Component.BUTLER
        return owner

    def _get_file_path(self, owner: Component, context_id: str) -> str:
        """根据最新路径规范生成文件存储地址"""
        owner = self._get_effective_owner(owner)
        base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '../../workspace'))
        memory_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '../../memory'))

        if owner == Component.BUTLER:
            # 交互上下文落到 memory
            os.makedirs(memory_dir, exist_ok=True)
            return os.path.join(memory_dir, f"butler_{context_id}.json")
            
        elif owner == Component.SOLVER:
            # Solver上下文
            solver_dir = os.path.join(base_dir, context_id)
            os.makedirs(solver_dir, exist_ok=True)
            return os.path.join(solver_dir, f"{context_id}_context.json")
            
        elif owner == Component.WORKER:
            # Worker上下文：根据任务树深度嵌套构建目录
            solve_id = self.task_manager.work_to_solve.get(context_id)
            if not solve_id:
                solver_dir = os.path.join(base_dir, 'unknown')
            else:
                # 获取该 worker 所在任务树的路径
                segments = self.task_manager.get_work_path_segments(context_id)
                if segments:
                    # 排除自己（因为自己是文件名），前面的祖先节点全部作为嵌套目录
                    dir_segments = segments[1:]
                    solver_dir = os.path.join(base_dir, solve_id, *dir_segments)
                else:
                    solver_dir = os.path.join(base_dir, solve_id)
                    
            os.makedirs(solver_dir, exist_ok=True)
            return os.path.join(solver_dir, f"{context_id}_context.json")
            
        return os.path.join(base_dir, f"{context_id}_context.json")

    def get(self, owner: Component, context_id: str) -> Optional[Context]:
        owner = self._get_effective_owner(owner)
        if not context_id:
            return None
            
        with self._lock:
            if (owner, context_id) in self.store:
                return self.store[(owner, context_id)]
            
            file_path = self._get_file_path(owner, context_id)
            if os.path.exists(file_path):
                try:
                    with open(file_path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                        ctx = Context.model_validate(data)
                        self.store[(owner, context_id)] = ctx
                        return ctx
                except Exception as e:
                    get_logger("gateway").error(f"Failed to load context from {file_path}: {e}")
                    
        return None

    def set(self, owner: Component, ctx: Context, context_id: str) -> None:
        owner = self._get_effective_owner(owner)
        if context_id:
            with self._lock:
                self.store[(owner, context_id)] = ctx
            try:
                file_path = self._get_file_path(owner, context_id)
                ctx.work_dir = os.path.dirname(file_path)
                os.makedirs(ctx.work_dir, exist_ok=True)
                with open(file_path, "w", encoding="utf-8") as f:
                    json.dump(ctx.model_dump(mode='json'), f, ensure_ascii=False, indent=2)
            except Exception as e:
                log_event(logger, "CONTEXT_SAVE_FAILED", work_path=getattr(ctx, 'work_dir', 'unknown'), error=str(e), level=40)

    def exists(self, owner: Component, context_id: str) -> bool:
        owner = self._get_effective_owner(owner)
        if not context_id:
            return False
            
        with self._lock:
            if (owner, context_id) in self.store:
                return True
            file_path = self._get_file_path(owner, context_id)
            return os.path.exists(file_path)

# ----------------------------
# 3) Gateway：快速校验 + 异步落库
# ----------------------------
class Gateway:
    def __init__(self, store: Optional[InMemoryContextStore] = None) -> None:
        self.task_manager = TaskManager()
        self.store = store or InMemoryContextStore(task_manager=self.task_manager)
        self.llmcfg = LLMConfig.load()
        
        # 异步队列
        self._queue = queue.Queue()
        # --- 各层的收件箱 (Queue 路由表) ---
        self._routes: Dict[Component, queue.Queue] = {}

        self.running: Dict[Tuple[Component, str], bool] = {} # 记录正在处理的消息避免冲突
        self._run_lock = threading.RLock()
        
        self._workers: List[threading.Thread] = []
        self._start_consumer()

    def start_running(self, comp: Component, context_id: str):
        with self._run_lock:
            self.running[(comp, context_id)] = True

    def update_running(self, comp: Component, context_id: str, ctx: Context):
        self.store.set(comp, ctx, context_id)
        with self._run_lock:
            return self.running.get((comp, context_id), True)
    
    def stop_running(self, comp: Component, context_id: str):
        with self._run_lock:
            self.running[(comp, context_id)] = False

    def finish_running(self, comp: Component, context_id: str, ctx: Context):
        self.store.set(comp, ctx, context_id)
        with self._run_lock:
            if (comp, context_id) in self.running:
                self.running.pop((comp, context_id), None)

    def is_running(self, comp: Component, context_id: str):
        return (comp, context_id) in self.running

    def register_queue(self, comp: Component, q: queue.Queue) -> None:
        """注册某层的接收队列"""
        self._routes[comp] = q

    def _start_consumer(self):
        cpu_count = os.cpu_count() or 1
        max_workers = cpu_count * 5
        for i in range(max_workers):
            t = threading.Thread(
                target=self._worker_loop, 
                name=f"GatewayWorker-{i}", 
                daemon=True
            )
            t.start()
            self._workers.append(t)

    def _worker_loop(self):
        """多消费者循环"""
        while True:
            try:
                # 阻塞获取消息
                target_component, msg = self._queue.get()
                
                try:
                    self._process_message(target_component, msg)
                except Exception as e:
                    log_event(logger, "PROCESS_CRASH", error=str(e), level=40)
                finally:
                    self._queue.task_done()

            except Exception as e:
                log_event(logger, "GATEWAY_WORKER_QUEUE_ERROR", error=str(e), level=40)
                time.sleep(1)
        
    def _process_message(self, target_component: Component, msg: Message):
        context_id = msg.receiver_id
        ctx = self.store.get(target_component, context_id)
        target_q = self._routes.get(target_component)

        if ctx is None:
            file_path = self.store._get_file_path(target_component, context_id)
            work_path = os.path.dirname(file_path)

            ctx = Context(
                owner_id=context_id,
                owner=target_component, 
                permission_type=msg.data.get("permission_type", "smart") if msg.data else "smart",
                work_dir=work_path,
                model_id=self.llmcfg.defaults.get(target_component, None)
            )
            msg.message_role = MessageRole.USER
            
        else:
            if target_q:
                with target_q.mutex:
                    # 过滤掉 queue 中与当前消息 owner_id 相同的 Context
                    new_queue_items = [
                        item for item in target_q.queue 
                        if item.owner_id != context_id
                    ]
                    target_q.queue.clear()
                    target_q.queue.extend(new_queue_items)
            
            set_stop = True
            while self.is_running(target_component, context_id):
                if set_stop and target_component == Component.WORKER:
                    self.stop_running(target_component, context_id)
                    set_stop = False
                time.sleep(2)
            # 获取最新上下文
            ctx = self.store.get(target_component, context_id)        
        
        # 从全局 Task 树中获取当前目标的 entity (Task 或 TaskNode)
        target_entity = self.task_manager.get_entity(context_id)
        pending = getattr(target_entity, 'pending_works', []) if target_entity else []
        
        if target_component == Component.WORKER or target_component == Component.SOLVER:
            if len(pending) == 0 or msg.message_type == MessageType.EXTRA:
                ctx.add_packet(msg)
                if target_q: target_q.put(ctx) 
            elif msg.sender_id in pending:
                # 状态变更：根据消息的 status 映射 NodeStatus，并更新节点树状态
                node_status = NodeStatus.COMPLETED if msg.status == Status.DONE else (
                    NodeStatus.FAILED if msg.status in [Status.FAILED, Status.ERROR] else NodeStatus.COMPLETED
                )
                self.task_manager.mark_work_completed(msg.sender_id, status=node_status)
                
                ctx.add_packet(msg)
                # 重新检查剩余的 pending 数量，决定是否激活队列
                if len(getattr(target_entity, 'pending_works', [])) == 0 and target_q:
                    target_q.put(ctx)
            else:
                log_event(
                    logger, 
                    "UNEXPECTED_MSG_WHILE_PENDING", 
                    content=f"Msg from {msg.sender}({msg.sender_id}) not in pending_works: {pending}",
                    level=40
                )
        else:
            ctx.add_packet(msg)
            if target_q: target_q.put(ctx)

        self.store.set(target_component, ctx, context_id)
        log_message(logger, msg)

    def handle(self, msg: Message) -> HandleResult:
        try:
            target_component = None
            target_id = msg.receiver_id

            # ----------------------------------------------------
            # 路由解析逻辑 (抛弃了原 TraceContext 关联，使用基于 Task 的推导)
            # ----------------------------------------------------
            if msg.send_type == SendType.USER:
                target_component = Component.USER
                if msg.sender == Component.BUTLER:
                    target_id = msg.sender_id # Butler 层发送，其 sender_id 本身就是 channel_id
                else:
                    # 使用新的 get_task_by_node_id 方法
                    task = self.task_manager.get_task(msg.sender_id) or self.task_manager.get_task_by_node_id(msg.sender_id)
                    if task:
                        target_id = task.channel_id
                    else:
                        raise ValueError(f"Cannot find task for sender {msg.sender_id} to extract channel_id")

            elif msg.sender == Component.USER:
                target_component = Component.BUTLER
                target_id = msg.sender_id # User 发送的 sender_id 就是 channel_id

            elif msg.sender == Component.BUTLER:
                if msg.send_type == SendType.DOWNWARD:
                    target_component = Component.SOLVER
                    if not target_id:
                        # 交互层发往下层无明确ID时创建新的主 Task，注意属性变更为 tool_call_id
                        task = self.task_manager.create_task(channel_id=msg.sender_id, goal=msg.content, tool_call_id=msg.tool_call_id,model=self.llmcfg.defaults.get(target_component, None))
                        target_id = task.solve_id

            elif msg.sender == Component.SOLVER:
                if msg.send_type == SendType.UPWARD:
                    target_component = Component.BUTLER
                    task = self.task_manager.get_task(msg.sender_id)
                    target_id = task.channel_id if task else None
                elif msg.send_type == SendType.DOWNWARD:
                    target_component = Component.WORKER
                    if not target_id:
                        skill_name = msg.data.get('skill_name', 'unknown') if msg.data else 'unknown'
                        node = self.task_manager.add_node(
                            solve_id=msg.sender_id, 
                            parent_id=msg.sender_id, 
                            goal=msg.content, 
                            skill_name=skill_name,
                            tool_call_id=msg.tool_call_id,
                            model=self.llmcfg.defaults.get(target_component, None)
                        )
                        target_id = node.node_id

            elif msg.sender == Component.WORKER:
                task = self.task_manager.get_task_by_node_id(msg.sender_id)
                if not task:
                    raise ValueError(f"Task not found for node_id {msg.sender_id}")
                    
                if msg.send_type == SendType.UPWARD:
                    # 直接 O(1) 获取当前节点
                    node = self.task_manager.get_node(msg.sender_id)
                    # 如果有明确的父 Worker Node 则上抛给 Worker，否则上抛给 Solver
                    if node and node.parent_id and node.parent_id != task.solve_id:
                        target_component = Component.WORKER
                        target_id = node.parent_id
                    else:
                        target_component = Component.SOLVER
                        target_id = task.solve_id

                elif msg.send_type == SendType.DOWNWARD:
                    target_component = Component.WORKER
                    if not target_id:
                        skill_name = msg.data.get('skill_name', 'unknown') if msg.data else 'unknown'
                        node = self.task_manager.add_node(
                            solve_id=task.solve_id, 
                            parent_id=msg.sender_id, 
                            goal=msg.content, 
                            skill_name=skill_name,
                            tool_call_id=msg.tool_call_id,
                            model=self.llmcfg.defaults.get(target_component, None)
                        )
                        target_id = node.node_id

            if not target_component or not target_id:
                raise ValueError(f"Could not determine target for sender:{msg.sender} send_type:{msg.send_type}")

            msg.receiver_id = target_id
            
            # 放行推送处理队列
            self._queue.put((target_component, msg))
            return HandleResult(receiver_id=msg.receiver_id, status="OK")

        except Exception as e:
            log_event(logger, "REJECTED", content=str(e), sender=str(msg.sender), level=40)
            return HandleResult(receiver_id=getattr(msg, "receiver_id", None), status="REJECTED", reason=str(e))
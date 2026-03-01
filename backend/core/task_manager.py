import os
import json
from typing import Dict, List, Optional
from backend.core.schemas import NodeStatus, Task, TaskNode, WorkerAttempt
from backend.core.log import get_logger, log_event
import threading
from backend.core.utils import gen_id

logger = get_logger("core.task_manager")

# ----------------------------
# TaskManager: 负责管理上下游树结构与文件落盘
# ----------------------------
class TaskManager:
    def __init__(self):
        self.tasks: Dict[str, Task] = {}          # solve_id -> Task
        self.nodes: Dict[str, TaskNode] = {}      # node_id -> TaskNode
        self.work_to_solve: Dict[str, str] = {}   # node_id -> solve_id
        self._lock = threading.RLock()
        
        # Workspace中的solver文件夹路径
        self.base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '../workspace'))
        os.makedirs(self.base_dir, exist_ok=True)
        self._load_all_tasks()

    def _load_all_tasks(self):
        """启动时全量加载本地存在的Task与扁平化的Nodes"""
        if not os.path.exists(self.base_dir):
            return
        for solve_id in os.listdir(self.base_dir):
            task_dir = os.path.join(self.base_dir, solve_id)
            task_path = os.path.join(task_dir, "task.json")
            nodes_path = os.path.join(task_dir, "nodes.json") 
            
            if os.path.exists(task_path) and os.path.exists(nodes_path):
                try:
                    with open(task_path, "r", encoding="utf-8") as f:
                        task = Task.model_validate(json.load(f))
                        self.tasks[solve_id] = task
                        
                    with open(nodes_path, "r", encoding="utf-8") as f:
                        nodes_data = json.load(f)
                        for node_dict in nodes_data:
                            node = TaskNode.model_validate(node_dict)
                            self.nodes[node.node_id] = node
                            self.work_to_solve[node.node_id] = solve_id
                except Exception as e:
                    log_event(logger, "TASK_LOAD_ERROR", solve_id=solve_id, error=str(e))

    def create_task(self, channel_id: str, goal: str, tool_call_id: Optional[str] = None, model: Optional[str] = None) -> Task:
        solve_id = gen_id("sol_")
        root_node_id = solve_id # 根节点 ID 默认与 solve_id 相同，方便追溯
        
        with self._lock:
            # 1. 创建 Task 容器
            task = Task(solve_id=solve_id, channel_id=channel_id, root_node_id=root_node_id)
            self.tasks[solve_id] = task
            
            # 2. 创建 根节点 (Solver 对应的首个节点)
            root_node = TaskNode(
                node_id=root_node_id,
                parent_id=None,
                goal=goal,
                skill_name="solver_root",
                tool_call_id=tool_call_id,
                attempts=[WorkerAttempt(model=model or "default")] 
            )
            self.nodes[root_node_id] = root_node
            self.work_to_solve[root_node_id] = solve_id
            
        self.save_task(solve_id)
        return task

    def get_task(self, solve_id: str) -> Optional[Task]:
        with self._lock:
            return self.tasks.get(solve_id)

    def get_node(self, node_id: str) -> Optional[TaskNode]:
        with self._lock:
            return self.nodes.get(node_id)

    def get_task_by_node_id(self, node_id: str) -> Optional[Task]:
        """通过任意层级的 node_id 向上追溯到根 Task"""
        with self._lock:
            solve_id = self.work_to_solve.get(node_id)
            return self.tasks.get(solve_id) if solve_id else None

    def add_node(self, solve_id: str, parent_id: str, goal: str, skill_name: str, tool_call_id: Optional[str] = None, model: Optional[str] = None) -> TaskNode:
        with self._lock:
            if solve_id not in self.tasks:
                raise ValueError(f"Task {solve_id} not found")
            if parent_id not in self.nodes:
                raise ValueError(f"Parent node {parent_id} not found")
            
            new_node_id = gen_id("wrk_")
            new_node = TaskNode(
                node_id=new_node_id, 
                parent_id=parent_id, 
                goal=goal, 
                skill_name=skill_name, 
                tool_call_id=tool_call_id,
                attempts=[WorkerAttempt(model=model or "default")]
            )
            
            # 更新全局字典
            self.nodes[new_node_id] = new_node
            self.work_to_solve[new_node_id] = solve_id
            
            # 更新父节点拓扑与 pending 状态
            parent_node = self.nodes[parent_id]
            parent_node.children_ids.append(new_node_id)
            parent_node.pending_works.append(new_node_id)
            
        self.save_task(solve_id)
        return new_node

    def update_node_status(self, node_id: str, status: NodeStatus):
        """主动更新节点自身的状态，并落盘"""
        with self._lock:
            node = self.nodes.get(node_id)
            if node:
                node.status = status
                solve_id = self.work_to_solve.get(node_id)
                if solve_id:
                    self.save_task(solve_id)

    def mark_work_completed(self, node_id: str, status: NodeStatus = NodeStatus.COMPLETED):
        """安全状态变更接口，更新节点自身状态，并从父节点的 pending 列表中移除"""
        with self._lock:
            node = self.nodes.get(node_id)
            if not node:
                return
            
            node.status = status # 更新自身状态
            
            if node.parent_id:
                parent_node = self.nodes.get(node.parent_id)
                if parent_node and node_id in parent_node.pending_works:
                    parent_node.pending_works.remove(node_id)
                    
            solve_id = self.work_to_solve.get(node_id)
            if solve_id:
                self.save_task(solve_id)
                
    def cleanup_task(self, solve_id: str):
        """任务彻底完结后，从内存中释放，仅保留硬盘数据，防止内存泄漏"""
        with self._lock:
            if solve_id in self.tasks:
                self.save_task(solve_id) # 移除前确保最终状态已落盘
                del self.tasks[solve_id]
                
                # 找出属于该任务的所有节点并剔除
                nodes_to_remove = [nid for nid, sid in self.work_to_solve.items() if sid == solve_id]
                for nid in nodes_to_remove:
                    self.nodes.pop(nid, None)
                    self.work_to_solve.pop(nid, None)

    def save_task(self, solve_id: str):
        with self._lock:
            task = self.tasks.get(solve_id)
            if not task: return
            
            # 收集该 task 下的所有扁平 node
            task_nodes = [node.model_dump(mode='json') for nid, node in self.nodes.items() if self.work_to_solve.get(nid) == solve_id]

        task_dir = os.path.join(self.base_dir, solve_id)
        os.makedirs(task_dir, exist_ok=True)
        task_path = os.path.join(task_dir, "task.json")
        nodes_path = os.path.join(task_dir, "nodes.json")
        
        try:
            with open(task_path, "w", encoding="utf-8") as f:
                json.dump(task.model_dump(mode='json'), f, ensure_ascii=False, indent=2)
            with open(nodes_path, "w", encoding="utf-8") as f:
                json.dump(task_nodes, f, ensure_ascii=False, indent=2)
        except Exception as e:
            get_logger("task_manager").error(f"Failed to save task {solve_id}: {e}")

    def get_work_path_segments(self, node_id: str) -> List[str]:
        """O(1) 向上追溯层级路径，不再需要 DFS 递归"""
        with self._lock:
            if node_id not in self.nodes:
                return []
            path = []
            curr_id = node_id
            while curr_id:
                path.insert(0, curr_id)
                curr_node = self.nodes.get(curr_id)
                curr_id = curr_node.parent_id if curr_node else None
            return path

    def find_receiver_by_tool_call_id(self, tool_call_id: str) -> Optional[str]:
        """由于使用了扁平字典，遍历查找极快，且彻底告别递归"""
        if not tool_call_id:
            return None
        with self._lock:
            for nid, node in self.nodes.items():
                if getattr(node, 'tool_call_id', None) == tool_call_id:
                    return nid
        return None
    
    def get_entity(self, entity_id: str):
        """统一获取执行实体：如果传入的是 Task 的 solve_id，则返回其根 Node，因为只有 Node 才有 pending_works"""
        with self._lock:
            if entity_id in self.tasks:
                root_id = self.tasks[entity_id].root_node_id
                return self.nodes.get(root_id)
            if entity_id in self.nodes:
                return self.nodes[entity_id]
        return None
    
    def record_node_cost(self, node_id: str, cost: float, latency: float):
        """记录开销：更新最后一个 WorkerAttempt 并向上递归累加全部父节点"""
        with self._lock:
            if not node_id or node_id not in self.nodes:
                return

            solve_id = self.work_to_solve.get(node_id)
            curr_node = self.nodes.get(node_id)
            
            last_attempt = curr_node.attempts[-1]
            last_attempt.cost += cost
            last_attempt.latency_s += latency

            # 2. 向上递归累加自身及所有祖先节点
            curr_id = node_id
            while curr_id:
                node = self.nodes.get(curr_id)
                if not node:
                    break
                node.total_cost += cost
                node.total_latency_s += latency
                curr_id = node.parent_id
            
            if solve_id:
                self.save_task(solve_id)
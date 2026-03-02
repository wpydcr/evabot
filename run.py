# run.py
import asyncio
import os
import queue
import sys
import threading
import webbrowser
from contextlib import asynccontextmanager
from typing import Dict

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel
import uvicorn
from fastapi.staticfiles import StaticFiles

# 将当前根目录加入系统路径，确保能够正确 import backend 包
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from backend.app.gateway.gateway import Gateway
from backend.app.butler.butler import ButlerService
from backend.app.solver.solver import SolverService
from backend.app.workers.worker import WorkerService
from backend.core.schemas import Message, Component, MessageRole, MessageType, SendType
from backend.core.log import setup_logging
from backend.power.power import PowerManager
from backend.llm.llm_config import LLMConfig, ProviderConfig, ModelConfig

# ==========================================
# 1. 全局状态与初始化
# ==========================================
gateway = Gateway()
user_queue = queue.Queue()
active_websockets: Dict[str, WebSocket] = {}

@asynccontextmanager
async def lifespan(app: FastAPI):
    print("="*50)
    print("🚀 正在启动 Autonomous Agent 系统 (Web 模式)...")
    setup_logging()
    
    # 注册 USER 队列，接收推给前端的消息
    gateway.register_queue(Component.USER, user_queue)
    power = PowerManager()
    
    print("📦 加载微服务: Butler, Solver, Worker...")
    ButlerService(gateway)
    SolverService(gateway, power=power)
    WorkerService(gateway, power=power)
    
    # 启动异步后台任务消费 user_queue
    task = asyncio.create_task(listen_user_queue())
    
    print("✅ 系统启动完成！")
    print("🌐 正在唤起浏览器，或手动访问: http://127.0.0.1:8000")
    print("="*50)

    # 延迟 1 秒后自动打开浏览器，确保 uvicorn 已准备就绪
    def open_browser():
        import time
        time.sleep(1)
        webbrowser.open("http://127.0.0.1:8000")
    
    threading.Thread(target=open_browser, daemon=True).start()

    yield
    # 退出时清理
    task.cancel()

app = FastAPI(title="EvaBot", lifespan=lifespan)
frontend_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "frontend")
app.mount("/static", StaticFiles(directory=frontend_dir), name="static")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ==========================================
# 2. 前端页面路由
# ==========================================
@app.get("/")
def serve_index():
    """根路由：直接返回前端 HTML 页面"""
    html_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "frontend/index.html")
    if not os.path.exists(html_path):
        raise HTTPException(status_code=404, detail="未找到 index.html")
    return FileResponse(html_path)


# ==========================================
# 3. WebSocket 聊天与实时事件推送
# ==========================================
@app.get("/api/chat/history/{channel_id}")
def get_chat_history(channel_id: str, offset: int = 0, limit: int = 10):
    """获取指定频道的历史聊天记录 (分页)"""
    ctx = gateway.store.get(Component.BUTLER, channel_id)
    if not ctx or not ctx.packets:
        return {"messages": [], "has_more": False}
    
    valid_msgs = []
    for m in ctx.packets:
        # 安全地获取字段，防止读取旧的记忆数据时报错
        msg_type = getattr(m, 'message_type', MessageType.MESSAGE)
        send_type = getattr(m, 'send_type', None)
        sender = getattr(m, 'sender', None)
        
        # 筛选对用户可见的消息（排除掉底层的 Tool Call 等内部过程）
        if msg_type in [MessageType.MESSAGE, MessageType.REPORT, MessageType.HEARTBEAT]:
            if send_type == SendType.USER or sender == Component.USER:
                valid_msgs.append(m)
                
    total = len(valid_msgs)
    start = max(0, total - offset - limit)
    end = total - offset
    
    if start >= end or end <= 0:
        return {"messages": [], "has_more": False}
        
    msgs = valid_msgs[start:end]
    
    return {
        "messages": [m.model_dump(mode="json") for m in msgs],
        "has_more": start > 0
    }

async def listen_user_queue():
    """后台任务：监听发给用户的消息，并通过 WebSocket 推送给前端"""
    import queue # 确保文件顶部有 import queue
    while True:
        try:
            # 增加 timeout=1，防止底层线程池永久阻塞，允许进程优雅退出
            ctx = await asyncio.to_thread(user_queue.get, True, 1.0)
            
            if ctx.packets:
                last_msg = ctx.packets[-1]
                # 筛选出需要发给前端用户的消息
                if last_msg.send_type == SendType.USER and last_msg.sender != Component.USER:
                    channel_id = last_msg.receiver_id or ctx.owner_id
                    if channel_id in active_websockets:
                        ws = active_websockets[channel_id]
                        await ws.send_json(last_msg.model_dump(mode="json"))
            user_queue.task_done()
            
        except queue.Empty:
            # 队列空时短暂挂起协程，响应系统的 CancelledError (Ctrl+C)
            await asyncio.sleep(0.1)
            continue
        except asyncio.CancelledError:
            break
        except Exception as e:
            print(f"WebSocket 消息分发异常: {e}")

@app.websocket("/ws/chat/{channel_id}")
async def chat_endpoint(websocket: WebSocket, channel_id: str):
    """前端连接此 WebSocket 接口进行对话"""
    await websocket.accept()
    active_websockets[channel_id] = websocket
    try:
        while True:
            user_input = await websocket.receive_text()
            if not user_input.strip():
                continue
            
            # 构造用户的消息丢入网关
            msg = Message(
                sender_id=channel_id,
                sender=Component.USER,
                send_type=SendType.DOWNWARD,
                content=user_input,
                message_role=MessageRole.USER
            )
            gateway.handle(msg)
            
    except WebSocketDisconnect:
        if channel_id in active_websockets:
            del active_websockets[channel_id]

# ==========================================
# 4. 任务树 API (Task Tree)
# ==========================================
@app.get("/api/tasks")
def get_all_tasks():
    with gateway.task_manager._lock:
        tasks = []
        for task in gateway.task_manager.tasks.values():
            task_dict = task.model_dump(mode="json")
            # 获取该任务的根节点，提取 goal 作为历史任务的直观显示名
            root_node = gateway.task_manager.nodes.get(task.root_node_id)
            task_dict["goal"] = root_node.goal if root_node else "未知任务"
            
            # 动态获取任务文件夹的最后修改时间
            import os
            task_dir = os.path.join(gateway.task_manager.base_dir, task.solve_id)
            task_dict["_mtime"] = os.path.getmtime(task_dir) if os.path.exists(task_dir) else 0
            
            tasks.append(task_dict)
            
    # 严格按照最后活跃时间倒序（最新的排在最上面）
    tasks.sort(key=lambda x: x["_mtime"], reverse=True)
    
    # 清理临时辅助排序字段
    for t in tasks:
        t.pop("_mtime", None)
        
    return {"tasks": tasks}

@app.get("/api/tasks/{solve_id}")
def get_task_tree(solve_id: str):
    with gateway.task_manager._lock:
        task = gateway.task_manager.tasks.get(solve_id)
        if not task:
            raise HTTPException(status_code=404, detail="Task not found")
        
        nodes = [
            node.model_dump(mode="json") 
            for nid, node in gateway.task_manager.nodes.items() 
            if gateway.task_manager.work_to_solve.get(nid) == solve_id
        ]
        
    return {
        "task": task.model_dump(mode="json"),
        "nodes": nodes
    }

# ==========================================
# 5. 大模型配置 API (LLM Config)
# ==========================================
class ProviderReq(BaseModel):
    name: str
    config: ProviderConfig

# 改名：避开 model_ 前缀
class ModelReq(BaseModel):
    provider_name: str
    llm_config: ModelConfig  

# 改名：避开 model_ 前缀
class DefaultReq(BaseModel):
    role: str
    llm_ref: str

@app.get("/api/artifacts")
def get_artifact(uri: str):
    """根据 URI 获取并返回附件文件"""
    if not os.path.exists(uri):
        raise HTTPException(status_code=404, detail="文件不存在或已被移动")
    
    filename = os.path.basename(uri)
    return FileResponse(path=uri, filename=filename)

@app.get("/api/config/llm")
def get_llm_config():
    cfg = LLMConfig.load()
    return cfg.model_dump(mode="json")

@app.post("/api/config/llm/provider")
def upsert_provider(req: ProviderReq):
    cfg = LLMConfig.load()
    res = cfg.upsert_provider(req.name, req.config)
    if not res.success: raise HTTPException(status_code=400, detail=res.message)
    return {"success": True, "message": res.message}

@app.delete("/api/config/llm/provider/{provider_name}")
def delete_provider(provider_name: str):
    cfg = LLMConfig.load()
    res = cfg.delete_provider(provider_name)
    if not res.success: raise HTTPException(status_code=400, detail=res.message)
    return {"success": True, "message": res.message}

@app.post("/api/config/llm/model")
def upsert_model(req: ModelReq):
    cfg = LLMConfig.load()
    res = cfg.upsert_model(req.provider_name, req.llm_config)
    if not res.success: raise HTTPException(status_code=400, detail=res.message)
    return {"success": True, "message": res.message}

@app.delete("/api/config/llm/model/{provider_name}/{model_id}")
def delete_model(provider_name: str, model_id: str):
    cfg = LLMConfig.load()
    res = cfg.delete_model(provider_name, model_id)
    if not res.success: raise HTTPException(status_code=400, detail=res.message)
    return {"success": True, "message": res.message}

@app.post("/api/config/llm/default")
def update_default(req: DefaultReq):
    cfg = LLMConfig.load()
    res = cfg.update_default(req.role, req.llm_ref)
    if not res.success: raise HTTPException(status_code=400, detail=res.message)
    return {"success": True, "message": res.message}

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
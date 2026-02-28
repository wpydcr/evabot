# run.py
import sys
import os
import queue
import threading
import time

# 将当前根目录加入系统路径，确保能够正确 import backend 包
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from backend.app.gateway.gateway import Gateway
from backend.app.butler.butler import ButlerService
from backend.app.solver.solver import SolverService
from backend.app.workers.worker import WorkerService
from backend.core.schemas import Message, Component, MessageRole, SendType
from backend.core.log import setup_logging
from backend.core.utils import gen_id
from backend.power.power import PowerManager

def main():
    print("="*50)
    print("🚀 正在启动 Autonomous Agent 系统...")
    setup_logging()
    
    # 1. 初始化网关 (Gateway)
    gateway = Gateway()
    
    # 2. 注册 USER 队列
    # 网关会自动将目标为用户的 Context 推送到这个队列中
    user_queue = queue.Queue()
    gateway.register_queue(Component.USER, user_queue)
    power = PowerManager()
    
    # 3. 启动后台微服务
    print("📦 加载微服务: Butler, Solver, Worker...")
    ButlerService(gateway)
    SolverService(gateway, power=power)
    WorkerService(gateway, power=power)
    
    # 4. 初始化用户的 Channel ID (代表本次会话的唯一标识，彻底替代旧版的 TraceContext)
    session_id = gen_id("chan_")
    
    print("✅ 系统启动完成！(输入 'exit' 或 'quit' 退出)")
    print("="*50)

    # 5. 定义接收消息的后台线程
    def listen_replies():
        while True:
            try:
                # 阻塞等待发给 USER 的 Context
                ctx = user_queue.get()
                if ctx.packets:
                    last_msg = ctx.packets[-1]
                    # 过滤：依靠 send_type 精准打印由系统反馈给用户的消息
                    if last_msg.send_type == SendType.USER and last_msg.sender != Component.USER:
                        sender_name = last_msg.sender.value.upper()
                        print(f"\n🤖 [{sender_name}]: {last_msg.content}")
                        print("> ", end="", flush=True)
                user_queue.task_done()
            except Exception as e:
                print(f"接收消息异常: {e}")

    # 启动监听线程
    listener_thread = threading.Thread(target=listen_replies, daemon=True)
    listener_thread.start()

    # 6. 主循环：接收终端输入并发送
    time.sleep(0.5)  # 稍微等待内部日志打印完
    while True:
        try:
            user_input = input("\n> ")
            if user_input.lower() in ['exit', 'quit']:
                break
            if not user_input.strip():
                continue
            
            # 构造用户的消息，严格遵守最新的 Schema 规范
            msg = Message(
                sender_id=session_id,              # 发送方：用户的唯一 Channel ID
                sender=Component.USER,             # 发送方身份：用户
                send_type=SendType.DOWNWARD,       # 消息流向：向下派发给 Butler 层
                content=user_input,
                message_role=MessageRole.USER
            )
            
            # 丢给网关处理
            gateway.handle(msg)
            
        except KeyboardInterrupt:
            break
        except Exception as e:
            print(f"❌ 发送失败: {e}")

    print("\n👋 正在关闭系统...")

if __name__ == "__main__":
    main()
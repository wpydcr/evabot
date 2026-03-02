import os
import threading
import queue
import time
from typing import List

from backend.app.gateway.gateway import Gateway
from backend.core.schemas import Message, Component, Context, MessageRole, MessageType, SendType, Status
from backend.llm.llm import call_llm
from backend.core.log import get_logger, log_event
from .call_solver import get_solver_tool_schema, SolverTrigger
from backend.core.base_tools import get_base_tool, execute_tool
from backend.core.utils import load_prompt, extract_json

logger = get_logger("butler")

need_tools = ['communicate_with_downstream']

class ButlerService:
    def __init__(self, gateway: Gateway):
        self.gateway = gateway
        self.queue = queue.Queue()
        
        # 1. 注册队列到网关，这样网关收到 Butler 的消息就会推到 self.queue
        self.gateway.register_queue(Component.BUTLER, self.queue)
        
        # 3. 工具集
        self._tools_schema = get_solver_tool_schema() + get_base_tool(need_tools)
        
        # 4. 启动消费者线程
        self._workers: List[threading.Thread] = []
        self._running = True
        self.thread_run()

    def thread_run(self):
        cpu_count = os.cpu_count() or 1
        max_workers = cpu_count * 3
        for i in range(max_workers):
            t = threading.Thread(
                target=self._dispatch_loop, 
                name=f"Butler-{i}", 
                daemon=True
            )
            t.start()
            self._workers.append(t)
    
    def stop(self):
        self._running = False
        # 等待线程结束
        for t in self._workers:
            t.join(timeout=5)


    def _dispatch_loop(self):
        """异步消费循环"""
        while self._running:
            try:
                # 阻塞等待消息
                ctx: Context = self.queue.get()
                try:
                    self.gateway.start_running(Component.BUTLER, ctx.owner_id)
                    self._system_prompt = load_prompt(os.path.dirname(__file__),'soul.md',with_path=False)
                    self._process_context(ctx)
                    self.gateway.finish_running(Component.BUTLER, ctx.owner_id, ctx)
                except Exception as e:
                    # 捕获业务逻辑的未知异常，防止线程挂掉
                    error_response = Message(
                        sender=Component.BUTLER,
                        sender_id=ctx.owner_id,
                        send_type=SendType.USER,                 # 直接发给用户
                        content=f"系统遇到内部错误，无法处理您的请求。\n错误信息: {str(e)}",
                        status=Status.ERROR                      # 标记为 Error 状态
                        )
                    self.gateway.handle(error_response) # 直接发回用户，告知错误
                    log_event(logger, "PROCESS_ERROR", error=str(e), level=40)
                finally:
                    # 标记任务完成（对队列计数很重要）
                    self.queue.task_done()
                    self.gateway.finish_running(Component.BUTLER, ctx.owner_id, ctx)
                
            except Exception as e:
                log_event(logger, "WORKER_ERROR", error=str(e), level=40)
                time.sleep(1)

    def _process_context(self, ctx: Context):
        """
        业务逻辑核心：
        1. 从 Gateway 存储中恢复 Context
        2. 调用 LLM (传入 ctx 和 system_prompt)
        3. 路由结果
        """

        # B) 调用 LLM (Stateless call based on Context)
        response_msg = call_llm(
            ctx=ctx,
            system_prompt=self._system_prompt,
            tools=self._tools_schema     
            )

        # C) 结果解析与路由
        
        # 情况 1: 触发工具
        if response_msg.data and response_msg.data.get("tool_calls"):
            # 看有没有需要给用户说的内容
            if response_msg.content:
                user_msg = Message(
                    message_role=MessageRole.ASSISTANT,
                    sender=Component.BUTLER,
                    sender_id=ctx.owner_id,
                    send_type=SendType.USER,                          
                    content=response_msg.content,
                    data=response_msg.data
                )
                self.gateway.handle(user_msg)
            else:
                ctx.add_packet(response_msg) 
            # 补齐内部流转的必要字段再写入上下文
            response_msg.sender_id = ctx.owner_id
            response_msg.send_type = SendType.SELF
            
            for tc in response_msg.data["tool_calls"]:
                func = tc.get("function", {})
                tool_name = func.get("name")
                args_str = func.get("arguments", "{}")
                tool_call_id = tc.get("id")
                args_dict = extract_json(args_str)

                if tool_name == 'communicate_with_downstream':
                    provide_info = args_dict.get("provide_info")
                    target_tool_call_id = args_dict.get("tool_call_id") 
                    receiver_id = self.gateway.task_manager.find_receiver_by_tool_call_id(target_tool_call_id)
                    
                    if provide_info and receiver_id:
                        # 直接使用 DOWNWARD 让 Gateway 基于 Task 树找 Solver
                        info_msg = Message(
                            message_type=MessageType.EXTRA,
                            sender=Component.BUTLER,
                            sender_id=ctx.owner_id,
                            send_type=SendType.DOWNWARD,          
                            content=f'【系统通知：收到来自上游的最新补充信息】: {provide_info}',
                            receiver_id=receiver_id
                        )
                        self.gateway.handle(info_msg)
                        
                        ctx.add_packet(Message(
                            sender=Component.BUTLER,
                            sender_id=ctx.owner_id,
                            send_type=SendType.SELF,
                            content='已收到信息',
                            message_role=MessageRole.TOOL,
                            tool_call_id=tool_call_id
                        ))
                    else:
                        retry_msg = Message(
                            sender=Component.BUTLER,
                            sender_id=ctx.owner_id,
                            send_type=SendType.SELF,
                            content='信息发送失败，请检查提供的信息后重试。',
                            message_role=MessageRole.TOOL,
                            tool_call_id=tool_call_id                                    
                        )
                        ctx.add_packet(retry_msg)
                        self._process_context(ctx)

                elif tool_name == 'call_solver':
                    trigger_data = SolverTrigger(**args_dict)

                    solver_msg = Message(
                        sender=Component.BUTLER,
                        sender_id=ctx.owner_id,
                        send_type=SendType.DOWNWARD,              # 新建下发任务
                        content=trigger_data.intent,
                        tool_call_id=tool_call_id,
                        data={"permission_type": trigger_data.auth_level}
                    )
                    self.gateway.handle(solver_msg)
                    
                    tool_ack_msg = Message(
                        sender=Component.BUTLER,
                        sender_id=ctx.owner_id,
                        send_type=SendType.SELF,
                        content='任务已发布，正在处理...',
                        tool_call_id=tool_call_id,
                        message_role=MessageRole.TOOL
                    )
                    ctx.add_packet(tool_ack_msg)
                else:
                    if tool_name == 'edit_file':
                        args_dict["path"] = os.path.join(os.path.dirname(__file__),'soul.md')
                    result = execute_tool(tool_name, args_dict)
                    tool_message = Message(
                        sender=Component.BUTLER,
                        sender_id=ctx.owner_id,
                        send_type=SendType.SELF,
                        content=result,
                        data={"tool_name": tool_name, "args_str": args_str},
                        tool_call_id=tool_call_id,
                        message_role=MessageRole.TOOL
                    )
                    ctx.add_packet(tool_message)

        # 情况 2: 普通回复 -> 发给 User
        else:
            user_msg = Message(
                message_role=MessageRole.ASSISTANT,
                sender=Component.BUTLER,
                sender_id=ctx.owner_id,
                send_type=SendType.USER,                          
                content=response_msg.content
            )
            self.gateway.handle(user_msg)
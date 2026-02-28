
import os
import json
import queue
import threading
import time
from typing import List

from backend.core.schemas import Context, Message, Component, MessageRole, MessageType, NodeStatus, SendType, Status
from backend.llm.llm import call_llm
from backend.core.log import get_logger, log_event
from backend.core.utils import extract_json, load_prompt
from backend.power.power import PowerManager
from backend.llm.llm_config import LLMConfig
from backend.app.gateway.gateway import Gateway
from backend.core.base_tools import execute_tool, get_base_tool

logger = get_logger("solver.loop")


need_tools =['edit_file','communicate_with_downstream', 'communicate_with_upstream', 'use_skill']

class SolverService():
    """
    Solver loop
    """

    def __init__(self, gateway: Gateway, power: PowerManager = None):
        self.gateway = gateway  # 必须注入网关，用来给 Worker 发指令
        self.queue = queue.Queue()

        # 1. 注册队列到网关，这样网关收到 Solver 的消息就会推到 self.queue
        self.gateway.register_queue(Component.SOLVER, self.queue)

        self.power = power or PowerManager()
        self.agent_prompt = load_prompt(os.path.dirname(__file__),'agent.md')
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
                name=f"Solver-{i}",
                daemon=True
            )
            t.start()
            self._workers.append(t)

    def _dispatch_loop(self):
        """异步消费循环"""
        while self._running:
            try:
                # 阻塞等待消息
                ctx: Context = self.queue.get()

                try:
                    # 替换掉原本的 ctx.trace，使用 ctx.owner_id 作为 context_id
                    self.gateway.start_running(Component.SOLVER, ctx.owner_id)
                    self.agent_prompt = load_prompt(os.path.dirname(__file__),'agent.md')
                    self.run_loop(ctx)
                    self.gateway.finish_running(Component.SOLVER, ctx.owner_id, ctx)
                except Exception as e:
                    # 捕获业务逻辑的未知异常，防止线程挂掉
                    error_response = Message(
                        sender=Component.SOLVER,  # 发送者是我
                        sender_id=ctx.owner_id,
                        send_type=SendType.USER,  # 直接发给用户
                        content=f"系统遇到内部错误，无法处理您的请求。\n错误信息: {str(e)}",
                        status=Status.ERROR       # 标记为 Error 状态
                    )
                    self.gateway.handle(error_response)  # 直接发回用户，告知错误
                    log_event(logger, "PROCESS_ERROR", error=str(e), level=40)
                finally:
                    # 标记任务完成（对队列计数很重要）
                    self.queue.task_done()
                    self.gateway.finish_running(Component.SOLVER, ctx.owner_id, ctx)

            except Exception as e:
                log_event(logger, "SOLVER_ERROR", error=str(e), level=40)
                time.sleep(1)

    def run_init(self, ctx: Context):
        skill_xml_list = self.power.get_main_skill_xml()
        agent_prompt = self.agent_prompt.replace("{{skills}}", str(skill_xml_list))\
                                        .replace("{{intent}}", ctx.packets[0].content)
        resp = call_llm(ctx, system_prompt=agent_prompt, tools=get_base_tool(need_tools) )
        if resp.data:
            cost = resp.data.get("cost", 0.0)
            latency = resp.data.get("latency_s", 0.0)
            self.gateway.task_manager.record_node_cost(ctx.owner_id, cost, latency)
        ctx.add_packet(resp)
        return resp

    def run_loop(self, ctx: Context):
        if not ctx.packets:
            return
        self.gateway.task_manager.update_node_status(ctx.owner_id, NodeStatus.RUNNING)
        resp = self.run_init(ctx)
        if resp.data and resp.data.get("tool_calls"):
            heart_content = ''
            for tc in resp.data["tool_calls"]:
                func = tc.get("function", {})
                tool_name = func.get("name")
                args_str = func.get("arguments", "{}")
                tool_call_id = tc.get("id")
                args_dict = extract_json(args_str)

                if tool_name == 'communicate_with_upstream':
                    info_msg = Message(
                        status=Status.WAITING,
                        message_type=MessageType.EXTRA,
                        sender=Component.SOLVER,
                        sender_id=ctx.owner_id,
                        send_type=SendType.UPWARD,
                        content=f'我的tool_call_id是{ctx.packets[0].tool_call_id}。以下是我需要的信息: \n{args_dict.get("send_info")}'
                    )
                    self.gateway.handle(info_msg)
                    tool_ack_msg = Message(
                        sender=Component.SOLVER,
                        send_type=SendType.SELF,
                        content='已向上游请求信息，等待反馈...',
                        tool_call_id=tool_call_id,
                        message_role=MessageRole.TOOL
                    )
                    ctx.add_packet(tool_ack_msg)
                    
                elif tool_name == 'communicate_with_downstream':
                    provide_info = args_dict.get("provide_info")
                    target_tool_call_id = args_dict.get("tool_call_id")
                    
                    # 彻底丢弃 ctx.sub_agent，通过全局字典 O(1) 查找
                    receiver_id = self.gateway.task_manager.find_receiver_by_tool_call_id(target_tool_call_id)
                    
                    if provide_info and receiver_id:
                        info_msg = Message(
                            sender_id=ctx.owner_id,
                            message_type=MessageType.EXTRA,
                            sender=Component.SOLVER,
                            send_type=SendType.DOWNWARD,
                            receiver_id=receiver_id,
                            content=f'【系统通知：收到来自上游的最新补充信息】: {provide_info}'
                        )
                        self.gateway.handle(info_msg)
                        ctx.add_packet(Message(
                            sender=Component.SOLVER,
                            send_type=SendType.SELF,
                            content='已收到信息',
                            message_role=MessageRole.TOOL,
                            tool_call_id=tool_call_id
                        ))
                    else:
                        retry_msg = Message(
                            sender=Component.SOLVER,
                            send_type=SendType.SELF,
                            content='信息发送失败，请再次确认tool_call_id，请检查后重试。',
                            message_role=MessageRole.TOOL,
                            tool_call_id=tool_call_id                                    
                        )
                        ctx.add_packet(retry_msg)
                        self.run_loop(ctx)
                        
                elif tool_name == "use_skill":
                    skill_name = args_dict.get("skill_name")
                    goal = args_dict.get("goal", "")
                    needs_verification = args_dict.get("needs_self_verification", False)
                    worker_msg = Message(
                        sender_id=ctx.owner_id,
                        sender=Component.SOLVER,
                        send_type=SendType.DOWNWARD,
                        content='你负责的阶段性目标是: ' + goal,
                        data={
                            'skill_name': skill_name,
                            'needs_self_verification': needs_verification,
                            "permission_type": ctx.permission_type
                        },
                        tool_call_id=tool_call_id,
                    )

                    result = self.gateway.handle(worker_msg)
                    
                    # 删除了原本对 ctx.pending_works 和 ctx.sub_agent 的冗余维护
                    heart_content += f"Processing by skill: {skill_name} for {goal}\n"

                    tool_ack_msg = Message(
                        sender=Component.SOLVER,
                        send_type=SendType.SELF,
                        content='任务已发布，正在处理...',
                        tool_call_id=tool_call_id,
                        message_role=MessageRole.TOOL
                    )
                    ctx.add_packet(tool_ack_msg)
                    
                elif tool_name in need_tools:
                    start_ts = time.time()
                    result = execute_tool(tool_name, args_dict)
                    duration_s = round(time.time() - start_ts, 2)
                    self.gateway.task_manager.record_node_cost(ctx.owner_id, 0, duration_s)
                    tool_message = Message(
                        sender=Component.SOLVER,
                        send_type=SendType.SELF,
                        content=result,
                        data={"tool_name": tool_name, "args_str": args_str},
                        tool_call_id=tool_call_id,
                        message_role=MessageRole.TOOL
                    )
                    ctx.add_packet(tool_message)
                    self.run_loop(ctx)  # 执行完工具后继续循环，等待新的 tool_calls 或者任务结束信号
                else:
                    log_event(logger, "UNKNOWN_TOOL_CALL", tool_name=tool_name, level=40)

            if heart_content:
                self.gateway.task_manager.update_node_status(ctx.owner_id, NodeStatus.WAITING)
                heart_msg = Message(
                    sender_id=ctx.owner_id,
                    sender=Component.SOLVER,
                    send_type=SendType.USER,
                    content=f'【系统通知：心跳消息】\n{heart_content}',
                    message_type=MessageType.HEARTBEAT
                )
                self.gateway.handle(heart_msg)

        else:
            self.gateway.task_manager.update_node_status(ctx.owner_id, NodeStatus.COMPLETED)
            final_msg = Message(
                sender_id=ctx.owner_id,
                sender=Component.SOLVER,
                send_type=SendType.UPWARD,
                content=f'【系统通知：任务已结束】\n{resp.content.strip()}',
                message_type=MessageType.REPORT
            )
            self.gateway.handle(final_msg)


import os
import json
import queue
import threading
import time
from typing import List
import platform
from backend.app.workers.auditor import WorkerAuditor
from backend.core.base_tools import get_base_tool, execute_tool
from backend.core.schemas import Context, Message, Component, MessageRole, MessageType, NodeStatus, SendType, Status
from backend.llm.llm import call_llm
from backend.core.log import get_logger, log_event
from backend.core.utils import extract_json, load_prompt
from backend.power.power import PowerManager
from backend.app.gateway.gateway import Gateway

logger = get_logger("worker.worker")


class WorkerService():
    def __init__(self, gateway: Gateway, power: PowerManager = None):
        self.gateway = gateway  # 必须注入网关，用来给 Worker 发指令
        self.queue = queue.Queue()

        # 1. 注册队列到网关，这样网关收到 Worker 的消息就会推到 self.queue
        self.gateway.register_queue(Component.WORKER, self.queue)

        self.auditor = WorkerAuditor(self.gateway.task_manager)

        self.max_iterations = 20  # 最大迭代次数，防止死循环
        self.power = power or PowerManager()
        self.worker_prompt = load_prompt(os.path.dirname(__file__),'worker.md',False)
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
                name=f"Worker-{i}",
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
                    self.gateway.start_running(Component.WORKER, ctx.owner_id)
                    self.worker_prompt = load_prompt(os.path.dirname(__file__),'worker.md',False)
                    self.run_worker(ctx)
                    self.gateway.finish_running(Component.WORKER, ctx.owner_id, ctx)
                except Exception as e:
                    # 捕获业务逻辑的未知异常，防止线程挂掉
                    error_response = Message(
                        sender_id=ctx.owner_id,
                        sender=Component.WORKER,  # 发送者是我
                        send_type=SendType.USER,  
                        content=f"系统遇到内部错误，无法处理您的请求。\n错误信息: {str(e)}",
                        status=Status.ERROR       # 标记为 Error 状态
                    )
                    self.gateway.handle(error_response)  # 直接发回用户，告知错误
                    log_event(logger, "PROCESS_ERROR", error=str(e), level=40)
                finally:
                    # 标记任务完成（对队列计数很重要）
                    self.queue.task_done()
                    self.gateway.finish_running(Component.WORKER, ctx.owner_id, ctx)

            except Exception as e:
                log_event(logger, "WORKER_ERROR", error=str(e), level=40)
                time.sleep(1)


    def run_worker(self, ctx: Context):
        if not ctx.packets:
            return
        self.gateway.task_manager.update_node_status(ctx.owner_id, NodeStatus.RUNNING)
        os_name = platform.system()

        if ctx.packets[0].data and ctx.packets[0].data.get("skill_name"):
            skill_name = ctx.packets[0].data.get("skill_name", "")
            skill_content = self.power.get_skill_context(skill_name)
            skill_dir = self.power.get_skill_dir(skill_name)

            system_content = f'{self.worker_prompt}\n\n当前操作系统为：{os_name} \n 当前**skill路径**为：{skill_dir} \n 你的**工作区路径**是：{ctx.work_dir} \n\n{skill_content}\n\nAvailable Sub_Skills: \n{self.power.get_sub_skill_xml(skill_name)}'
            should_wait = False
            iteration = 0
            while iteration < 20:
                iteration += 1
                resp = call_llm(
                    ctx,
                    system_prompt=system_content,
                    tools=get_base_tool(),
                    model_override=ctx.model_id
                )
                ctx.add_packet(resp)
                if resp.data:
                    cost = resp.data.get("cost", 0.0)
                    latency = resp.data.get("latency_s", 0.0)
                    self.gateway.task_manager.record_node_cost(ctx.owner_id, cost, latency)

            
                if resp.data and resp.data.get("tool_calls"):
                    for tc in resp.data["tool_calls"]:
                        func = tc.get("function", {})
                        tool_name = func.get("name")
                        args_str = func.get("arguments", "{}")
                        tool_call_id = tc.get("id")
                        log_event(logger, "TOOL_CALL", tool_name=tool_name, args_str=args_str, level=20)
                        
                        args_dict = extract_json(args_str)
                        heart_content=''
                        if tool_name == "use_skill":
                            sub_skill_name = args_dict.get("skill_name")
                            if sub_skill_name==skill_name:
                                tool_ack_msg = Message(
                                    sender=Component.WORKER,
                                    send_type=SendType.SELF,
                                    content='你正在调用的skill_name与当前skill_name相同，可能导致死循环，这个操作是禁止的，你需要自己完成工作。',
                                    tool_call_id=tool_call_id,
                                    message_role=MessageRole.TOOL
                                )
                                ctx.add_packet(tool_ack_msg)
                                continue
                            sub_goal = args_dict.get("goal", "")
                            sub_needs_verification = args_dict.get("needs_self_verification", False) # 新增布尔判断
                            worker_msg = Message(
                                sender=Component.WORKER,
                                send_type=SendType.DOWNWARD,
                                sender_id=ctx.owner_id,
                                content='你负责的阶段性目标是: ' +sub_goal,
                                data={
                                    'skill_name': sub_skill_name,
                                    'needs_self_verification': sub_needs_verification,
                                    "permission_type": ctx.permission_type
                                },
                                tool_call_id=tool_call_id
                            )

                            result = self.gateway.handle(worker_msg)
                            # 彻底移除 ctx.pending_works 和 ctx.sub_agent 的手动维护
                            heart_content += f"Processing by skill: {sub_skill_name} for {sub_goal}\n"
                            tool_ack_msg = Message(
                                sender=Component.WORKER,
                                send_type=SendType.SELF,
                                content='任务已发布，正在处理...',
                                tool_call_id=tool_call_id,
                                message_role=MessageRole.TOOL
                            )
                            ctx.add_packet(tool_ack_msg)
                            should_wait = True
                            
                        elif tool_name == 'communicate_with_upstream':
                            info_msg = Message(
                                status=Status.WAITING,
                                message_type = MessageType.EXTRA,
                                sender=Component.WORKER,
                                sender_id=ctx.packets[0].receiver_id,
                                send_type=SendType.UPWARD,
                                receiver_id=ctx.packets[0].sender_id,
                                # 使用 tool_call_id
                                content=f'我的tool_call_id是{ctx.packets[0].tool_call_id}。以下是我需要的信息: \n{args_dict.get("send_info")}',
                                tool_call_id=tool_call_id
                            )
                            self.gateway.handle(info_msg)
                            tool_ack_msg = Message(
                                sender=Component.WORKER, # 自己发给自己的提示
                                send_type=SendType.SELF,
                                content='已向上游请求信息，等待反馈...',
                                tool_call_id=tool_call_id,
                                message_role=MessageRole.TOOL
                            )
                            ctx.add_packet(tool_ack_msg)
                            should_wait = True
                            
                        elif tool_name == 'communicate_with_downstream':
                            provide_info = args_dict.get("provide_info")
                            target_tool_call_id = args_dict.get("tool_call_id")
                            receiver_id = self.gateway.task_manager.find_receiver_by_tool_call_id(target_tool_call_id)
                            
                            if provide_info and receiver_id:
                                info_msg = Message(
                                    sender_id=ctx.owner_id,
                                    message_type = MessageType.EXTRA,
                                    sender=Component.WORKER,
                                    send_type=SendType.DOWNWARD,
                                    receiver_id=receiver_id,
                                    content=f'【系统通知：收到来自上游的最新补充信息】: {provide_info}'
                                )
                                self.gateway.handle(info_msg)
                                ctx.add_packet(Message(
                                    sender=Component.WORKER,
                                    send_type=SendType.SELF,
                                    content='已收到信息',
                                    message_role=MessageRole.TOOL,
                                    tool_call_id=tool_call_id
                                ))
                            else:
                                retry_msg = Message(
                                    sender=Component.WORKER,
                                    send_type=SendType.SELF,
                                    content='信息发送失败，请再次确认tool_call_id，请检查后重试。',
                                    message_role=MessageRole.TOOL,
                                    tool_call_id=tool_call_id                                    
                                )
                                ctx.add_packet(retry_msg)
                        else:
                            start_ts = time.time()
                            result = execute_tool(tool_name, args_dict)
                            duration_s = round(time.time() - start_ts, 2)
                            self.gateway.task_manager.record_node_cost(ctx.owner_id, 0, duration_s)
                            tool_message = Message(
                                sender=Component.WORKER,
                                send_type=SendType.SELF,
                                content=result,
                                data={"tool_name": tool_name, "args_str": args_str},
                                tool_call_id=tool_call_id,
                                message_role=MessageRole.TOOL
                            )
                            ctx.add_packet(tool_message)
                        
                    if heart_content:
                        heart_msg = Message(
                            sender_id=ctx.owner_id,
                            sender=Component.WORKER,
                            send_type=SendType.USER,
                            content=f'【系统通知：心跳消息】\n{heart_content}',
                            message_type=MessageType.HEARTBEAT
                        )
                        self.gateway.handle(heart_msg)
                    if not self.gateway.update_running(Component.WORKER, ctx.owner_id, ctx) or should_wait:
                        self.gateway.task_manager.update_node_status(ctx.owner_id, NodeStatus.WAITING)
                        return
                else:
                    if iteration > 8:
                        # 复杂任务结束，审核结果并评估资源使用
                        is_passed, has_verified, new_ctx = self.auditor.run_finish_audit(ctx, skill_content, iteration)
                        if not is_passed:
                            if new_ctx:
                                self.gateway.task_manager.update_node_status(ctx.owner_id, NodeStatus.FAILED)
                                report = Message(
                                    sender_id=ctx.owner_id,
                                    status=Status.FAILED,
                                    message_type=MessageType.REPORT,
                                    sender=Component.WORKER,
                                    send_type=SendType.UPWARD,
                                    receiver_id=ctx.packets[0].sender_id,
                                    content=f"【系统通知：任务失败终止】\nThe task is :{ctx.packets[0].content }\n\n经审计确认当前技能可能无法完成任务或存在逻辑缺陷。请检查任务内容，或使用 skill-manager 创建/更新技能。",
                                    tool_call_id=ctx.packets[0].tool_call_id
                                )
                                self.gateway.handle(report)
                                log_event(logger, "SKILL_FAILED", skill_name=skill_name, level=30)
                                return
                            elif not has_verified:
                                ctx = new_ctx
                                verif = Message(
                                    sender=Component.WORKER,
                                    send_type=SendType.SELF,
                                    content=f"经审计确认当前技能没有对最终结果自我测试/验证。请对交付结果进行验证。",
                                )
                                ctx.add_packet(verif)
                                iteration = 0
                                log_event(logger, "WORKER_AUDIT_RETRY", content="任务未通过质检，已重置上下文重新执行。")
                                continue
                            else:
                                ctx = new_ctx
                                iteration = 0
                                log_event(logger, "WORKER_AUDIT_RETRY", content="任务未通过质检，已重置上下文重新执行。")
                                continue

                    self.gateway.task_manager.update_node_status(ctx.owner_id, NodeStatus.COMPLETED)
                    report = Message(
                        sender_id=ctx.owner_id,
                        message_type=MessageType.REPORT,
                        sender=Component.WORKER,
                        send_type=SendType.UPWARD,
                        receiver_id=ctx.packets[0].sender_id,
                        content=f'【系统通知：任务已结束】\nThe task is :{ctx.packets[0].content}\nthe feedback is:\n{resp.content.strip()}',
                        tool_call_id=ctx.packets[0].tool_call_id
                    )
                    result = self.gateway.handle(report)
                    return

            # 超出循环次数，调用Auditor审计是否继续
            is_passed, new_ctx = self.auditor.run_timeout_audit(ctx, skill_content, iteration)
            if is_passed:
                ctx = new_ctx
                iteration = 0
                log_event(logger, "WORKER_AUDIT_RETRY", content="继续循环执行。")
            else:
                self.gateway.task_manager.update_node_status(ctx.owner_id, NodeStatus.FAILED)
                report = Message(
                    sender_id=ctx.owner_id,
                    status=Status.FAILED,
                    message_type=MessageType.REPORT,
                    sender=Component.WORKER,
                    send_type=SendType.UPWARD,
                    receiver_id=ctx.packets[0].sender_id,
                    content=f"【系统通知：任务失败终止】\nThe task is :{ctx.packets[0].content }\n\n经审计确认当前技能可能无法完成任务或存在逻辑缺陷。请检查任务内容，或使用 skill-manager 创建/更新技能。",
                    tool_call_id=ctx.packets[0].tool_call_id
                )
                self.gateway.handle(report)
                return

        else:
            log_event(logger, "WORKER_NO_TOOL_CALL", content=ctx.packets[-1].content, level=40)
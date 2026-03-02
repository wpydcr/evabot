import json
import os
from typing import Tuple
from backend.core.base_tools import execute_tool, get_base_tool
from backend.core.schemas import Context, Message, Component, MessageRole, MessageType, NodeStatus, SendType
from backend.llm.llm import call_llm
from backend.core.log import get_logger, log_event
from backend.llm.llm_config import LLMConfig
from backend.core.utils import extract_json, load_prompt

logger = get_logger("worker.auditor")
need_tools = ["edit_file"]

class WorkerAuditor:
    def __init__(self, task_manager):
        self.llm_config_loader = LLMConfig()
        self.task_manager = task_manager

    def _create_eval_context(self, ctx: Context, content: str) -> Context:
        """创建一个独立的、干净的上下文用于 Auditor 的 LLM 调用，避免污染业务 ctx"""
        eval_ctx = Context(owner=Component.AUDITOR, owner_id=ctx.owner_id)
        eval_ctx.model_id = self.llm_config_loader.defaults.get(Component.AUDITOR)
        
        # 补齐最新的必备字段 sender_id
        eval_msg = Message(
            sender_id=ctx.owner_id,
            sender=Component.AUDITOR,
            send_type=SendType.SELF,
            message_role=MessageRole.USER,
            content=content
        )
        eval_ctx.add_packet(eval_msg)
        return eval_ctx
    
    def _record_llm_cost(self, ctx: Context, resp: Message):
        """记录 Auditor 自身思考产生的花销到任务树"""
        if resp.data and self.task_manager:
            cost = resp.data.get("cost", 0.0)
            latency = resp.data.get("latency_s", 0.0)
            self.task_manager.record_node_cost(ctx.owner_id, cost, latency)

    def _update_attempt(self, ctx: Context, status: NodeStatus, attribution: bool = None, feedback: str = None, add_new: bool = False, iteration: int = 0):
        """同步审计结果到当前 Attempt，并在需要继续时压入一个新的 Attempt"""
        if not self.task_manager: return
        from backend.core.schemas import WorkerAttempt
        
        with self.task_manager._lock:
            node = self.task_manager.get_node(ctx.owner_id)
            if node and node.attempts:
                last_attempt = node.attempts[-1]
                last_attempt.iteration = iteration
                # 同步审计反馈与状态
                if not last_attempt.model:
                    last_attempt.model = ctx.model_id
                last_attempt.status = status
                if attribution is not None:
                    last_attempt.attribution = attribution
                if feedback:
                    last_attempt.audit_feedback = feedback
                
                # 如果审计决议是“继续执行”，则压入下一次迭代的全新载体
                if add_new:
                    node.attempts.append(WorkerAttempt(model=ctx.model_id))
            
            solve_id = self.task_manager.work_to_solve.get(ctx.owner_id)
            if solve_id:
                self.task_manager.save_task(solve_id)
    
    def get_context_summary(self, ctx: Context) -> str:
        """生成上下文摘要，供审计使用"""
        summary = f"【初始任务】：{ctx.packets[0].content}\n"
        summary += "【执行轨迹】：\n"
        for p in ctx.packets[1:]:
            content_preview = p.content[:50] if p.content else (", ".join([f"{t.get('name', 'unknown')}:{t.get('arguments', 'no arguments')[:50]}" for t in p.data.get('tool_calls', [])]) if p.data.get('tool_calls') else '<no content>')
            summary += f"- [{p.message_role.value}] {content_preview}...\n"
        return summary

    def calculate_complexity(self, ctx: Context) -> float:
        """评估任务实际难度 (1.0 - 5.0)"""
        # 提取执行轨迹的简要信息
        trace_summary = self.get_context_summary(ctx)
        prompt = trace_summary

        system_prompt = f"""请评估以下任务的实际执行复杂度 (1.0 到 5.0 分)，数值越高表示任务越复杂。
        以你的水平做这个任务，勉强能完成是5.0分。任意一个大模型都可以轻松完成，是1。0分。
        不要受到这个任务执行员的能力所干扰，完全根据任务本身的复杂度来评估。
        直接返回一个 JSON，格式：{{"actual_complexity": 3.0}}"""
        
        eval_ctx = self._create_eval_context(ctx, prompt)
        resp = call_llm(eval_ctx, system_prompt=system_prompt, json_mode=True)
        self._record_llm_cost(ctx, resp)
        
        res_dict = extract_json(resp.content)
        ctx.complexity = float(res_dict.get("actual_complexity", 3.0))


    def audit_task(self, ctx: Context, res_dict={}) -> dict:
        """核心裁判：评估是否通过及归因"""
        finally_report = ctx.packets[-1].content if len(ctx.packets) > 1 else ""
        # 提取执行轨迹与是否需要验证
        trace_summary = self.get_context_summary(ctx)
        needs_verification = ctx.packets[0].data.get("needs_verification", False) if ctx.packets[0].data else False

        prompt = f"【最终汇报】：\n{finally_report}"
        system_prompt = """你是一个严苛的审计员。判断执行员的【最终汇报】是否承认失败。承认失败 is_passed 是 false，否则为 true。请严格返回 JSON 格式： {"is_passed": true/false}"""
        
        if res_dict=={}:
            eval_ctx = self._create_eval_context(ctx, prompt)
            resp = call_llm(eval_ctx, system_prompt=system_prompt, json_mode=True)
            self._record_llm_cost(ctx, resp)
            
            audit_result = extract_json(resp.content)
            # 兼容处理
            if "is_passed" not in audit_result:
                return self.audit_task(ctx) # 重新审计一次，给模型一个机会修正格式问题
        
        if not res_dict.get("is_passed"):
            return {"is_passed": False, "have_verified": True}
        
        prompt = trace_summary
        system_prompt = """你是一个严苛的审计员。请基于【执行轨迹概要】，判断执行员的【最终汇报】是否来源可靠。
        审计规则（幻觉检测）：每一步的信息，必须在执行轨迹中有正确的操作获得。如果信息没有经过正确的工具执行获得，或者工具返回错误或为空，执行员却宣称完成，属于幻觉，is_passed 必须为 false。
        注意：这里我们不判定这些步骤是否必要（即使有些步骤看起来多余或者不合理，只要它们的结果是通过正确的工具调用获得的，就不算幻觉），我们只判定最终汇报的信息是否可靠。
        请严格返回 JSON 格式： {"is_passed": true/false}"""
        
        if res_dict=={}:
            eval_ctx = self._create_eval_context(ctx, prompt)
            resp = call_llm(eval_ctx, system_prompt=system_prompt, json_mode=True)
            self._record_llm_cost(ctx, resp)
            
            audit_result = extract_json(resp.content)
            # 兼容处理
            if "is_passed" not in audit_result:
                return self.audit_task(ctx) # 重新审计一次，给模型一个机会修正格式问题
        
        res_dict ={'is_passed': audit_result.get("is_passed"), 'have_verified': True}
        if needs_verification and audit_result.get("is_passed"):
            prompt = trace_summary
            system_prompt = """你是一个严苛的审计员。请基于【执行轨迹概要】，判断执行员的【最终汇报】是否经过自我验证。
            如果对任务要求的交付物，执行测试/验证，基于正确的工具调用获得了反馈，并且根据反馈才认定的通过，那么就算经过了自我验证，is_passed 才是 true。
            请严格返回 JSON 格式： {"have_verified": true/false}"""
            
            eval_ctx = self._create_eval_context(ctx, prompt)
            resp = call_llm(eval_ctx, system_prompt=system_prompt, json_mode=True)
            self._record_llm_cost(ctx, resp)
            verify_result = extract_json(resp.content)
            # 兼容处理
            if "have_verified" not in verify_result:
                return self.audit_task(ctx, res_dict) # 重新审计一次，给模型一个机会修正格式问题
            else:
                res_dict["have_verified"] = verify_result.get("have_verified")
            
        log_event(logger, "AUDIT_RESULT", node_id=ctx.owner_id, result=res_dict)
        return res_dict
 
    def update_worker(self, ctx: Context, failure_reason: str):
        
        system_prompt = """根据失败原因，总结通用性经验，调用edit_file工具，把经验更新到 worker.py 中。如果是任务特殊性，并不能给未来的其他工作提供指导意义的经验，就不需要更新文件了。"""
        worker_prompt = load_prompt(os.path.dirname(__file__),'worker.md',True)
        eval_ctx = self._create_eval_context(ctx, f'【失败原因】：{failure_reason}\n【之前经验与该文件路径】：\n{worker_prompt}')
        resp = call_llm(eval_ctx, system_prompt=system_prompt, tools=get_base_tool(need_tools))
        self._record_llm_cost(ctx, resp)
        
        if resp.data and resp.data.get("tool_calls"):
            tc = resp.data["tool_calls"][0]
            func = tc.get("function", {})
            args_str = func.get("arguments", "{}")
            args_dict = extract_json(args_str)
            result = execute_tool('edit_file', args_dict)
            log_event(logger, "WORKER_MD_UPDATE", node_id=ctx.owner_id, args=args_dict, result=result)


    def analyze_failure(self, ctx: Context, skill_desc: str = "") -> dict:
        """失败归因诊断：判断是模型智商不够还是 Skill 能力缺失"""
        original_intent = ctx.packets[0].content
        # 提取执行轨迹，用于分析到底卡在哪里
        trace_summary = "\n".join([f"[{p.message_role.value}]: {p.content[:200]}..." for p in ctx.packets[1:]])

        prompt = f'【初始任务】：{original_intent}\n【执行轨迹概要】：\n{trace_summary}\n【当前使用的 Skill 描述】：\n{skill_desc}'

        system_prompt = """你作为失败任务的复盘专家，请分析导致任务失败的具体原因。
        归因原则：
        1. "model": 逻辑混乱、死循环、没理解意图、反复调用错误的参数、幻觉。
        2. "skill": 缺关键说明、缺附件、API 不支持该操作。
        **特别注意**：model错误使用skill的情况，归因应该是 model 而不是 skill，因为根本原因是模型没理解好怎么用这个技能，而不是技能本身的问题。

        请严格返回 JSON 格式：
        {
            "failure_reason": "具体是因为什么原因失败的？",
            "attribution": "model" 或 "skill" 
        }"""
        
        eval_ctx = self._create_eval_context(ctx, prompt)
        resp = call_llm(eval_ctx, system_prompt=system_prompt, json_mode=True)
        self._record_llm_cost(ctx, resp)

        res_dict = extract_json(resp.content)
        # 兼容性兜底
        if "attribution" not in res_dict:
            return self.analyze_failure(ctx, skill_desc) # 重新分析一次，给模型一个机会修正格式问题
        
        if res_dict.get("attribution") == "model" and res_dict.get("failure_reason"):
            self.update_worker(ctx, res_dict.get("failure_reason"))

        log_event(logger, "AUDIT_FAILURE_ANALYSIS", node_id=ctx.owner_id, result=res_dict)
        return res_dict

    def need_continue(self, ctx: Context, skill_desc: str = "") -> dict:
        """判断是否继续进行，skill不行就算了，模型不行就换模型继续"""
        original_intent = ctx.packets[0].content
        # 提取执行轨迹，用于分析到底卡在哪里
        trace_summary = "\n".join([f"[{p.message_role.value}]: {p.content[:200]}..." for p in ctx.packets[1:]])

        prompt = f'【初始任务】：{original_intent}\n【执行轨迹概要】：\n{trace_summary}\n【当前使用的 Skill 描述】：\n{skill_desc}'

        system_prompt = """你正在核查这个执行好久没完成的任务，是因为什么这么久没做完。
        归因原则：
        1. "model": 逻辑混乱、死循环、没理解意图、反复调用错误的参数、幻觉。
        2. "skill": 缺工具、缺附件、API 不支持该操作、Skill 设计缺失。
        3. "task": 任务本身过于复杂或庞大。

        请严格返回 JSON 格式：
        {
            "reason": "如果是因为模型，请给出指导建议，纠正模型方向，其他可为空",
            "attribution": "model" 或 "skill" 或 "task"
        }"""
        
        eval_ctx = self._create_eval_context(ctx, prompt)
        resp = call_llm(eval_ctx, system_prompt=system_prompt, json_mode=True)
        self._record_llm_cost(ctx, resp)

        res_dict = extract_json(resp.content)
        # 兼容性兜底
        if "attribution" not in res_dict:
            return self.need_continue(ctx, skill_desc) # 重新分析一次，给模型一个机会修正格式问题

        log_event(logger, "AUDIT_FAILURE_ANALYSIS", node_id=ctx.owner_id, attribution=res_dict.get("attribution", 'unknown'))
        return res_dict
    
    def update_model(self, ctx: Context, is_passed: bool, failure_reason: str) -> float:
        actual_complexity = ctx.complexity
        config = self.llm_config_loader.load()
        current_model_ref = ctx.model_id

        provider_conf, current_model = config.get_model(current_model_ref)
        
        if not current_model:
            return current_model_ref # 找不到则兜底返回原样
            
        # 1. 计算分数增减
        score_diff = 0.0
        if is_passed and actual_complexity > current_model.capability_score:
            score_diff = 0.1 # 小马拉大车成功，加分
            log_event(logger, "MODEL_PERFORMANCE_UP", node_id=ctx.owner_id, model=current_model_ref, score=current_model.capability_score, score_diff=score_diff)
        elif not is_passed and actual_complexity < current_model.capability_score:
            score_diff = -0.2 # 杀鸡用牛刀失败，重扣
            log_event(logger, "MODEL_PERFORMANCE_DOWN", node_id=ctx.owner_id, model=current_model_ref, score=current_model.capability_score, score_diff=score_diff, failure_reason=failure_reason)
            
        if score_diff != 0:
            current_model.capability_score = max(1.0, min(5.0, current_model.capability_score + score_diff))
            
        # 2. 动态更新模型能力描述
        if not is_passed:
            update_prompt = f"""原描述：{current_model.description} , 失败原因：{failure_reason}"""
            eval_ctx = self._create_eval_context(ctx, update_prompt)
            desc_resp = call_llm(eval_ctx, system_prompt="这个模型执行任务失败了，请根据失败原因，找出这个模型不擅长的方向，并把这个信息浓缩成一个标签更新到模型描述里。\n直接返回纯文本的新描述，尽量不超过15字，不要任何解释。", json_mode=False)
            self._record_llm_cost(ctx, desc_resp)
            current_model.description = desc_resp.content.strip()

        # 尝试保存配置更新
        p_name = self.llm_config_loader.get_provider_name(provider_conf)
        if p_name:
            self.llm_config_loader.upsert_model(p_name, current_model)
        
        return score_diff


    def decide_next_model(self, ctx: Context) -> str:
        """HR 调度中心：算分、更新模型画像、挑选最便宜且胜任的模型"""
        current_model_ref = ctx.model_id

        best_model_ref = current_model_ref
        lowest_cost = float('inf')
        
        config = self.llm_config_loader.load() # 获取最新状态
        for p_key, p_val in config.providers.items():
            for m in p_val.models:
                if not m.enabled: continue
                if m.capability_score >= ctx.complexity:
                    cost_val = m.cost.input_1m + m.cost.output_1m
                    if cost_val < lowest_cost:
                        lowest_cost = cost_val
                        best_model_ref = f"{p_key}/{m.id}"
                        
        ctx.model_id = best_model_ref


    def compress_context(self, ctx: Context, failure_reason: str = ""):
        """压缩上下文：
        1. 识别出哪些步骤是无用的（如多余的 communicate_with_downstream，没反馈的 communicate_with_upstream，没反馈的工具调用等），哪些步骤是有价值的（如真正闭环了的工具调用，来自上游的补充信息等）
        2. 调用大模型压缩闭环的消息，提取有价值的信息，丢弃无用的过程
        3. 对于未闭环的工具调用，保留其关键信息（如工具名称、参数等），并将它们合并成一条消息，保留给模型作为下一次迭代的线索
        4. 注入失败原因（如果有的话），让模型明确知道上次失败的原因，避免继续在同一个问题上浪费资源，直接触发思考调整策略"""
        import copy
        
        if len(ctx.packets) <= 1:
            return

        downstream_ids = set(ctx.sub_agent.values()) if ctx.sub_agent else set()
        
        # 判断是否收到了上游的补充信息 (即来自非下游的 EXTRA 消息)
        has_reply_from_upstream = any(
            m.message_type == MessageType.EXTRA and m.sender_id not in downstream_ids 
            for m in ctx.packets[1:]
        )

        actions_to_compress = []
        unclosed_assistant_msgs = [] # 存放被修改过（只含未闭环 tool_call）的 ASSISTANT 消息
        unclosed_tool_tc_ids = set()

        # ==========================================
        # 1. 遍历消息，分离已闭环(需压缩)和未闭环(需保留)的内容
        # ==========================================
        for msg in ctx.packets[1:]:
            # A. 处理 ASSISTANT 的 tool_calls
            if msg.message_role == MessageRole.ASSISTANT and msg.data and "tool_calls" in msg.data:
                unclosed_tcs_for_this_msg = []
                
                for tc in msg.data["tool_calls"]:
                    name = tc.get("function", {}).get("name")
                    args = tc.get("function", {}).get("arguments", "{}")
                    tc_id = tc.get("id")
                    args_json = extract_json(args)

                    tc_id = tc.get("id")

                    if name == "communicate_with_downstream":
                        # 发送的 communicate_with_downstream 不需要保留，直接丢弃
                        continue
                        
                    elif name == "communicate_with_upstream":
                        # 发送的 communicate_with_upstream 如果没收到回复则压缩，收到回复则丢弃
                        if not has_reply_from_upstream:
                            actions_to_compress.append(f"我向上游请求了信息：{args_json.get('send_info')}，未收到回复")
                            
                    elif name == "use_skill":
                        # 根据 sub_agent 字典找到真实的下级 agent id，并寻找 REPORT 消息
                        sub_agent_id = ctx.sub_agent.get(tc_id)
                        report_msg = next(
                            (m for m in ctx.packets[1:] if m.message_type == MessageType.REPORT and m.sender_id == sub_agent_id), 
                            None
                        )
                        if report_msg:
                            # 已闭环，提取真反馈进行压缩
                            actions_to_compress.append(f"我调用了skill：{args_json.get('skill_name')}，反馈是：{report_msg.content}")
                        else:
                            # 未闭环，必须保留这个 tc
                            unclosed_tcs_for_this_msg.append(tc)
                            unclosed_tool_tc_ids.add(tc_id)
                            
                    else:
                        # 本地工具，从后续的 TOOL role 中提取结果进行压缩
                        tool_msg = next(
                            (m for m in ctx.packets[1:] if m.message_role == MessageRole.TOOL and m.tool_call_id == tc_id), 
                            None
                        )
                        if tool_msg:
                            actions_to_compress.append(f"我调用了工具{name}，参数：{args}，反馈是：{tool_msg.content}")

                # 如果这个 ASSISTANT 消息里有未闭环的 tool_call，则进行“手术”修改并保留
                if unclosed_tcs_for_this_msg:
                    new_msg = msg.model_copy() # 拷贝原有消息体
                    new_msg.data = copy.deepcopy(msg.data)
                    # 仅保留未闭环的 tool_calls
                    new_msg.data["tool_calls"] = unclosed_tcs_for_this_msg 
                    unclosed_assistant_msgs.append(new_msg)

            # B. 处理系统和异步传递的 EXTRA 消息
            elif msg.message_type == MessageType.EXTRA:
                if msg.sender_id in downstream_ids:
                    # 收到的 communicate_with_upstream (来自下游求助) -> 丢弃
                    continue
                else:
                    # 收到的 communicate_with_downstream (来自上游回复) -> 加入压缩列表
                    actions_to_compress.append(f"收到了上游的补充信息，内容是：{msg.content}")

        # ==========================================
        # 2. 使用大模型进行极致压缩
        # ==========================================
        compressed_summary = ""
        if actions_to_compress:
            actions_str = "\n".join(actions_to_compress)
            system_prompt = '''请将以下执行过程进行压缩。提取出有价值的信息和结果，丢弃无用的过程。
                【核心规则】：如果某个操作的反馈结果证明是无效的、后续不再需要的，则直接丢弃或一笔带过；
                如果后续重试时需要依赖该结果(如已探明的文件路径、报错教训等)，则尽可能保留其关键信息。
                要求格式精简、客观，直接输出压缩后的历史摘要，不要多余解释'''
            
            eval_ctx = self._create_eval_context(ctx, actions_str)
            resp = call_llm(eval_ctx, system_prompt=system_prompt)
            self._record_llm_cost(ctx, resp)
            compressed_summary = resp.content.strip()

        # ==========================================
        # 3. 重新组装 ctx.packets (严格保证 API 结构)
        # ==========================================
        new_packets = [ctx.packets[0]] # 永远保留第一条原始任务意图

        # 1) 插入压缩摘要
        if compressed_summary:
            summary_msg = Message(
                sender_id=ctx.owner_id,       # 替换 trace
                send_type=SendType.SELF,      # 补齐必备字段
                sender=Component.AUDITOR,
                message_role=MessageRole.ASSISTANT,
                content=f"【前置执行轨迹摘要】\n{compressed_summary}"
            )
            new_packets.append(summary_msg)

        # 2) 拼接未闭环的 ASSISTANT 消息，以及它对应的假反馈 TOOL 消息
        if unclosed_assistant_msgs:
            # 以第一条未闭环消息为基底
            merged_msg = unclosed_assistant_msgs[0].model_copy()
            merged_msg.data = copy.deepcopy(merged_msg.data)
            
            all_unclosed_tcs = []
            merged_contents = []
            
            # 汇总所有未闭环的 tool_calls 和文本内容
            for msg in unclosed_assistant_msgs:
                all_unclosed_tcs.extend(msg.data["tool_calls"])
                if msg.content:
                    merged_contents.append(msg.content)
            
            # 覆写合并后的数据
            merged_msg.data["tool_calls"] = all_unclosed_tcs
            merged_msg.content = "\n".join(merged_contents) if merged_contents else ""
            
            # 插入合并后的单一 ASSISTANT 消息
            new_packets.append(merged_msg)
            
            # 紧接着依序插入所有对应的 TOOL 返回消息
            for tc in all_unclosed_tcs:
                tc_id = tc["id"]
                tool_msg = next((m for m in ctx.packets[1:] if m.message_role == MessageRole.TOOL and m.tool_call_id == tc_id), None)
                if tool_msg:
                    new_packets.append(tool_msg)

        if failure_reason:
            # 3) 注入失败原因，作为下一次思考的触发点
            feedback_msg = Message(
                sender_id=ctx.owner_id,       # 替换 trace
                send_type=SendType.SELF,      # 补齐必备字段
                sender=Component.AUDITOR,
                message_role=MessageRole.USER,
                content=f"【系统审计反馈】：上次尝试未成功，原因为：{failure_reason}。请你调整策略重新尝试。"
            )
            new_packets.append(feedback_msg)

        # 覆盖历史完成压缩
        ctx.packets = new_packets

    # --- 暴露给 Worker 的高层接口 ---
    
    def run_finish_audit(self, ctx: Context, skill_content: str, iteration: int) -> Tuple[bool, bool, Context]:
        """场景一：长期迭代任务结束时调用"""
        audit_res = self.audit_task(ctx)
        self.calculate_complexity(ctx)
        
        if audit_res.get("is_passed") and audit_res.get("have_verified"):
            ## 任务通过且自我验证了，直接更新模型能力
            self.update_model(ctx, True, "")
            self._update_attempt(ctx, NodeStatus.COMPLETED, feedback="任务通过且完成自我验证", iteration=iteration)
            return True, True, ctx
        elif audit_res.get("is_passed") and not audit_res.get("have_verified"):
            ## 任务通过但未自我验证，先不更新模型能力分，直接让模型继续迭代，并注入提示让它这次一定要验证交付物
            if len(ctx.packets) > 12:     
                self.compress_context(ctx,failure_reason)
            self._update_attempt(ctx, NodeStatus.RUNNING, feedback="任务未自我验证", add_new=True, iteration=iteration)
            return True, False, ctx
        
        failure = self.analyze_failure(ctx, skill_content)
        failure_reason = failure.get("failure_reason", "未知原因")
        attribution = audit_res.get("attribution", "model")
        
        if attribution == "skill":
            self._update_attempt(ctx, NodeStatus.FAILED, attribution=False, feedback=failure_reason, iteration=iteration)
            return False, False, None
        else:
            self.update_model(ctx, False, failure_reason)
            self.decide_next_model(ctx)

        if len(ctx.packets) > 8:     
            self.compress_context(ctx,failure_reason)

        self._update_attempt(ctx, NodeStatus.RUNNING, attribution=True, feedback=failure_reason, add_new=True, iteration=iteration)
        return False, False, ctx

    def run_timeout_audit(self, ctx: Context, skill_content: str, iteration: int) -> Tuple[bool, Context]:
        """场景二：超出循环次数时调用"""
        failure = self.need_continue(ctx, skill_content)
        attribution = failure.get("attribution", True)        
        self.calculate_complexity(ctx)
        reason = failure.get("reason", "执行超时，可能由于逻辑循环或死胡同")
        if attribution == "skill":
            self._update_attempt(ctx, NodeStatus.FAILED, attribution=False, feedback=reason, iteration=iteration)
            return False, ctx
        else:
            self.decide_next_model(ctx)
            if attribution == "task":
                self._update_attempt(ctx, NodeStatus.RUNNING, attribution=True, feedback="任务过于复杂,需要继续迭代", add_new=True, iteration=iteration)
                return True, ctx
            else:
                self.compress_context(ctx)
        self._update_attempt(ctx, NodeStatus.RUNNING, attribution=True, feedback=reason, add_new=True, iteration=iteration)
        return True, ctx
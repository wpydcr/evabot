import time
from typing import List, Optional, Dict, Any, Tuple
from openai import OpenAI
from openai.types.chat import ChatCompletionMessage

from backend.core.schemas import Message, Context, Component, MessageRole, SendType, Status
from backend.core.log import get_logger, log_event
from backend.llm.llm_config import LLMConfig, ModelConfig, ProviderConfig

logger = get_logger("llm")

def execute_openai_completion(
    p_name: str,
    provider_conf: ProviderConfig,
    model_conf: ModelConfig,
    messages: List[Dict[str, Any]],
    tools: Optional[List[Dict[str, Any]]] = None,
    json_mode: bool = False
) -> Tuple[str, Optional[List[Dict[str, Any]]], Dict[str, int], Optional[str]]:
    """
    底层的大模型 API 调用封装。
    负责初始化 Client、发起请求，并解析出核心字段。
    
    :return: (文本内容, 工具调用数据, usage字典, 思考内容)
    """
    client = OpenAI(
        base_url=provider_conf.base_url,
        api_key=provider_conf.resolved_api_key,
        default_headers=provider_conf.headers
    )
    
    try:
        kwargs = {
            "model": model_conf.id,
            "messages": messages,
        }
        if tools:
            kwargs["tools"] = tools

        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        
        if p_name and p_name.lower() == 'qwen' and not model_conf.reasoning:
            kwargs["extra_body"] = {"enable_thinking": False}
        # print("LLM request kwargs:", kwargs)
        response = client.chat.completions.create(**kwargs)
        # print(response)
        
    except Exception as e:
        log_event(logger, "LLM_ERR", error=str(e), model=model_conf.id)
        raise e

    # -------------------------------------------
    # 解析原生结果
    # -------------------------------------------
    choice = response.choices[0]
    res_msg: ChatCompletionMessage = choice.message
    content_text = res_msg.content or ""
    
    # 获取 DeepSeek 等模型附带的思考过程
    reasoning_content = getattr(res_msg, "reasoning_content", None)
    
    # 提取 Tool Calls
    tool_calls_data = None
    if res_msg.tool_calls:
        tool_calls_data = [
            {
                "id": tc.id,
                "type": tc.type,
                "function": tc.function.model_dump()
            }
            for tc in res_msg.tool_calls
        ]

    # 提取 Token 使用量
    usage = response.usage
    input_tokens = usage.prompt_tokens if usage else 0
    output_tokens = usage.completion_tokens if usage else 0
    usage_dict = {"input": input_tokens, "output": output_tokens}

    return content_text, tool_calls_data, usage_dict, reasoning_content


def call_llm(
    ctx: Context,
    system_prompt: str,
    tools: Optional[List[Dict[str, Any]]] = None,
    model_override: Optional[str] = None,
    json_mode: bool = False
) -> Message:
    """
    :param ctx: 包含完整对话历史的上下文对象 (Context.packets)
    :param system_prompt: 系统提示词 (通常读取自 md 文件)
    :param tools: 工具定义列表
    :return: 包含结果的标准 Message 对象
    """
    start_ts = time.time()
    # -------------------------------------------
    # 1. 准备配置
    # -------------------------------------------
    cfg = LLMConfig.load()
    
    ref = model_override or cfg.defaults.get(ctx.owner)
    if not ref:
        raise ValueError(f"No default model for {ctx.owner}")

    provider_conf, model_conf = cfg.get_model(ref)
    if not provider_conf or not model_conf:
        raise ValueError(f"Model config not found: {ref}")
    
    p_name = cfg.get_provider_name(provider_conf)
    # -------------------------------------------
    # 2. 构建消息历史 (History Construction)
    # -------------------------------------------
    llm_context = []

    # A) 插入 System Prompt
    if system_prompt:
        llm_context.append({"role": "system", "content": system_prompt})

    # B) 遍历 Context 构建历史
    for packet in ctx.packets:
        role = packet.message_role
        content = packet.content or "" 
        if role == MessageRole.TOOL:
            llm_context.append({
                "role": "tool",
                "content": content,
                "tool_call_id": packet.tool_call_id
            })
            
        # 处理包含工具调用的助手回复 (Role: ASSISTANT)
        elif role == MessageRole.ASSISTANT and packet.data and packet.data.get("tool_calls"):
            msg_dict = {
                "role": "assistant",
                "content": content,
                "tool_calls": packet.data.get("tool_calls")
            }
            # 如果之前保存了思考过程，一定要带上，防止 DeepSeek 等严格校验的 API 报错
            if packet.data.get("reasoning_content") is not None:
                msg_dict["reasoning_content"] = packet.data.get("reasoning_content")
                
            llm_context.append(msg_dict)
            
        # 处理普通消息 (User 或普通 Assistant)
        else:
            msg_dict = {
                "role": role.value, 
                "content": content
            }
            if role == MessageRole.ASSISTANT and packet.data and packet.data.get("reasoning_content") is not None:
                msg_dict["reasoning_content"] = packet.data.get("reasoning_content")

            if role == MessageRole.USER and llm_context and llm_context[-1]["role"] == "user":
                llm_context[-1]["content"] += f"\n\n{content}"
            else:
                llm_context.append(msg_dict)
                
            # llm_context.append(msg_dict)

    # -------------------------------------------
    # 3. 触发底层网络调用
    # -------------------------------------------
    if provider_conf.api_type.startswith("openai"):
        content_text, tool_calls_data, usage_dict, reasoning_content = execute_openai_completion(
            p_name=p_name,
            provider_conf=provider_conf,
            model_conf=model_conf,
            messages=llm_context,
            tools=tools,
            json_mode=json_mode
        )
    else:
        raise NotImplementedError(f"Provider type not supported: {provider_conf.api_type}")
    
    # -------------------------------------------
    # 4. 结算与封装业务层 Message
    # -------------------------------------------
    input_tokens = usage_dict["input"]
    output_tokens = usage_dict["output"]

    cost_val = (
        (input_tokens / 1000000.0) * model_conf.cost.input_1m +
        (output_tokens / 1000000.0) * model_conf.cost.output_1m
    )
    if p_name and p_name.lower() == 'qwen':
        if input_tokens >128000:
            cost_val *= 2.8
        elif input_tokens >32000:
            cost_val *= 1.6

    duration_s = round(time.time() - start_ts, 2)

    
    # 构造附加数据
    data_dict = {
        "usage": {"input": input_tokens, "output": output_tokens},
        "cost": round(cost_val, 6),
        "latency_s": duration_s
    }
    if tool_calls_data:
        data_dict["tool_calls"] = tool_calls_data
    if reasoning_content is not None:
        data_dict["reasoning_content"] = reasoning_content

    # 返回消息
    return Message(
        sender_id=ctx.owner_id,          # 替换原来的 trace，使用 ctx 自身的 owner_id
        send_type=SendType.SELF,         # 必填字段，LLM 返回的消息在初次生成时属于同层自我追加
        sender=ctx.owner,
        content=content_text,
        message_role=MessageRole.ASSISTANT,
        data=data_dict,
        status=Status.DONE
    )
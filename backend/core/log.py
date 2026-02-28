import logging
import os
from typing import Any
from logging.handlers import TimedRotatingFileHandler
from .schemas import Message, Component

def get_logger(name: str) -> logging.Logger:
    """统一获取 logger：get_logger(__name__)。"""
    return logging.getLogger(name)

def setup_logging(level: int = logging.INFO) -> None:
    """初始化一次即可：配置控制台输出与统一的本地文件输出"""
    root = logging.getLogger()
    root.setLevel(level)

    logging.getLogger("httpx").setLevel(logging.WARNING)
    
    # 避免重复初始化 handler
    if any(isinstance(h, logging.StreamHandler) for h in root.handlers):
        return
        
    # 统一的日志格式
    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
    )

    # 1. 控制台 Handler (如果需要输出到终端可以放开)
    # console_handler = logging.StreamHandler()
    # console_handler.setFormatter(formatter)
    # root.addHandler(console_handler)

    # 2. 文件 Handler (集中输出到 backend/data/log 目录)
    log_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "../logs"))
    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, "agent_system.log")

    # 使用 TimedRotatingFileHandler，每天午夜自动切分日志
    file_handler = TimedRotatingFileHandler(
        log_file, 
        when="midnight", 
        interval=1, 
        backupCount=30, 
        encoding="utf-8"
    )
    def custom_namer(default_name):
        dir_name, base_name = os.path.split(default_name)
        if ".log." in base_name:
            name_part, date_part = base_name.split(".log.", 1)
            new_name = f"{date_part}_{name_part}.log"
            return os.path.join(dir_name, new_name)
        return default_name
        
    file_handler.namer = custom_namer
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)


def log_message(logger: logging.Logger, msg: Message, level: int = logging.INFO) -> None:
    """打印 Message：根据 send_type 和 sender_id 展现流转链路"""
    c = (msg.content or "").replace("\n", "\\n")
    if len(c) > 200: c = c[:200] + "…"
    
    # 1. 发送方标识 (Butler 唯一不带 ID)
    sender_str = f"{msg.sender.value}"
    if msg.sender != Component.BUTLER and getattr(msg, 'sender_id', None):
        sender_str += f"({msg.sender_id})"
        
    # 2. 接收方/流转动作标识
    action_str = f"[{msg.send_type.value}]"
    if getattr(msg, 'receiver_id', None):
        action_str += f" -> target({msg.receiver_id})"
        
    logger.log(level, f"{sender_str} {action_str} | {msg.message_type} \"{c}\"")


def log_event(logger: logging.Logger, kind: str, *, obj: Any = None,
              content: str | None = None, level: int = logging.INFO, **kv: Any) -> None:
    """兼容工具/其他事件：支持传入 ctx 或 msg 进行自动归属溯源"""
    c = (content or "").replace("\n", "\\n")
    if len(c) > 200: c = c[:200] + "…"
    
    identity = ""
    if obj is not None:
        # 尝试从 Context 或 Message 中提取归属信息
        comp = getattr(obj, 'sender', getattr(obj, 'owner', None))
        oid = getattr(obj, 'sender_id', getattr(obj, 'owner_id', None))
        
        if comp:
            if comp == Component.BUTLER:
                identity = "[butler] "
            else:
                identity = f"[{comp.value}({oid})] " if oid else f"[{comp.value}] "

    tail = " ".join(f"{k}={v}" for k, v in kv.items())
    logger.log(level, f"{identity}{kind} \"{c}\" {tail}".strip())
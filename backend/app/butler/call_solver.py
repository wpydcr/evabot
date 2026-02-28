# backend/app/butler/tools.py
from enum import Enum
from typing import Optional, Any, Dict
from pydantic import BaseModel, Field

class AuthLevel(str, Enum):
    """
    授权级别，决定执行时的自主程度。
    """
    FULL = "full"           # 完全授权：直接执行，不询问。适合低风险或用户明确要求的任务。
    
    SMART = "smart"         # 智能授权（默认）：关键步骤（如删除文件、花钱）需确认，其他自动。
        
    STRICT = "strict"         # 严格模式：每一步操作都需要用户授权。

class SolverTrigger(BaseModel):
    """
    当用户的需要得到的消息超出大模型本身知识边界或能力（如需要未来天气、写代码、操作浏览器、生成文档等）时，调用此工具进行处理。
    """
    intent: str = Field(...,)
    auth_level: AuthLevel = Field(default=AuthLevel.SMART)

def get_solver_tool_schema():
    return {
        "type": "function",
        "function": {
            "name": "call_solver",
            "description": SolverTrigger.__doc__.strip(),
            "parameters": {
                "type": "object",
                "properties": {
                    "intent": {
                        "type": "string",
                        "description": "原汁原味的用户需求意图。严禁脑补技术细节、严禁添加用户未明确提出的格式或约束条件。只允许去除寒暄，如无绝对把握，请直接复制用户的原话！"
                    },
                    "auth_level": {
                        "type": "string",
                        "enum": ["full", "smart", "strict"], 
                        "description": "用户的授权意愿。默认为 smart（关键步骤确认）。完全授权不需要确认则选full；每一步都需要确认则选strict。",
                        "default": "smart"
                    }
                },
                "required": ["intent"]
            }
        }
    }

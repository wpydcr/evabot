"""
utils.py

这里放“极少量”跨模块都会用的工具函数，第一版尽量薄：
1) ID / 时间：统一格式，方便排查日志
2) JSON 序列化：统一 model_dump / model_validate 的用法
3) 日志：标准库 logging + contextvars（可选）
   - 目的是让每行日志自动带上 correlation_id / channel_id / solve_id
   - 不引入三方库，后续需要再替换也很容易
"""

import json
from datetime import datetime, timezone
import os
import re
from typing import List
import uuid
from hashids import Hashids



# ----------------------------
# 轻量工具：ID / 时间
# ----------------------------

def gen_id(prefix: str = "") -> str:
    """
    生成一个字符串 ID，便于日志排查：
    - prefix 为空也可以
    - 默认用 uuid4().hex，长度固定，碰撞概率极低
    """
    core = uuid.uuid4().hex[:12]
    if not prefix:
        return f"{utc_now().strftime('%H%M%S')}_{core}"
    elif prefix == 'sol_':
        return f"{utc_now().strftime('%Y%m%d')}_{prefix}{core}" 
    else:
        return f"{utc_now().strftime('%H%M%S')}_{prefix}{core}" 


def utc_now() -> datetime:
    """
    统一使用 UTC 时间（timezone-aware datetime），避免组件之间时区混乱。
    """
    return datetime.now(timezone.utc)


# ----------------------------
# 轻量工具
# ----------------------------

def extract_json(data: str) -> dict:
    try:
        raw_content = data.strip()
        pattern = r'^```(?:json)?\s*(.*?)\s*```$'
        match = re.search(pattern, raw_content, re.DOTALL | re.IGNORECASE)
        if match:
            raw_content = match.group(1)
        decision = json.loads(raw_content)
        if not isinstance(decision, dict):
            decision = {}
    except json.JSONDecodeError as e:
        decision = {}
    return decision

def load_prompt(dir, filename: str, with_path: bool = True) -> str:
    path = os.path.join(dir, filename)
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
            if with_path:
                content += f"\n\n本文件地址{path}"
            return content
    else:
        return ""
    return ""

def format_artifacts(artifacts: List) -> str:
    """
    将附件列表转化为大模型可读的文本格式
    :param artifacts: List[ArtifactRef]
    :return: 格式化后的字符串，如 "路径: 描述\n路径: 描述"
    """
    if not artifacts:
        return ""
    
    lines = []
    for art in artifacts:
        # 防御性处理，确保没有描述时也有保底文本
        desc = getattr(art, 'description', None) or "无描述"
        uri = getattr(art, 'uri', '未知路径')
        lines.append(f"{uri}: {desc}")
        
    return "\n".join(lines)
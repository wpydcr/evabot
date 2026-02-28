# backend/power/power.py

import os
import re
import yaml
from typing import List, Dict, Any, Set, Optional
from pydantic import BaseModel, Field
from backend.core.log import get_logger, log_event

logger = get_logger("power")

class SkillDef(BaseModel):
    """统一的技能定义模型"""
    name: str
    description: str = ""
    skill_path: str
    # 支持无限级递归子技能挂载
    sub_skills: Dict[str, "SkillDef"] = Field(default_factory=dict) 


class PowerManager:
    """
    能力中枢管理器：
    统一管理 Skills 信息，支持无限极层级的目录结构解析。
    提供给 Solver (Funnel) 及 Worker 使用。
    """
    def __init__(self):
        # 确定本地能力的物理存放路径 (如 backend/power/active)
        self.active_dir = os.path.join(os.path.dirname(__file__), "active")
        self.skills: Dict[str, SkillDef] = {}
        self.reload_all()

    def _load_skill_recursive(self, folder_path: str, default_name: str, frontmatter_pattern: re.Pattern) -> Optional[SkillDef]:
        """递归解析目录，返回带有完整子树的 SkillDef 实例"""
        # 兼容大小写 skill.md 或 SKILL.md
        md_file = os.path.join(folder_path, "skill.md")
        if not os.path.exists(md_file):
            md_file = os.path.join(folder_path, "SKILL.md")
            
        # 如果当前目录不是一个合法的 skill，直接返回 None，中断当前分支
        if not os.path.exists(md_file):
            return None

        try:
            with open(md_file, 'r', encoding='utf-8') as f:
                content = f.read()

            match = frontmatter_pattern.match(content)
            meta = {}
            if match:
                yaml_content = match.group(1)
                meta = yaml.safe_load(yaml_content) or {}
                
            skill = SkillDef(
                name=meta.get("name", default_name),
                description=meta.get("description", ""),
                skill_path=folder_path
            )

            # 递归：扫描当前目录下的子文件夹，将其作为子 skill 挂载
            for sub_item in os.listdir(folder_path):
                sub_folder_path = os.path.join(folder_path, sub_item)
                if os.path.isdir(sub_folder_path):
                    sub_skill = self._load_skill_recursive(sub_folder_path, sub_item, frontmatter_pattern)
                    if sub_skill:
                        skill.sub_skills[sub_skill.name] = sub_skill
                        
            return skill

        except Exception as e:
            log_event(logger, "SKILL_LOAD_FAILED", error=str(e), folder=folder_path, level=40)
            
        return None

    def reload_all(self):
        """支持热更新，重新扫描和读取所有技能配置 (无限级)"""
        self.skills.clear()
        if not os.path.exists(self.active_dir):
            return

        # 匹配 Markdown 文件顶部的 FrontMatter (--- yaml内容 ---)
        frontmatter_pattern = re.compile(r'^---\s*\n(.*?)\n---\s*\n', re.MULTILINE | re.DOTALL)

        # 扫描 active 目录下的各个 skill 文件夹作为顶层节点
        for item in os.listdir(self.active_dir):
            folder_path = os.path.join(self.active_dir, item)
            if not os.path.isdir(folder_path):
                continue
                
            skill = self._load_skill_recursive(folder_path, item, frontmatter_pattern)
            if skill:
                self.skills[skill.name] = skill
        
        # 递归统计一共加载了多少个 skill (用于日志)
        def _count_skills(skills_dict: Dict[str, SkillDef]) -> int:
            count = len(skills_dict)
            for s in skills_dict.values():
                count += _count_skills(s.sub_skills)
            return count
            
    def _strip_frontmatter(self, content: str) -> str:
        """Remove YAML frontmatter from markdown content."""
        if content.startswith("---"):
            match = re.match(r"^---\n.*?\n---\n", content, re.DOTALL)
            if match:
                return content[match.end():].strip()
        return content

    def _find_skill_recursive(self, skills_dict: Dict[str, SkillDef], target_name: str) -> Optional[SkillDef]:
        """DFS 递归在整棵树中寻找指定名称的 skill"""
        # 当前层寻找
        if target_name in skills_dict:
            return skills_dict[target_name]
            
        # 往子节点深挖
        for skill in skills_dict.values():
            found = self._find_skill_recursive(skill.sub_skills, target_name)
            if found:
                return found
                
        return None

    def _find_skill(self, skill_name: str) -> Optional[SkillDef]:
        """查找通用入口"""
        return self._find_skill_recursive(self.skills, skill_name)

    def get_skill_context(self, skill_name: str) -> str:
        """读取指定 Skill (任意层级) 的 Markdown 指令说明，以供大模型或者 Worker 使用"""
        skill = self._find_skill(skill_name)
        if not skill:
            return ""
        
        md_path = os.path.join(skill.skill_path, "skill.md")
        if not os.path.exists(md_path):
            md_path = os.path.join(skill.skill_path, "SKILL.md")
            
        if os.path.exists(md_path):
            with open(md_path, "r", encoding="utf-8") as f:
                return self._strip_frontmatter(f.read())
        return ""
    
    def get_skill_dir(self, skill_name: str) -> str:
        """获取指定 Skill (任意层级) 的物理绝对路径"""
        skill = self._find_skill(skill_name)
        if not skill:
            return ""
        return skill.skill_path
    
    def get_main_skill_xml(self) -> str:
        """获取所有最顶层的主 Skills (XML 格式字符串)"""
        xml_list = ["<skills>"]
        for skill in self.skills.values():
            xml = f'<skill><name>{skill.name}</name><description>{skill.description}</description></skill>'
            xml_list.append(xml)
        xml_list.append("</skills>")
        return "\n".join(xml_list)

    def get_sub_skill_xml(self, parent_skill_name: str) -> str:
        """
        获取指定 Skill 的所有【直属下一级】子 Skills (XML 格式字符串)。
        只要传入的是树中真实存在的节点名称，无论是第几级，都会返回它的子节点。
        """
        parent_skill = self._find_skill(parent_skill_name)
        if not parent_skill or not parent_skill.sub_skills:
            return "<skills></skills>"
            
        xml_list = ["<skills>"]
        for sub_skill in parent_skill.sub_skills.values():
            xml = f'<skill><name>{sub_skill.name}</name><description>{sub_skill.description}</description></skill>'
            xml_list.append(xml)
        xml_list.append("</skills>")
        return "\n".join(xml_list)

# 注意：为了让 Pydantic 兼容自引用(SkillDef类中引用SkillDef)，
# 这里做一次 model_rebuild 以更新 forward references。
SkillDef.model_rebuild()
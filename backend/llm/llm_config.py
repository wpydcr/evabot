import os
import yaml
from typing import List, Dict, Optional, Tuple, Union, Any
from pydantic import BaseModel, Field, PrivateAttr, field_validator
import threading
# 引入核心日志和架构定义
from backend.core.schemas import Component
from backend.core.log import get_logger, log_event

logger = get_logger("llm_config")

class OpResult(BaseModel):
    success: bool
    message: str

    @classmethod
    def ok(cls, msg: str = "Success"):
        return cls(success=True, message=msg)

    @classmethod
    def fail(cls, msg: str):
        return cls(success=False, message=msg)

# ==========================================
# 1. 基础数据模型
# ==========================================

class ModelCost(BaseModel):
    input_1m: float = 0.0
    output_1m: float = 0.0
    cache_read_1m: float = 0.0
    cache_write_1m: float = 0.0

class ModelConfig(BaseModel):
    id: str
    description: str = ""
    enabled: bool = True
    capability_score: float = 0.0
    features: List[str] = ["text"]
    context_window: int = 4096
    max_tokens: int = 4096
    cost: ModelCost = Field(default_factory=ModelCost)
    reasoning: bool = False

class ProviderConfig(BaseModel):
    base_url: str
    api_key: str
    api_type: str = "openai"
    models: List[ModelConfig] = []
    headers: Optional[Dict[str, str]] = Field(default_factory=dict)
    doc_url: Optional[str] = None

    @property
    def resolved_api_key(self) -> str:
        value = self.api_key.strip()
        if value.startswith("ENV:"):
            try:
                env_var_name = value.split(":", 1)[1].strip()
                return os.environ.get(env_var_name, "")
            except IndexError:
                return ""
        return value

# ==========================================
# 2. 配置管理器 (核心)
# ==========================================
DEFAULT_CONFIG_PATH = os.path.join(os.path.dirname(__file__), "llm.yaml")

class LLMConfig(BaseModel):
    defaults: Dict[Component, str] = Field(default_factory=lambda: {
        Component.BUTLER: "",
        Component.SOLVER: "",
        Component.WORKER: "",
        Component.AUDITOR: ""
    })

    providers: Dict[str, ProviderConfig] = Field(default_factory=dict)


    # 内部状态
    _model_map: Dict[str, Tuple[ProviderConfig, ModelConfig]] = PrivateAttr(default_factory=dict)
    _ALLOWED_DEFAULTS = {Component.BUTLER, Component.SOLVER, Component.WORKER}     
    _lock: threading.RLock = PrivateAttr(default_factory=threading.RLock)    

    def initialize_registry(self):
        """
        根据当前的 providers 数据，重新构建内存中的 _model_map 索引
        """
        with self._lock:
            self._model_map.clear()
            for p_name, p_conf in self.providers.items():
                for model in p_conf.models:
                    self._model_map[f"{p_name}/{model.id}"] = (p_conf, model)

    @classmethod
    def get_default_path(cls):
        return os.path.join(os.path.dirname(__file__), "llm.yaml")
    
    @classmethod
    def load(cls) -> "LLMConfig":
        """
        加载配置
        """
        path = DEFAULT_CONFIG_PATH
        
        # --- 情况 A: 文件不存在 ---
        if not os.path.exists(path):
            # 实例化时，model_post_init 会自动运行，索引已建立（虽然是空的）
            inst = cls()
            inst.initialize_registry()
            inst.save()
            return inst

        # --- 情况 B: 文件存在 ---
        try:
            with open(path, 'r', encoding='utf-8') as f:
                data = yaml.safe_load(f) or {}
            
            # 实例化 -> 自动触发 model_post_init -> 自动构建 _model_map
            inst = cls(**data) 
            inst.initialize_registry()
            return inst
            
        except Exception as e:
            log_event(logger, "CONFIG_LOAD_ERROR", error=str(e))
            raise e
        
    def get_provider_name(self, provider: ProviderConfig) -> str | None:
        for name, p in self.providers.items():
            if p is provider:
                return name
        return None

    # --- 统一的保存函数，返回 OpResult ---
    def save(self) -> OpResult:
        with self._lock:
            try:
                data = self.model_dump(mode='json', exclude_none=True)
                with open(DEFAULT_CONFIG_PATH, 'w', encoding='utf-8') as f:
                    yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False, indent=2)
                self.initialize_registry()
                return OpResult.ok("Saved")
            except Exception as e:
                log_event(logger, "SAVE_ERROR", error=str(e))
                return OpResult.fail(f"Save failed: {str(e)}")


    # ==========================================
    # 增删改查
    # ==========================================

    def get_model(self, ref: str):
        return self._model_map.get(ref)

    def upsert_provider(self, name: str, config: ProviderConfig) -> OpResult:
        with self._lock:
            try:
                action = "updated" if name in self.providers else "created"
                self.providers[name] = config
                # 尝试保存
                res = self.save()
                if not res.success: return res # 保存失败直接返回错误
                log_event(logger, "PROVIDER_UPSERT", name=name, action=action)
                return OpResult.ok(f"Provider '{name}' {action} successfully")
                
            except Exception as e:
                log_event(logger, "PROVIDER_UPSERT_ERROR", error=str(e))
                return OpResult.fail(f"Error: {str(e)}")

    def delete_provider(self, name: str) -> OpResult:
        with self._lock:
            try:
                if name not in self.providers:
                    return OpResult.fail(f"Provider '{name}' not found")

                del self.providers[name]
                
                res = self.save()
                if not res.success: return res

                log_event(logger, "PROVIDER_DELETE", name=name)
                return OpResult.ok(f"Provider '{name}' deleted")
            except Exception as e:
                return OpResult.fail(str(e))

    def upsert_model(self, provider_name: str, model_config: ModelConfig) -> OpResult:
        with self._lock:
            try:
                if provider_name not in self.providers:
                    return OpResult.fail(f"Provider '{provider_name}' not found")
                
                provider = self.providers[provider_name]
                # 查找索引
                idx = next((i for i, m in enumerate(provider.models) if m.id == model_config.id), -1)
                
                if idx >= 0:
                    provider.models[idx] = model_config
                    action = "updated"
                else:
                    provider.models.append(model_config)
                    action = "created"
                                
                res = self.save()
                if not res.success: return res

                log_event(logger, "MODEL_UPSERT", provider=provider_name, model=model_config.id)
                return OpResult.ok(f"Model '{model_config.id}' {action}")

            except Exception as e:
                log_event(logger, "MODEL_UPSERT_ERROR", error=str(e))
                return OpResult.fail(str(e))

    def delete_model(self, provider_name: str, model_id: str) -> OpResult:
        with self._lock:
            try:
                if provider_name not in self.providers:
                    return OpResult.fail(f"Provider '{provider_name}' not found")

                provider = self.providers[provider_name]
                # 过滤列表
                new_models = [m for m in provider.models if m.id != model_id]
                
                if len(new_models) == len(provider.models):
                    return OpResult.fail(f"Model '{model_id}' not found")
                
                provider.models = new_models
                
                res = self.save()
                if not res.success: return res

                log_event(logger, "MODEL_DELETE", provider=provider_name, model=model_id)
                return OpResult.ok(f"Model '{model_id}' deleted")

            except Exception as e:
                return OpResult.fail(str(e))

    def update_default(self, role: Union[Component, str], model_ref: str) -> OpResult:
        with self._lock:
            try:
                # 转换和校验
                if isinstance(role, str):
                    try: role_enum = Component(role.lower())
                    except: return OpResult.fail(f"Invalid role: {role}")
                else:
                    role_enum = role

                if role_enum not in self._ALLOWED_DEFAULTS:
                    return OpResult.fail(f"Role {role_enum} not allowed")

                self.defaults[role_enum] = model_ref
                
                res = self.save()
                if not res.success: return res

                warn = ""
                if model_ref not in self._model_map:
                    warn = " (Warning: Model not found)"
                    log_event(logger, "DEFAULT_WARN", content=warn)

                log_event(logger, "DEFAULT_UPDATE", role=role_enum.value, new=model_ref)
                return OpResult.ok(f"Default updated{warn}")

            except Exception as e:
                return OpResult.fail(str(e))
            
    def get_model_menu(self,score=0) -> str:
        """
        生成供 LLM 选择的模型菜单。
        """
        lines = []
        with self._lock:
            for key, (p_conf, m_conf) in self._model_map.items():
                if not m_conf.enabled:
                    continue

                if m_conf.capability_score < score:
                    continue

                price_tag = m_conf.cost.input_1m+m_conf.cost.output_1m
                
                line = (
                    f"- Model_id: {key}\n"
                    f"  Description: {m_conf.description}\n"
                    f"  Capabilities: Score {m_conf.capability_score}, Features: {m_conf.features}\n"
                    f"  Cost: ${price_tag}/1M tokens"
                )
                lines.append(line)
        if not lines:
            return ""
        return "\n".join(lines)
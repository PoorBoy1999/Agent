"""
基础工具接口定义
所有工具都应继承 BaseTool 并实现 execute 方法
"""
from abc import ABC, abstractmethod
from typing import Type
from pydantic import BaseModel


class BaseTool(ABC):
    """工具基类"""
    
    @property
    @abstractmethod
    def name(self) -> str:
        """工具名称"""
        pass
    
    @property
    @abstractmethod
    def description(self) -> str:
        """工具描述，用于 LLM 理解何时使用"""
        pass
    
    @property
    @abstractmethod
    def input_schema(self) -> Type[BaseModel]:
        """输入参数 Pydantic 模型"""
        pass
    
    @abstractmethod
    def execute(self, **kwargs) -> dict:
        """执行工具逻辑"""
        pass

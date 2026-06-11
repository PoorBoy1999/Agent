"""
Pydantic 模型 - LangGraph 状态定义
"""
from typing import TypedDict, Optional


class AgentState(TypedDict):
    """LangGraph 使用的状态字典"""
    messages: list  # 对话历史
    tool_result: Optional[dict]  # 工具调用结果
    current_tool: Optional[str]  # 当前正在调用的工具名
    pending_tool_calls: Optional[list]  # 待执行的工具调用

"""
复杂规划 Agent - 支持 DAG 工作流的 LangGraph 实现

架构设计：
1. Planner 节点 - 分析用户请求，生成执行计划 DAG
2. Executor 节点 - 按依赖顺序执行工具
3. Conditional Edge - 处理工具间的数据依赖和循环检测
4. 工作记忆系统 - 管理会话内的消息历史，支持上下文累积

工作流程示例（用户："找出 Alice 的邮件，如果有紧急的，就在周三下午找个时间创建待办"）：
┌─────────────┐
│   Planner   │ 分析请求 → 生成 DAG: [邮件查询] → [紧急判断] → [日历查询] → [创建待办]
└─────────────┘
       │
       ▼
┌─────────────┐
│   Executor  │ 执行工具链，传递依赖数据
└─────────────┘
       │
       ▼
   [继续/完成]

工作记忆系统：
┌────────────────────────────────────────────────────────────┐
│  用户输入 → 工作记忆 → LLM → 响应 → 工作记忆更新            │
│                          ↑                                 │
│                     包含历史上下文                          │
└────────────────────────────────────────────────────────────┘
"""

from typing import TypedDict, Optional, Any, Literal, List, Dict
from dataclasses import dataclass, field
from enum import Enum
from langgraph.graph import StateGraph, END
from langchain_core.messages import HumanMessage, AIMessage, ToolMessage, SystemMessage
from pydantic import BaseModel, Field
import json
from datetime import datetime, timedelta
from collections import deque
from copy import deepcopy

from .llm_config import llm as _original_llm, get_llm

# 为了向后兼容，保留原来的导入方式
# 但实际调用时使用 get_llm() 来获取动态选择的 LLM
llm = _original_llm  # 占位符，实际调用时用 get_llm()


# =============================================================================
# 规划状态定义
# =============================================================================

class TaskStatus(str, Enum):
    """任务状态枚举"""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"
    WAITING_CONFIRMATION = "waiting_confirmation"
    NEEDS_RETRY = "needs_retry"  # 需要重试
    RETRY_FAILED = "retry_failed"  # 重试后仍然失败


class TaskType(str, Enum):
    """任务类型枚举"""
    READ = "read"
    WRITE = "write"
    CONDITION = "condition"
    SYNTHESIZE = "synthesize"
    LLM_JUDGE = "llm_judge"  # LLM 判断任务
    REFLECT = "reflect"  # 自我反思任务


@dataclass
class TaskNode:
    """DAG 中的任务节点"""
    task_id: str
    task_type: TaskType
    tool_name: str
    params: dict = field(default_factory=dict)
    depends_on: list[str] = field(default_factory=list)
    result: Optional[dict] = None
    status: TaskStatus = TaskStatus.PENDING
    condition_result: Optional[bool] = None
    description: str = ""
    
    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "task_type": self.task_type.value,
            "tool_name": self.tool_name,
            "params": self.params,
            "depends_on": self.depends_on,
            "status": self.status.value,
            "description": self.description,
            "result": self.result,
            "condition_result": self.condition_result
        }


class ExecutionPlan:
    """执行计划 - 包含 DAG 和执行状态"""
    def __init__(self):
        self.task_dag: dict[str, TaskNode] = {}
        self.root_tasks: list[str] = []
        self.pending_confirmation: Optional[dict] = None
        self.completed_count: int = 0
        self.total_count: int = 0
        self.execution_order: list[str] = []
        self.intermediate_results: dict = {}
        self.step_count: int = 0  # 防止无限循环
    
    def get_next_runnable_tasks(self) -> list[str]:
        """获取可以运行的任务（依赖都已完成或失败）

        支持的任务状态：
        - PENDING: 等待执行
        - NEEDS_RETRY: 需要重试的任务
        """
        runnable = []
        for task_id, node in self.task_dag.items():
            # 跳过已完成、失败、等待中的任务
            if node.status not in (TaskStatus.PENDING, TaskStatus.NEEDS_RETRY):
                continue

            # 检查依赖：如果所有依赖都已完成（包括失败），则可以运行
            # 注意：这里允许依赖失败的任务继续运行，因为日历查询失败不影响创建事件
            # 只有当依赖是 PENDING 或 WAITING_CONFIRMATION 时才阻塞
            blocking_deps = [
                dep_id for dep_id in node.depends_on
                if self.task_dag.get(dep_id, TaskNode(task_id="", task_type=TaskType.READ, tool_name="")).status
                in (TaskStatus.PENDING, TaskStatus.WAITING_CONFIRMATION)
            ]
            if not blocking_deps:
                runnable.append(task_id)

        # 优先处理需要重试的任务
        retry_tasks = [t for t in runnable if "_retry_" in t]
        pending_tasks = [t for t in runnable if "_retry_" not in t]

        return retry_tasks + pending_tasks
        return runnable
    
    def is_complete(self) -> bool:
        """检查计划是否完成"""
        if self.pending_confirmation:
            return False
        pending_tasks = [
            t for t in self.task_dag.values() 
            if t.status in (TaskStatus.PENDING, TaskStatus.RUNNING, TaskStatus.WAITING_CONFIRMATION)
        ]
        return len(pending_tasks) == 0 or self.step_count > 20  # 最多 20 步


class PlanningState(TypedDict):
    """规划 Agent 的状态"""
    messages: list
    user_input: str
    execution_plan: Optional[dict]
    current_task_id: Optional[str]
    tool_result: Optional[dict]
    response_text: Optional[str]
    pending_confirmation: Optional[dict]
    needs_confirmation: bool
    plan_description: Optional[str]
    intermediate_results: dict
    error: Optional[str]
    step_count: int  # 当前执行步骤
    cached_tool_results: dict  # 跨请求的工具结果缓存
    # 自我反思相关状态
    reflection_enabled: bool  # 是否启用反思机制
    reflection_result: Optional[dict]  # 反思结果
    retry_count: int  # 当前任务的重试次数
    max_retries: int  # 最大重试次数
    reflection_history: list  # 反思历史记录
    failed_tasks_needing_review: list  # 需要审查的失败任务
    # 工作记忆相关状态
    working_memory: Optional[dict]  # 工作记忆序列化数据
    session_id: Optional[str]  # 会话ID


# =============================================================================
# 工作记忆系统 (Working Memory)
# 生命周期：单个对话会话
# 存储内容：当前对话的所有消息
# =============================================================================

# 工作记忆配置
WORKING_MEMORY_CONFIG = {
    "max_messages": 50,  # 最大保留消息数
    "max_token_estimate": 8000,  # 预估最大 token 数
    "summary_threshold": 30,  # 超过此消息数后启用摘要
    "system_prompt_reserve": 2000,  # 保留给系统提示的空间（字符）
}


@dataclass
class WorkingMemoryMessage:
    """工作记忆中的单条消息"""
    role: str  # "user" / "assistant" / "system" / "tool"
    content: str
    timestamp: str = ""
    metadata: dict = field(default_factory=dict)  # 额外元数据

    def to_dict(self) -> dict:
        return {
            "role": self.role,
            "content": self.content,
            "timestamp": self.timestamp,
            "metadata": self.metadata
        }

    @classmethod
    def from_dict(cls, data: dict) -> "WorkingMemoryMessage":
        return cls(
            role=data.get("role", "user"),
            content=data.get("content", ""),
            timestamp=data.get("timestamp", ""),
            metadata=data.get("metadata", {})
        )


class WorkingMemory:
    """
    工作记忆管理器

    特性：
    1. 消息累积 - 保存会话中的所有消息
    2. 容量控制 - 超过上限时自动压缩
    3. 上下文检索 - 支持按关键词检索历史消息
    4. 摘要生成 - 支持生成对话摘要

    使用方式：
    memory = WorkingMemory()
    memory.add_user_message("帮我创建日程")
    memory.add_assistant_message("好的，请告诉我日程详情")
    messages = memory.get_messages_for_llm()  # 获取格式化的消息列表
    """

    def __init__(
        self,
        max_messages: int = None,
        max_token_estimate: int = None
    ):
        self._messages: deque = deque(maxlen=max_messages or WORKING_MEMORY_CONFIG["max_messages"])
        self._max_token_estimate = max_token_estimate or WORKING_MEMORY_CONFIG["max_token_estimate"]
        self._conversation_summary: str = ""  # 对话摘要（压缩后）
        self._last_summarized_at: int = 0  # 最后摘要时的消息数

    def add_user_message(self, content: str, metadata: dict = None) -> WorkingMemoryMessage:
        """添加用户消息"""
        msg = WorkingMemoryMessage(
            role="user",
            content=content,
            timestamp=datetime.now().isoformat(),
            metadata=metadata or {}
        )
        self._messages.append(msg)
        return msg

    def add_assistant_message(self, content: str, metadata: dict = None) -> WorkingMemoryMessage:
        """添加助手消息"""
        msg = WorkingMemoryMessage(
            role="assistant",
            content=content,
            timestamp=datetime.now().isoformat(),
            metadata=metadata or {}
        )
        self._messages.append(msg)
        return msg

    def add_tool_message(self, content: str, tool_name: str = "", metadata: dict = None) -> WorkingMemoryMessage:
        """添加工具结果消息"""
        msg = WorkingMemoryMessage(
            role="tool",
            content=content,
            timestamp=datetime.now().isoformat(),
            metadata={
                **(metadata or {}),
                "tool_name": tool_name
            }
        )
        self._messages.append(msg)
        return msg

    def add_system_message(self, content: str, metadata: dict = None) -> WorkingMemoryMessage:
        """添加系统消息"""
        msg = WorkingMemoryMessage(
            role="system",
            content=content,
            timestamp=datetime.now().isoformat(),
            metadata=metadata or {}
        )
        self._messages.append(msg)
        return msg

    def get_all_messages(self) -> List[WorkingMemoryMessage]:
        """获取所有消息"""
        return list(self._messages)

    def get_messages_for_llm(self, include_summary: bool = True) -> List[dict]:
        """
        获取格式化后的消息列表，用于传给 LLM

        格式：
        - 如果有摘要，在开头添加系统消息
        - 按时间顺序返回消息
        """
        result = []

        # 如果有摘要且启用了摘要模式，添加到开头
        if include_summary and self._conversation_summary:
            result.append({
                "role": "system",
                "content": f"【对话摘要】以下是之前对话的摘要，帮助你理解上下文：\n{self._conversation_summary}"
            })

        # 添加所有消息
        for msg in self._messages:
            result.append(msg.to_dict())

        return result

    def get_conversation_history_text(self, max_messages: int = None) -> str:
        """
        获取对话历史文本（用于检索或调试）

        Args:
            max_messages: 最多返回多少条消息，默认返回所有
        """
        messages = list(self._messages)[-max_messages:] if max_messages else list(self._messages)

        if not messages:
            return "（暂无对话历史）"

        parts = []
        for msg in messages:
            role_display = {"user": "用户", "assistant": "助手", "tool": "工具", "system": "系统"}.get(msg.role, msg.role)
            # 截断长内容
            content = msg.content[:200] + "..." if len(msg.content) > 200 else msg.content
            parts.append(f"{role_display}：{content}")

        return "\n".join(parts)

    def search_messages(self, keyword: str, case_sensitive: bool = False) -> List[WorkingMemoryMessage]:
        """
        搜索包含关键词的消息

        Args:
            keyword: 搜索关键词
            case_sensitive: 是否区分大小写

        Returns:
            匹配的消息列表
        """
        results = []
        search_in = str.lower if not case_sensitive else lambda x: x

        for msg in self._messages:
            if search_in(keyword) in search_in(msg.content):
                results.append(msg)

        return results

    def get_context_for_current_task(self, current_task: str = None) -> str:
        """
        获取与当前任务相关的上下文

        Args:
            current_task: 当前任务描述

        Returns:
            相关上下文文本
        """
        if not self._messages:
            return ""

        # 获取最近的消息作为上下文
        recent = list(self._messages)[-10:]

        parts = ["【最近对话上下文】"]
        for msg in recent:
            role_display = {"user": "用户", "assistant": "助手", "tool": "工具"}.get(msg.role, msg.role)
            content = msg.content[:300] + "..." if len(msg.content) > 300 else msg.content
            parts.append(f"{role_display}：{content}")

        return "\n".join(parts)

    def should_summarize(self) -> bool:
        """检查是否需要生成摘要"""
        threshold = WORKING_MEMORY_CONFIG["summary_threshold"]
        return len(self._messages) - self._last_summarized_at >= threshold

    def set_summary(self, summary: str):
        """设置对话摘要"""
        self._conversation_summary = summary
        self._last_summarized_at = len(self._messages)

    def get_summary(self) -> str:
        """获取对话摘要"""
        return self._conversation_summary

    def clear(self):
        """清空工作记忆"""
        self._messages.clear()
        self._conversation_summary = ""
        self._last_summarized_at = 0

    def __len__(self) -> int:
        """获取消息数量"""
        return len(self._messages)

    def get_stats(self) -> dict:
        """获取记忆统计信息"""
        roles = {}
        for msg in self._messages:
            roles[msg.role] = roles.get(msg.role, 0) + 1

        return {
            "total_messages": len(self._messages),
            "role_distribution": roles,
            "has_summary": bool(self._conversation_summary),
            "summary_length": len(self._conversation_summary) if self._conversation_summary else 0,
            "estimated_tokens": len(" ".join([m.content for m in self._messages])) // 4  # 简单估算
        }


# =============================================================================
# 工作记忆状态转换函数
# =============================================================================

def create_working_memory() -> WorkingMemory:
    """创建新的工作记忆实例"""
    return WorkingMemory()


def add_message_to_memory(
    memory: WorkingMemory,
    role: str,
    content: str,
    metadata: dict = None
) -> WorkingMemory:
    """
    向工作记忆添加消息（不可变更新）

    Returns:
        新的 WorkingMemory 实例（因为 deque 是不可变的）
    """
    new_memory = WorkingMemory()
    new_memory._messages = memory._messages.copy()
    new_memory._conversation_summary = memory._conversation_summary
    new_memory._last_summarized_at = memory._last_summarized_at

    if role == "user":
        new_memory.add_user_message(content, metadata)
    elif role == "assistant":
        new_memory.add_assistant_message(content, metadata)
    elif role == "tool":
        new_memory.add_tool_message(content, metadata.get("tool_name") if metadata else "", metadata)
    elif role == "system":
        new_memory.add_system_message(content, metadata)

    return new_memory


# =============================================================================
# Planner 节点 - 生成执行计划
# =============================================================================

PLANNER_PROMPT_TEMPLATE = """你是一个任务规划专家。将用户请求分解为可执行步骤。

可用工具及参数：
1. read_email - 读取邮件
   参数: limit(默认20), unread_only(默认false), subject_contains(""), from_contains(""), mailbox(""), date_since("")
   日期格式: DD-MMM-YYYY (如 18-May-2026)
   - 当用户提到时间范围如"最近一周"、"最近一个月"、"今天"、"昨天"时，必须根据【系统提供的当前日期】计算具体日期
   - 例如：当前日期是 {current_date}，则"下周"应计算为下周的周一到周日

2. list_outlook_calendar_events - 列出日历
   参数: days(天数，默认30，最大31), timezone(时区如"Asia/Shanghai"), start_date(日期YYYY-MM-DD，为空表示今天)
   - 注意："下周"需要根据当前日期动态计算，不要硬编码日期

3. create_todo - 创建待办
   参数: content(内容), due_date(日期YYYY-MM-DD), due_time(时间HH:MM), priority(low/normal/high/urgent)

4. create_outlook_calendar_event - 创建日历事件
   参数: subject(标题), start(开始时间), end(结束时间), location(地点), all_day(是否全天,默认false), body(备注)
   注意: start/end 格式为 YYYY-MM-DD HH:MM:SS 或 YYYY-MM-DD
   - start 和 end 为必填参数

5. write_file - 写入文件
   参数: file_path(路径), content(内容)

6. write_email_draft - 创建 Outlook 邮件草稿
   参数: to(收件人邮箱), subject(邮件主题), body(邮件正文), cc(抄送), bcc(密送)
   注意: 只创建草稿，不发送邮件。需要在 Outlook 中手动发送。

7. browser_fetch - 访问网页并提取内容 ★★★ 重要：用于访问网址、总结网页内容 ★★★
   参数: url(完整URL地址), extract_content(默认true), max_content_length(默认50000)
   适用场景：
   - 用户发送链接并要求了解内容时使用
   - 用户指定了具体网址时使用
   - ⚠️ 如果用户没有提供具体URL，【禁止】自己生成URL，必须先使用 web_search 工具搜索真实URL

8. web_search - 网络搜索工具 ★★★ 用于搜索信息、查找文章 ★★★
   参数: search_term(搜索关键词)
   适用场景：
   - 用户要求"找"、"搜索"某篇文章或信息时
   - 用户没有提供具体URL，需要自己查找时
   - 这是获取真实URL的最佳方式
   使用方式：
   1. 先调用 web_search 获取相关网页链接
   2. 从搜索结果中选择合适的URL
   3. 再调用 browser_fetch 访问该URL

9. weather_query - 天气查询工具 ★★★ 专门用于查询天气预报 ★★★
   参数: city(城市名称,【必填】), forecast_days(预报天数1-3，默认3), date(指定日期)
   适用场景：
   - 用户询问"天气怎么样"、"今天/明天/后天天气"
   - 用户询问特定城市的天气预报
   - 用户询问"要不要带伞"、"会不会下雨"等与天气相关的问题
   使用示例：
   - 查询北京今天天气: {{"city": "北京", "forecast_days": 1}}
   - 查询上海明天天气: {{"city": "上海", "forecast_days": 1, "date": "明天"}}
   - 查询北京未来三天: {{"city": "北京", "forecast_days": 3}}
   ⚠️ 【重要】查询天气时请优先使用 weather_query 工具，不要使用 browser_fetch 或 web_search！
   ⚠️ 【重要】city 是必填参数！如果用户没有指定城市，你【必须】在 description 中说明"请提供要查询天气的城市名称"，而不是生成计划！

重要规则：
1. 只使用上述列出的参数，不要发明新参数如 date_range、days_range 等
2. 当需要 LLM 进行语义判断时，使用 "llm_judge" 任务类型
3. 当 llm_judge 判断结果为真需要执行写操作时，创建一个独立的 write 类型任务
4. synthesize 只能生成文本回答，禁止调用写操作工具
5. 如果用户请求涉及网页内容，必须使用 browser_fetch 工具
6. ⚠️ 如果用户说"找文章"、"搜索XX最佳实践"但没有提供URL，必须先调用 web_search 搜索，再访问找到的URL
7. ⚠️ 【重要】时间相关参数（如 start_date、date_since、"下周"等）必须根据系统提供的当前日期动态计算，不要硬编码日期！
8. ⚠️ 【特别重要】当用户询问天气时，必须使用 weather_query 工具！
9. ⚠️ 【重要】如果任务的必填参数（如 city）缺失，必须在 description 中说明需要用户提供什么信息，不要生成不完整的计划！
10. ⚠️ 【重要】引用其他任务结果时，必须使用双大括号格式 `{{task_id.field}}`！
    - weather_query 任务的结果字段包括：`weather_data`（完整天气数据）、`summary`（简洁摘要）、`city`（城市名）、`forecast_type`（预报类型）
    - 错误示例：`{{weather_summary}}` ❌
    - 正确示例：`{{task_1.weather_data}}` 或 `{{task_1.summary}}` ✅

【日历操作特别规则 - 非常重要】
⚠️ 当用户提到"修改"、"改到"、"更新"、"调整"、"取消"某个已创建的日程时：
   1. 【必须】先调用 list_outlook_calendar_events 查找该日程，获取其 entry_id
   2. 使用 update_outlook_calendar_event 工具（参数包含 entry_id）来修改，而不是 create
   3. 禁止直接创建新日程！创建只用于"新的"、"添加"、"再创建一个"等明确表示新增的场景
   
   示例：
   - "把那个会议改到下午3点" → 先 list 查找，再用 update 修改
   - "把修车日程改到八点" → 先 list 查找，再用 update 修改
   - "取消下周的会议" → 先 list 查找，再用 update 修改
   - "再创建一个新会议" → 使用 create

输出 JSON 格式：
{{
    "plan_description": "计划描述",
    "tasks": [
        {{
            "task_id": "task_1",
            "task_type": "read",
            "tool_name": "read_email",
            "params": {{"limit": 50, "date_since": "<根据当前日期计算的日期>"}},
            "depends_on": [],
            "description": "读取最近一周的邮件"
        }},
        {{
            "task_id": "task_2",
            "task_type": "llm_judge",
            "params": {{
                "source_task": "task_1",
                "question": "这些邮件中是否有提及Spotify的内容？请分析主题和正文。",
                "result_key": "has_spotify"
            }},
            "depends_on": ["task_1"],
            "description": "判断是否有Spotify邮件"
        }}
    ]
}}

只输出 JSON，不要其他内容。"""


def _get_dynamic_prompt() -> str:
    """动态生成包含当前日期的 prompt"""
    now = datetime.now()
    # 格式化为 "2026-05-27" 格式
    current_date_iso = now.strftime("%Y-%m-%d")
    
    # 计算下周日期范围（下一个周一到周日）
    # 获取当前是周几（0=周一，6=周日）
    weekday = now.weekday()
    # 下周一是：当前周的周一 + 7 天
    # 当前周的周一是 now - weekday 天
    current_week_monday = now - timedelta(days=weekday)
    # 下周一是当前周周一之后的7天
    next_monday = current_week_monday + timedelta(days=7)
    next_sunday = next_monday + timedelta(days=6)
    
    # 计算"最近一周"的起始日期
    week_ago = now - timedelta(days=7)
    
    prompt = PLANNER_PROMPT_TEMPLATE.format(
        current_date=current_date_iso
    )
    
    # 在 prompt 末尾添加当前日期信息
    date_info = f"""
========================================
【系统当前日期信息】
今天是：{current_date_iso} (星期{"一二三四五六日"[weekday]})
"本周"：从 {current_week_monday.strftime('%Y-%m-%d')} 到 {(current_week_monday + timedelta(days=6)).strftime('%Y-%m-%d')}
"最近一周"：从 {week_ago.strftime('%Y-%m-%d')} 到 {current_date_iso}
"下周"：从 {next_monday.strftime('%Y-%m-%d')} 到 {next_sunday.strftime('%Y-%m-%d')}
========================================
"""
    
    return prompt + date_info


# 保留旧名称以保持向后兼容
PLANNER_PROMPT = _get_dynamic_prompt()


def planner_node(state: PlanningState) -> PlanningState:
    """Planner 节点 - 分析请求，生成计划

    使用工作记忆系统来维护对话上下文
    """
    user_input = state["user_input"]
    messages = state.get("messages", [])

    print("\n" + "=" * 60)
    print("[PLANNER] Analyzing request, generating plan")
    print("=" * 60)
    print(f"User request: {user_input}")

    # ========== [工作记忆] 获取历史上下文 ==========
    working_memory_data = state.get("working_memory")
    history_context = ""

    if working_memory_data:
        # 从序列化数据恢复工作记忆
        history_messages = working_memory_data.get("messages", [])
        if history_messages:
            # 构建历史上下文摘要
            recent_msgs = history_messages[-6:]  # 最近6条消息
            history_parts = []
            for msg in recent_msgs:
                role = msg.get("role", "unknown")
                content = msg.get("content", "")
                # 截断长内容
                if len(content) > 200:
                    content = content[:200] + "..."
                role_display = {"user": "用户", "assistant": "助手", "system": "系统"}.get(role, role)
                history_parts.append(f"{role_display}: {content}")

            history_context = "\n\n【对话历史】\n" + "\n".join(history_parts)
            print(f"[WORKING MEMORY] 使用 {len(history_messages)} 条历史消息作为上下文")

    # 动态获取包含当前日期的 prompt
    dynamic_prompt = _get_dynamic_prompt()

    # 将历史上下文添加到 prompt 中
    if history_context:
        dynamic_prompt += f"\n\n{history_context}"

    formatted_messages = [SystemMessage(content=dynamic_prompt)]
    formatted_messages.append(HumanMessage(content=f"User request: {user_input}\n\nGenerate execution plan."))

    try:
        # 使用动态选择的 LLM
        active_llm = get_llm()
        print(f"[PLANNER] 使用 LLM: {active_llm.model_name if hasattr(active_llm, 'model_name') else 'default'}")
        response = active_llm.invoke(formatted_messages)
        response_content = response.content if hasattr(response, 'content') else str(response)

        # ========== [DEBUG] Planner LLM 响应调试 ==========
        print("\n" + "=" * 60)
        print("[DEBUG] Planner LLM 原始响应:")
        print("=" * 60)
        print(f"响应类型: {type(response_content)}")
        print(f"响应长度: {len(response_content)} 字符")
        print(f"响应内容预览 (前500字符):")
        print(response_content[:500] if len(response_content) > 500 else response_content)
        print("=" * 60)
        # ========== [DEBUG] 结束 ==========

        plan_data = _parse_plan_response(response_content)

        if not plan_data:
            plan_data = {
                "plan_description": "Simple plan",
                "tasks": [{
                    "task_id": "task_1",
                    "task_type": "read",
                    "tool_name": "list_todo",
                    "params": {"status": "all"},
                    "depends_on": [],
                    "description": "List todos"
                }]
            }

        plan = _build_execution_plan(plan_data)

        print(f"\n[PLAN GENERATED]")
        print(f"- Tasks: {len(plan.task_dag)}")
        print(f"- Order: {plan.execution_order}")

        # ========== [工作记忆] 更新消息历史 ==========
        updated_messages = messages + [{"role": "user", "content": user_input}]

        # 构建新的工作记忆数据
        new_working_memory = {
            "messages": updated_messages,
            "session_id": state.get("session_id", ""),
            "updated_at": datetime.now().isoformat()
        }

        return {
            "messages": updated_messages,
            "working_memory": new_working_memory,
            "user_input": user_input,
            "execution_plan": _serialize_plan(plan),
            "current_task_id": None,
            "tool_result": None,
            "response_text": None,
            "pending_confirmation": None,
            "needs_confirmation": False,
            "plan_description": plan_data.get("plan_description"),
            "intermediate_results": {},
            "error": None,
            "step_count": 0
        }

    except Exception as e:
        print(f"[ERROR] Planner failed: {e}")
        return {
            **state,
            "error": f"Planning failed: {str(e)}",
            "response_text": f"Sorry, I couldn't generate a plan: {str(e)}"
        }


def _parse_plan_response(content: str) -> Optional[dict]:
    """解析 LLM 响应中的 JSON"""
    import re
    json_pattern = r'\{[\s\S]*"tasks"[\s\S]*\}'
    match = re.search(json_pattern, content)
    
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            pass
    
    code_block_pattern = r'```(?:json)?\s*([\s\S]*?)```'
    matches = re.findall(code_block_pattern, content)
    for match_str in matches:
        try:
            return json.loads(match_str.strip())
        except:
            continue
    return None


def _build_execution_plan(plan_data: dict) -> ExecutionPlan:
    """构建执行计划"""
    plan = ExecutionPlan()
    tasks = plan_data.get("tasks", [])
    
    for task_data in tasks:
        task_id = task_data["task_id"]
        task_type_str = task_data.get("task_type", "read")
        
        try:
            task_type = TaskType(task_type_str)
        except ValueError:
            task_type = TaskType.READ
        
        tool_name = _normalize_tool_name(task_data.get("tool_name", ""))
        
        node = TaskNode(
            task_id=task_id,
            task_type=task_type,
            tool_name=tool_name,
            params=task_data.get("params", {}),
            depends_on=task_data.get("depends_on", []),
            description=task_data.get("description", "")
        )
        plan.task_dag[task_id] = node
    
    plan.root_tasks = [task_id for task_id, node in plan.task_dag.items() if len(node.depends_on) == 0]
    plan.execution_order = _topological_sort(plan)
    plan.total_count = len(plan.task_dag)
    
    return plan


def _normalize_tool_name(tool_name: str) -> str:
    """标准化工具名称"""
    mapping = {
        "read_email": "read_email",
        "list_outlook_calendar": "list_outlook_calendar_events",
        "list_outlook_calendar_events": "list_outlook_calendar_events",
        "list_todo": "list_todo",
        "create_todo": "create_todo",
        "complete_todo": "complete_todo",
        "delete_todo": "delete_todo",
        "check_urgent": "check_urgent",
        "read_file": "read_file",
        "browser_fetch": "browser_fetch",
        "web_search": "web_search",
        "write_file": "write_file",
        "write_email_draft": "write_email_draft",
        "update_calendar": "update_outlook_calendar_event",
        "update_outlook_calendar_event": "update_outlook_calendar_event",
        "create_outlook_calendar_event": "create_outlook_calendar_event",
        "create_calendar": "create_outlook_calendar_event",
        "weather_query": "weather_query",
    }
    return mapping.get(tool_name.lower(), tool_name)


def _topological_sort(plan: ExecutionPlan) -> list[str]:
    """拓扑排序"""
    result = []
    visited = set()
    
    def visit(task_id: str):
        if task_id in visited:
            return
        visited.add(task_id)
        node = plan.task_dag.get(task_id)
        if node:
            for dep_id in node.depends_on:
                if dep_id in plan.task_dag:
                    visit(dep_id)
        result.append(task_id)
    
    for root_id in plan.root_tasks:
        visit(root_id)
    for task_id in plan.task_dag:
        if task_id not in visited:
            visit(task_id)
    
    return result


# =============================================================================
# Executor 节点 - 执行任务
# =============================================================================

# =============================================================================
# 自我反思与纠错系统
# =============================================================================

REFLECTION_PROMPT_TEMPLATE = """你是一个执行质量审查专家。请分析以下任务的执行结果，判断是否需要修正。

【用户原始请求】
{user_input}

【任务描述】
{task_description}

【任务类型】
{task_type}

【工具名称】
{tool_name}

【工具参数】
{params}

【执行结果】
{result}

【用户意图分析】
{intent_analysis}

请仔细检查：
1. 执行结果是否符合用户原始意图？
2. 工具参数是否正确？（如文件路径、时间格式、邮箱地址等）
3. 是否存在明显的错误或遗漏？
4. 结果是否可信？（如返回空数据是否合理？）

返回 JSON 格式的反思结果：
{{
    "needs_correction": true或false,
    "confidence": 0.0到1.0之间的置信度,
    "issues": [
        {{
            "type": "parameter_error"、"missing_data"、"logic_error"、"unclear_intent"、"other",
            "severity": "critical"、"warning"、"info",
            "description": "问题描述",
            "suggestion": "修正建议"
        }}
    ],
    "confidence_reason": "为什么对这个判断有信心",
    "alternative_analysis": "如果有问题，提供可能的替代方案"
}}

【判断标准】
- needs_correction = true 的情况：
  * 结果明显不符合用户意图
  * 关键参数错误（如文件路径不存在、时间格式错误）
  * 返回空数据但用户期望有结果
  * 工具调用失败且原因是参数问题

- needs_correction = false 的情况：
  * 结果符合用户意图
  * 工具正常执行但数据为空（用户就是查询空数据）
  * 轻微问题但不影响最终效果
"""


def _analyze_user_intent(user_input: str) -> str:
    """分析用户意图，用于反思时的上下文"""
    intent_keywords = {
        "file_read": ["读", "查看", "打开", "看一下"],
        "file_write": ["写", "保存", "创建文件", "写入"],
        "email": ["邮件", "邮箱", "发件", "收件"],
        "calendar": ["日历", "日程", "会议", "安排"],
        "todo": ["待办", "任务", "todo"],
        "web": ["搜索", "网页", "网站", "链接"],
        "weather": ["天气", "气温", "下雨", "晴天"],
    }

    detected = []
    lower_input = user_input.lower()
    for intent, keywords in intent_keywords.items():
        if any(kw in lower_input for kw in keywords):
            detected.append(intent)

    return f"用户意图涉及: {', '.join(detected) if detected else '通用查询'}"


def _execute_reflection(
    plan: ExecutionPlan,
    failed_task: TaskNode,
    user_input: str,
    max_retries: int = 2
) -> dict:
    """
    执行自我反思 - 分析任务失败原因并决定是否重试

    Args:
        plan: 执行计划
        failed_task: 失败的任务
        user_input: 用户原始输入
        max_retries: 最大重试次数

    Returns:
        dict: {
            "needs_correction": bool,
            "new_params": dict 或 None,
            "alternative_tool": str 或 None,
            "reflection_result": dict,
            "should_skip": bool
        }
    """
    print("\n" + "=" * 60)
    print(f"[REFLECTION] 开始反思任务: {failed_task.task_id}")
    print("=" * 60)

    # 构建反思上下文
    intent_analysis = _analyze_user_intent(user_input)

    # 获取源任务结果（如果有依赖）
    source_result = None
    if failed_task.depends_on:
        source_task = plan.task_dag.get(failed_task.depends_on[0])
        if source_task:
            source_result = source_task.result

    # 构建反思 prompt
    reflection_prompt = REFLECTION_PROMPT_TEMPLATE.format(
        user_input=user_input,
        task_description=failed_task.description,
        task_type=failed_task.task_type.value,
        tool_name=failed_task.tool_name,
        params=json.dumps(failed_task.params, ensure_ascii=False, indent=2),
        result=json.dumps(failed_task.result, ensure_ascii=False, indent=2) if failed_task.result else "无结果",
        intent_analysis=intent_analysis
    )

    if source_result:
        reflection_prompt += f"\n\n【上游任务结果】（用于判断数据传递是否正确）\n{json.dumps(source_result, ensure_ascii=False, indent=2)}"

    try:
        from .llm_config import get_llm
        active_llm = get_llm()

        print(f"[REFLECTION] 调用 LLM 进行反思分析...")
        response = active_llm.invoke([SystemMessage(content=reflection_prompt)])
        response_text = response.content if hasattr(response, 'content') else str(response)

        # 打印反思结果
        print(f"\n[REFLECTION] LLM 反思结果:")
        print("-" * 40)
        print(response_text[:500] + "..." if len(response_text) > 500 else response_text)
        print("-" * 40)

        # 解析 LLM 响应
        reflection_result = _parse_reflection_response(response_text)

        # 判断是否需要修正
        needs_correction = reflection_result.get("needs_correction", False)
        issues = reflection_result.get("issues", [])
        should_skip = False
        new_params = None
        alternative_tool = None

        if needs_correction and reflection_result.get("retry_count", 0) < max_retries:
            # 分析问题并生成修正方案
            critical_issues = [i for i in issues if i.get("severity") == "critical"]

            if critical_issues:
                print(f"[REFLECTION] 发现 {len(critical_issues)} 个关键问题，尝试修正...")

                # 根据问题类型生成修正参数
                for issue in critical_issues:
                    issue_type = issue.get("type", "")
                    suggestion = issue.get("suggestion", "")

                    if issue_type == "parameter_error":
                        # 尝试从建议中提取修正后的参数
                        new_params = _extract_corrected_params(
                            failed_task.params,
                            suggestion,
                            failed_task.tool_name
                        )
                    elif issue_type == "unclear_intent":
                        # 意图不清，标记需要跳过
                        should_skip = True

                if new_params:
                    alternative_tool = failed_task.tool_name

            elif issues and issues[0].get("severity") == "warning":
                # 警告级别问题，不重试但记录
                print(f"[REFLECTION] 警告级别问题，不进行重试")
                should_skip = True
        else:
            # 不需要修正或已达最大重试次数
            if reflection_result.get("retry_count", 0) >= max_retries:
                print(f"[REFLECTION] 已达最大重试次数 ({max_retries})，停止重试")
                should_skip = True
            else:
                print(f"[REFLECTION] 反思认为结果正确，无需修正")

        print(f"\n[REFLECTION] 决策:")
        print(f"  - needs_correction: {needs_correction}")
        print(f"  - should_skip: {should_skip}")
        print(f"  - new_params: {new_params is not None}")
        print(f"  - alternative_tool: {alternative_tool}")
        print("=" * 60 + "\n")

        return {
            "needs_correction": needs_correction,
            "new_params": new_params,
            "alternative_tool": alternative_tool,
            "reflection_result": reflection_result,
            "should_skip": should_skip
        }

    except Exception as e:
        print(f"[REFLECTION] 反思过程出错: {e}")
        import traceback
        traceback.print_exc()

        return {
            "needs_correction": False,
            "new_params": None,
            "alternative_tool": None,
            "reflection_result": {"error": str(e)},
            "should_skip": True
        }


def _parse_reflection_response(response_text: str) -> dict:
    """解析 LLM 反思响应"""
    import re

    # 尝试提取 JSON
    json_pattern = r'```(?:json)?\s*([\s\S]*?)```'
    match = re.search(json_pattern, response_text)

    if match:
        json_str = match.group(1).strip()
    else:
        # 直接查找 JSON 对象
        json_pattern2 = r'\{[\s\S]*"needs_correction"[\s\S]*\}'
        match2 = re.search(json_pattern2, response_text)
        if match2:
            json_str = match2.group(0)
        else:
            json_str = "{}"

    try:
        data = json.loads(json_str)
        return data
    except json.JSONDecodeError:
        print(f"[REFLECTION] JSON 解析失败，尝试备用解析")
        # 备用解析：尝试提取关键字段
        needs_correction = "true" in response_text.lower() and "needs_correction" in response_text.lower()

        return {
            "needs_correction": needs_correction,
            "issues": [],
            "confidence": 0.5,
            "confidence_reason": "JSON解析失败，使用默认值"
        }


def _extract_corrected_params(original_params: dict, suggestion: str, tool_name: str) -> dict:
    """从修正建议中提取修正后的参数"""
    import re

    corrected = original_params.copy()

    # 根据工具类型和修正建议调整参数
    if tool_name == "read_file":
        # 检查文件路径问题
        if "路径" in suggestion or "路径" in str(original_params.get("file_path", "")):
            # 尝试推断正确路径
            original_path = original_params.get("file_path", "")
            if "desktop" in original_path.lower() or "桌面" in original_path:
                # 桌面路径问题，尝试其他变体
                if "\\" in original_path:
                    corrected["file_path"] = original_path.replace("\\", "/")

    elif tool_name == "read_email":
        # 检查日期格式
        date_param = original_params.get("date_since", "")
        if date_param and "-" in date_param and len(date_param) > 10:
            # 可能是日期格式问题，尝试转换
            try:
                from datetime import datetime
                # 尝试解析常见格式
                for fmt in ["%d-%b-%Y", "%Y-%m-%d", "%d/%m/%Y"]:
                    try:
                        dt = datetime.strptime(date_param, fmt)
                        corrected["date_since"] = dt.strftime("%d-%b-%Y")
                        break
                    except:
                        continue
            except:
                pass

    elif tool_name in ["create_outlook_calendar_event", "update_outlook_calendar_event"]:
        # 检查时间格式
        for time_param in ["start", "end"]:
            if time_param in corrected:
                time_val = str(corrected[time_param])
                # 如果时间格式有问题，尝试修正
                if re.match(r'^\d{4}-\d{2}-\d{2}$', time_val):
                    # 缺少时间部分，添加默认时间
                    if "start" in time_param:
                        corrected[time_param] = time_val + " 09:00:00"
                    else:
                        corrected[time_param] = time_val + " 10:00:00"

    print(f"[REFLECTION] 参数修正: {original_params} -> {corrected}")
    return corrected


def _create_retry_task(
    plan: ExecutionPlan,
    original_task: TaskNode,
    new_params: dict,
    retry_count: int
) -> str:
    """
    为失败任务创建重试任务

    Returns:
        新任务的 task_id
    """
    retry_task_id = f"{original_task.task_id}_retry_{retry_count}"

    # 复制原任务作为重试任务
    retry_task = TaskNode(
        task_id=retry_task_id,
        task_type=original_task.task_type,
        tool_name=original_task.tool_name,
        params=new_params if new_params else original_task.params,
        depends_on=original_task.depends_on.copy(),
        description=f"[重试 {retry_count}] {original_task.description}",
        status=TaskStatus.PENDING
    )

    # 如果有依赖，更新依赖关系
    if original_task.depends_on:
        retry_task.depends_on = original_task.depends_on.copy()

    plan.task_dag[retry_task_id] = retry_task
    plan.total_count += 1

    print(f"[REFLECTION] 创建重试任务: {retry_task_id}")
    return retry_task_id


def executor_node(state: PlanningState) -> PlanningState:
    """Executor 节点 - 执行可运行的任务

    使用工作记忆系统来维护对话上下文
    """
    execution_plan_dict = state.get("execution_plan")

    if not execution_plan_dict:
        return {**state, "error": "No execution plan", "response_text": "No plan available"}

    plan = _deserialize_plan(execution_plan_dict)
    plan.step_count = state.get("step_count", 0) + 1

    print("\n" + "-" * 60)
    print(f"[EXECUTOR] Step {plan.step_count}")
    print("-" * 60)

    # ========== [工作记忆] 记录执行步骤 ==========
    working_memory_data = state.get("working_memory", {})
    messages = working_memory_data.get("messages", state.get("messages", []))

    # 检查步骤限制
    if plan.step_count > 20:
        print("[WARNING] Step limit reached")
        return {
            **state,
            "execution_plan": _serialize_plan(plan),
            "response_text": "Execution completed (step limit reached)",
            "step_count": plan.step_count,
            "working_memory": working_memory_data
        }

    # 检查待确认
    if plan.pending_confirmation:
        print("[WAITING] Pending confirmation")
        return {
            **state,
            "execution_plan": _serialize_plan(plan),
            "pending_confirmation": plan.pending_confirmation,
            "needs_confirmation": True,
            "step_count": plan.step_count,
            "working_memory": working_memory_data
        }

    # 检查完成
    if plan.is_complete():
        print("[COMPLETE] All tasks done")

        # 生成最终响应
        final_response = _generate_final_response(plan, state)

        # ========== [工作记忆] 更新最终状态 ==========
        updated_messages = messages + [
            {"role": "assistant", "content": final_response}
        ]
        working_memory_data["messages"] = updated_messages
        working_memory_data["updated_at"] = datetime.now().isoformat()

        return {
            **state,
            "execution_plan": _serialize_plan(plan),
            "response_text": final_response,
            "step_count": plan.step_count,
            "working_memory": working_memory_data
        }

    # 获取可运行任务
    runnable = plan.get_next_runnable_tasks()

    if not runnable:
        print("[COMPLETE] No runnable tasks")

        # 生成最终响应
        final_response = _generate_final_response(plan, state)

        # ========== [工作记忆] 更新最终状态 ==========
        updated_messages = messages + [
            {"role": "assistant", "content": final_response}
        ]
        working_memory_data["messages"] = updated_messages
        working_memory_data["updated_at"] = datetime.now().isoformat()

        return {
            **state,
            "execution_plan": _serialize_plan(plan),
            "response_text": final_response,
            "step_count": plan.step_count,
            "working_memory": working_memory_data
        }

    # 执行第一个任务
    task_id = runnable[0]
    task = plan.task_dag[task_id]

    print(f"[TASK] Executing: [{task_id}] {task.description}")
    print(f"       Tool: {task.tool_name}, Type: {task.task_type.value}")

    # 根据任务类型执行
    # 从 state 中获取缓存的工具结果
    cached_tool_results = state.get("cached_tool_results", {})
    reflection_enabled = state.get("reflection_enabled", True)
    max_retries = state.get("max_retries", 2)

    if task.task_type == TaskType.CONDITION:
        result = _execute_condition(plan, task)
    elif task.task_type == TaskType.SYNTHESIZE:
        result = _execute_synthesize(state, plan, task)
    elif task.task_type == TaskType.LLM_JUDGE:
        result = _execute_llm_judge(state, plan, task, cached_results=cached_tool_results)
    else:
        result = _execute_tool(plan, task)

    print(f"[EXECUTOR] After task execution:")
    print(f"           pending_confirmation: {plan.pending_confirmation}")
    print(f"           needs_confirmation: {plan.pending_confirmation is not None}")

    # ========== [反思机制] 检查任务是否失败 ==========
    task_failed = False
    task_needs_retry = False

    # 检查任务状态是否为失败
    current_task = plan.task_dag.get(task_id)
    if current_task and current_task.status == TaskStatus.FAILED:
        task_failed = True
        print(f"[REFLECTION] 任务 {task_id} 执行失败，触发反思机制")

    # 检查返回结果是否包含错误
    tool_result = result.get("tool_result", {})
    if tool_result and isinstance(tool_result, dict):
        if not tool_result.get("success", True) and tool_result.get("error"):
            task_failed = True
            print(f"[REFLECTION] 任务 {task_id} 返回错误: {tool_result.get('error')}")

    # 如果任务失败且启用反思机制，进行反思
    if task_failed and reflection_enabled and current_task:
        print(f"[REFLECTION] 开始自我反思与纠错...")

        # 获取当前重试次数
        current_retry_count = 0
        reflection_history = state.get("reflection_history", [])

        # 检查之前的反思记录
        for record in reflection_history:
            if record.get("task_id") == task_id:
                current_retry_count = record.get("retry_count", 0)
                break

        # 执行反思
        reflection_outcome = _execute_reflection(
            plan=plan,
            failed_task=current_task,
            user_input=state.get("user_input", ""),
            max_retries=max_retries
        )

        reflection_result = reflection_outcome.get("reflection_result", {})

        # 更新反思历史
        new_reflection_history = reflection_history + [{
            "task_id": task_id,
            "timestamp": datetime.now().isoformat(),
            "reflection_result": reflection_result,
            "retry_count": current_retry_count
        }]

        # 根据反思结果决定下一步
        if reflection_outcome.get("needs_correction") and not reflection_outcome.get("should_skip"):
            if current_retry_count < max_retries:
                # 需要修正，创建重试任务
                new_retry_count = current_retry_count + 1

                if reflection_outcome.get("new_params"):
                    # 使用修正后的参数创建重试任务
                    retry_task_id = _create_retry_task(
                        plan=plan,
                        original_task=current_task,
                        new_params=reflection_outcome.get("new_params"),
                        retry_count=new_retry_count
                    )

                    print(f"[REFLECTION] 创建重试任务 {retry_task_id}，使用修正后的参数")

                    # 标记原任务为需要重试
                    current_task.status = TaskStatus.NEEDS_RETRY

                    result_dict = {
                        "execution_plan": _serialize_plan(plan),
                        "tool_result": result.get("tool_result"),
                        "current_task_id": task_id,
                        "needs_confirmation": plan.pending_confirmation is not None,
                        "pending_confirmation": plan.pending_confirmation,
                        "response_text": f"[反思] 任务执行遇到问题，正在使用修正参数重试...\n反思分析: {reflection_result.get('confidence_reason', '参数可能存在问题')}",
                        "step_count": plan.step_count,
                        "reflection_result": reflection_result,
                        "retry_count": new_retry_count,
                        "reflection_history": new_reflection_history,
                        "reflection_enabled": reflection_enabled,
                        "max_retries": max_retries,
                        "working_memory": working_memory_data
                    }

                    return result_dict
        else:
            # 不需要修正或已达最大重试次数，标记任务为跳过
            current_task.status = TaskStatus.RETRY_FAILED
            print(f"[REFLECTION] 任务 {task_id} 标记为 RETRY_FAILED，将跳过")

            # 生成错误报告
            issues = reflection_result.get("issues", [])
            issue_summary = "\n".join([f"- {i.get('description', '未知问题')}" for i in issues]) if issues else "无具体问题描述"

            result_dict = {
                "execution_plan": _serialize_plan(plan),
                "tool_result": result.get("tool_result"),
                "current_task_id": task_id,
                "needs_confirmation": plan.pending_confirmation is not None,
                "pending_confirmation": plan.pending_confirmation,
                "response_text": f"[反思] 任务执行遇到问题，经过 {current_retry_count + 1} 次尝试后仍无法成功。\n\n问题分析：\n{issue_summary}\n\n反思结论：{reflection_result.get('confidence_reason', '任务确实无法完成')}",
                "step_count": plan.step_count,
                "reflection_result": reflection_result,
                "retry_count": current_retry_count + 1,
                "reflection_history": new_reflection_history,
                "reflection_enabled": reflection_enabled,
                "max_retries": max_retries,
                "working_memory": working_memory_data
            }

            return result_dict

    # ========== [反思机制结束] 正常返回 ==========

    # 记录工具执行结果到工作记忆
    if tool_result:
        tool_summary = f"[{task.tool_name}] {json.dumps(tool_result, ensure_ascii=False)[:200]}"
        messages = messages + [{"role": "tool", "content": tool_summary, "tool": task.tool_name}]

    result_dict = {
        "execution_plan": _serialize_plan(plan),
        "tool_result": result.get("tool_result"),
        "current_task_id": task_id,
        "needs_confirmation": plan.pending_confirmation is not None,
        "pending_confirmation": plan.pending_confirmation,
        "response_text": result.get("response_text"),
        "step_count": plan.step_count,
        "reflection_enabled": reflection_enabled,
        "max_retries": max_retries,
        "working_memory": {
            "messages": messages,
            "session_id": working_memory_data.get("session_id", ""),
            "updated_at": datetime.now().isoformat()
        }
    }

    return result_dict


def _execute_condition(plan: ExecutionPlan, task: TaskNode) -> dict:
    """执行条件判断任务"""
    print(f"[CONDITION] Starting condition check for task: {task.task_id}")
    
    try:
        source_task_id = task.params.get("source_task")
        
        if not source_task_id:
            print("[CONDITION] No source task, marking as completed")
            task.status = TaskStatus.COMPLETED
            task.condition_result = True
            task.result = {"condition_result": True}
            plan.completed_count += 1
            return {"tool_result": task.result}
        
        source_task = plan.task_dag.get(source_task_id)
        if not source_task:
            print(f"[CONDITION] Source task {source_task_id} not found")
            task.status = TaskStatus.FAILED
            task.result = {"error": f"Source task {source_task_id} not found"}
            return {"tool_result": task.result}
        
        if not source_task.result:
            print(f"[CONDITION] Source task {source_task_id} has no result")
            task.status = TaskStatus.FAILED
            task.result = {"error": f"Source task {source_task_id} not completed"}
            return {"tool_result": task.result}
        
        print(f"[CONDITION] Source task result: {source_task.result}")
        
        # 简单的条件判断任务
        # 实际的判断逻辑应该由 llm_judge 类型任务处理
        task.condition_result = True
        task.result = {"condition_result": True, "message": "Simple condition check"}
        response_text = "条件检查完成"
        
        task.status = TaskStatus.COMPLETED
        plan.completed_count += 1
        
        return {"tool_result": task.result, "response_text": response_text}
    
    except Exception as e:
        print(f"[CONDITION] Error: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        task.status = TaskStatus.FAILED
        task.result = {"error": str(e)}
        plan.completed_count += 1
        return {"tool_result": task.result, "response_text": f"执行出错: {str(e)}"}


def _execute_llm_judge(state: PlanningState, plan: ExecutionPlan, task: TaskNode, cached_results: dict = None) -> dict:
    """
    执行 LLM 判断任务
    通用的 LLM 判断器，根据 source_task 的结果和 question 进行语义分析判断
    支持从缓存的跨会话工具结果中获取数据
    """
    print(f"[LLM_JUDGE] Starting LLM judge for task: {task.task_id}")
    
    try:
        source_task_id = task.params.get("source_task")
        question = task.params.get("question", "")
        result_key = task.params.get("result_key", "result")
        
        if not source_task_id:
            print("[LLM_JUDGE] No source task, marking as failed")
            task.status = TaskStatus.FAILED
            task.result = {"error": "No source task specified"}
            return {"tool_result": task.result}
        
        source_task = plan.task_dag.get(source_task_id)
        source_result = None
        
        if source_task and source_task.result:
            # 从当前计划的 DAG 中获取结果
            source_result = source_task.result
            print(f"[LLM_JUDGE] Got result from current plan: {source_task_id}")
        elif cached_results and source_task_id in cached_results:
            # 从跨会话缓存中获取结果
            source_result = cached_results[source_task_id]
            print(f"[LLM_JUDGE] Got result from cache: {source_task_id}")
        else:
            print(f"[LLM_JUDGE] Source task {source_task_id} not found (not in plan or cache)")
            task.status = TaskStatus.FAILED
            task.result = {"error": f"Source task {source_task_id} not found (not in current plan or session cache)"}
            return {"tool_result": task.result}
        
        if not source_result:
            print(f"[LLM_JUDGE] Source task {source_task_id} has no result")
            task.status = TaskStatus.FAILED
            task.result = {"error": f"Source task {source_task_id} has empty result"}
            return {"tool_result": task.result}
        
        # 获取源任务的结果
        # 构建上下文信息
        context = _build_context_from_result(source_result)
        
        print(f"[LLM_JUDGE] Context length: {len(context)} chars")
        
        # 构建 LLM 判断 prompt
        prompt = f"""你是一个智能判断助手。请根据以下上下文信息回答用户的问题。

上下文信息：
{context}

用户问题：
{question}

请用 JSON 格式回复：
{{
    "{result_key}": true/false,
    "reason": "判断理由",
    "details": "具体分析"
}}

如果用户问题不需要返回布尔值，则返回：
{{
    "{result_key}": "具体回答内容",
    "reason": "回答依据",
    "details": "详细分析"
}}"""

        # 调用 LLM
        from .llm_config import get_llm
        active_llm = get_llm()
        response = active_llm.invoke([SystemMessage(content=prompt)])
        response_text = response.content if hasattr(response, 'content') else str(response)
        
        print(f"[LLM_JUDGE] LLM response: {response_text[:200]}...")
        
        # 解析 LLM 响应
        import re
        json_match = re.search(r'\{[\s\S]*\}', response_text)
        if json_match:
            llm_result = json.loads(json_match.group())
            judge_result = llm_result.get(result_key)
            
            # 特殊处理：如果是选择 URL 的任务，当返回空时使用第一个搜索结果
            if result_key == "selected_url" and not judge_result:
                # 从源任务获取搜索结果
                if source_result and source_result.get("results"):
                    first_result = source_result["results"][0]
                    judge_result = first_result.get("url", "")
                    print(f"[LLM_JUDGE] Empty selection, using first result: {judge_result}")
            
            task.result = {
                "judge_result": judge_result,
                "reason": llm_result.get("reason", ""),
                "details": llm_result.get("details", ""),
                "raw_response": llm_result
            }
            
            # 如果是选择 URL 的任务，更新后续 browser_fetch 的 URL
            if result_key == "selected_url" and judge_result:
                _update_browser_fetch_urls(plan, judge_result, source_result.get("results", []) if source_result else [])
            
            # 如果是布尔值判断，设置 condition_result
            if isinstance(judge_result, bool):
                task.condition_result = judge_result
                
                # 如果条件为 false，尝试获取下一个 URL 重试
                if not judge_result:
                    # 尝试从 web_search 结果中获取下一个 URL
                    if source_task and source_task.params:
                        search_task_id = source_task.params.get("source_task")
                        if search_task_id:
                            search_task = plan.task_dag.get(search_task_id)
                            if search_task and search_task.result and search_task.result.get("results"):
                                results = search_task.result.get("results", [])
                                # 找到当前使用的 URL
                                current_url = None
                                for node in plan.task_dag.values():
                                    if node.tool_name == "browser_fetch" and node.params.get("url"):
                                        current_url = node.params.get("url")
                                        break
                                
                                # 找到下一个未尝试的 URL
                                next_url = None
                                found_current = False
                                for r in results:
                                    url = r.get("url", "")
                                    if found_current:
                                        next_url = url
                                        break
                                    if url == current_url:
                                        found_current = True
                                
                                if next_url:
                                    # 更新 browser_fetch 任务的 URL
                                    for tid, node in plan.task_dag.items():
                                        if node.tool_name == "browser_fetch":
                                            print(f"       [RETRY] 更新 URL: {current_url} -> {next_url}")
                                            node.params["url"] = next_url
                                            node.status = TaskStatus.PENDING  # 重置为待执行
                                            break
                                    
                                    # 重置 task_3 为待执行（将重新判断）
                                    task.status = TaskStatus.PENDING
                                    plan.completed_count -= 1
                                    print(f"       [RETRY] 将重新获取内容...")
                                else:
                                    # 没有更多 URL，跳过下游任务
                                    for tid, node in plan.task_dag.items():
                                        if task.task_id in node.depends_on:
                                            print(f"       Skipping downstream task: {tid}")
                                            node.status = TaskStatus.SKIPPED
            
            print(f"[LLM_JUDGE] Result: {judge_result}")
        else:
            # 解析失败
            print(f"[LLM_JUDGE] Failed to parse LLM response")
            task.result = {
                "judge_result": None,
                "raw_response": response_text,
                "error": "Failed to parse LLM response"
            }
        
        task.status = TaskStatus.COMPLETED
        plan.completed_count += 1
        
        # 保存到 intermediate_results 供后续任务引用
        plan.intermediate_results = plan.intermediate_results or {}
        plan.intermediate_results[task.task_id] = task.result

        # 生成响应文本
        response_text = ""
        judge_result = task.result.get("judge_result")
        reason = task.result.get("reason", "")
        details = task.result.get("details", "")

        if isinstance(judge_result, bool):
            if judge_result:
                if details:
                    response_text = details
                elif reason:
                    response_text = reason
                else:
                    response_text = "是的，找到了相关结果"
            else:
                response_text = "没有找到相关结果"
        elif judge_result:
            response_text = str(judge_result)
        elif reason:
            response_text = reason

        return {"tool_result": task.result, "response_text": response_text}
    
    except Exception as e:
        print(f"[LLM_JUDGE] Error: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        task.status = TaskStatus.FAILED
        task.result = {"error": str(e)}
        plan.completed_count += 1
        return {"tool_result": task.result}


def _build_context_from_result(result: dict) -> str:
    """
    根据不同类型的结果构建上下文信息
    """
    if not result:
        return "无数据"
    
    # 邮件列表
    if "messages" in result:
        messages = result.get("messages", [])
        if not messages:
            return "没有找到邮件"
        
        context_parts = [f"共找到 {len(messages)} 封邮件：\n"]
        for i, msg in enumerate(messages[:10]):  # 最多 10 封
            subject = msg.get("subject", "无主题")
            sender = msg.get("from", "未知发件人")
            date = msg.get("date", "")
            body = (msg.get("body_preview") or msg.get("snippet") or msg.get("body") or "")[:300]
            context_parts.append(f"邮件 {i+1}:\n  主题: {subject}\n  发件人: {sender}\n  日期: {date}\n  内容: {body}")
        return "\n".join(context_parts)
    
    # 待办列表
    if "items" in result:
        items = result.get("items", [])
        if not items:
            return "没有待办事项"
        
        context_parts = [f"共找到 {len(items)} 个待办事项：\n"]
        for i, item in enumerate(items[:10]):
            content = item.get("content", "无内容")
            due = item.get("due", item.get("due_date", "无截止日期"))
            status = item.get("status", "unknown")
            context_parts.append(f"待办 {i+1}:\n  内容: {content}\n  截止: {due}\n  状态: {status}")
        return "\n".join(context_parts)
    
    # 日历事件
    if "events" in result or "items" in result:
        events = result.get("events", result.get("items", []))
        if not events:
            return "没有日历事件"
        
        context_parts = [f"共找到 {len(events)} 个日历事件：\n"]
        for i, event in enumerate(events[:10]):
            subject = event.get("subject", event.get("title", "无标题"))
            start = event.get("start", event.get("start_time", ""))
            end = event.get("end", event.get("end_time", ""))
            location = event.get("location", "")
            context_parts.append(f"事件 {i+1}:\n  标题: {subject}\n  开始: {start}\n  结束: {end}\n  地点: {location}")
        return "\n".join(context_parts)
    
    # 文件内容
    if "content" in result:
        content = result.get("content", "")
        return f"文件内容:\n{content[:1000]}"
    
    # 直接返回 JSON 格式的结果
    return json.dumps(result, ensure_ascii=False, indent=2)[:2000]


def _execute_synthesize(state: PlanningState, plan: ExecutionPlan, task: TaskNode) -> dict:
    """执行综合任务"""
    results = {}
    for dep_id in task.depends_on:
        dep_task = plan.task_dag.get(dep_id)
        if dep_task and dep_task.result:
            results[dep_id] = dep_task.result
    
    user_input = state.get('user_input', '')
    
    # 检测用户是否需要将内容保存到文件
    # 常见模式：保存到xxx、存到xxx、写入xxx、记录到xxx
    needs_file_content = any(keyword in user_input for keyword in ['保存', '存到', '写入', '记录到', '保存到'])
    
    # 构建源任务内容
    source_content = ""
    for task_id, result in results.items():
        if isinstance(result, dict):
            # browser_fetch 结果
            if result.get('content') or result.get('raw_text'):
                title = result.get('title', '')
                content = result.get('content', '') or result.get('raw_text', '')
                source_content += f"\n\n=== 来源：{title} ===\n{content}"
            # web_search 结果
            elif result.get('results'):
                search_results = result.get('results', [])
                source_content += f"\n\n=== 搜索结果 ({len(search_results)} 条) ===\n"
                for i, r in enumerate(search_results[:5], 1):
                    source_content += f"{i}. {r.get('title', '')} - {r.get('url', '')}\n"
    
    # 根据用户意图选择不同的 prompt
    if needs_file_content:
        # 用户需要将内容保存到文件，生成可直接写入的 Markdown 内容
        prompt = f"""你是一个知识整理助手。请根据以下搜索/网页内容，整理出要点并生成可直接写入文件的 Markdown 格式内容。

用户请求：{user_input}

内容来源：
{source_content[:15000]}

请生成符合以下要求的 Markdown 内容：
1. 直接输出 Markdown 格式的内容（不要包含"以下是..."、"内容如下..."等引导语）
2. 使用清晰的标题、列表、代码块等格式
3. 内容应该是用户可以直接保存到文件使用的格式
4. 不要添加任何解释性文字或元信息

直接输出 Markdown 内容："""
    else:
        # 普通回答
        prompt = f"""Based on these results, answer the user's question.

User question: {user_input}

Results:
{json.dumps(results, ensure_ascii=False, indent=2)[:5000]}

Provide a concise answer."""
    
    try:
        active_llm = get_llm()
        response = active_llm.invoke([HumanMessage(content=prompt)])
        response_text = response.content if hasattr(response, 'content') else "Done"
    except Exception as e:
        response_text = f"Done (error: {str(e)})"
    
    task.status = TaskStatus.COMPLETED
    task.result = {"response": response_text}
    plan.completed_count += 1
    
    print(f"[SYNTHESIZE] Response: {response_text[:200]}...")
    print(f"[SYNTHESIZE] needs_file_content: {needs_file_content}")
    
    return {"tool_result": task.result, "response_text": response_text}


def _generate_final_response(plan: ExecutionPlan, state: PlanningState) -> str:
    """
    生成最终响应 - 汇总所有任务结果
    """
    user_question = state.get("user_input", "")
    results = {}

    # 收集所有任务结果
    for task_id, task in plan.task_dag.items():
        if task.result:
            results[task_id] = task.result

    # ========== [DEBUG] 诊断最终响应生成 ==========
    print("\n" + "=" * 60)
    print("[DEBUG] _generate_final_response 诊断:")
    print("=" * 60)
    print(f"任务总数: {len(plan.task_dag)}")
    for tid, task in plan.task_dag.items():
        result_keys = list(task.result.keys()) if task.result else None
        print(f"  [{tid}] type={task.task_type.value}, tool={task.tool_name}, status={task.status.value}")
        print(f"       result={task.result is not None}, result_keys={result_keys}")
        if task.result:
            print(f"       title={task.result.get('title', '')!r}")
            content_len = len(task.result.get('content', '')) if task.result.get('content') else 0
            print(f"       content_length={content_len}")
    print(f"[DEBUG] browser_fetch_task detection: {[t.tool_name for t in plan.task_dag.values()]}")
    print("=" * 60)
    # ========== [DEBUG] 结束 ==========

    # 构建响应文本
    response_parts = []

    # 检查是否有日程创建任务
    calendar_task = None
    for task_id, task in plan.task_dag.items():
        if "calendar" in task.tool_name.lower() or task.params.get("summary"):
            calendar_task = task
            break

    # 检查是否有待办创建任务
    todo_task = None
    for task_id, task in plan.task_dag.items():
        if "todo" in task.tool_name.lower() and task.task_type.value == "create":
            todo_task = task
            break

    # 检查是否有邮件任务
    email_task = None
    for task_id, task in plan.task_dag.items():
        if "email" in task.tool_name.lower():
            email_task = task
            break

    # 检查是否有 llm_judge 任务（用于内容分析/判断）
    llm_judge_task = None
    for task_id, task in plan.task_dag.items():
        # 使用 .value 比较字符串值，避免类型问题
        if task.task_type.value == "llm_judge":
            llm_judge_task = task
            break

    # 检查是否有 browser_fetch 任务（用于网页内容提取）
    browser_fetch_task = None
    for task_id, task in plan.task_dag.items():
        if task.tool_name == "browser_fetch":
            browser_fetch_task = task
            break

    # ========== [DEBUG] 任务类型检测 ==========
    print(f"[DEBUG] calendar_task: {calendar_task is not None}")
    print(f"[DEBUG] todo_task: {todo_task is not None}")
    print(f"[DEBUG] email_task: {email_task is not None}")
    print(f"[DEBUG] llm_judge_task: {llm_judge_task is not None}")
    print(f"[DEBUG] browser_fetch_task: {browser_fetch_task is not None}")
    if browser_fetch_task:
        print(f"[DEBUG] browser_fetch_task.result keys: {list(browser_fetch_task.result.keys()) if browser_fetch_task.result else None}")
    # ========== [DEBUG] 结束 ==========

    # 生成有意义的响应
    # 优先级：calendar > todo > write_email > llm_judge > browser_fetch > email
    # 当有 calendar 任务时，跳过 llm_judge 的详细分析
    
    if calendar_task and calendar_task.result:
        # 使用实际存在的参数
        subject = calendar_task.params.get("subject", calendar_task.params.get("summary", "日历事件"))
        start = calendar_task.params.get("start", calendar_task.params.get("start_datetime", ""))
        body = calendar_task.params.get("body", "")
        response_parts.append(f"已为您创建日程：{subject}")
        if start:
            response_parts.append(f"时间：{start}")
        if body:
            response_parts.append(f"备注：{body}")
        # 日历创建成功后，跳过其他任务的结果显示
    elif todo_task and todo_task.result:
        content = todo_task.params.get("content", "")
        due_date = todo_task.params.get("due_date", "")
        if content:
            response_parts.append(f"已创建待办：{content}")
        if due_date:
            response_parts.append(f"截止日期：{due_date}")

    # 检查是否有写邮件草稿任务
    write_email_task = None
    for task_id, task in plan.task_dag.items():
        if task.tool_name == "write_email_draft":
            write_email_task = task
            break

    if write_email_task and write_email_task.result:
        result = write_email_task.result
        if result.get("success"):
            to = write_email_task.params.get("to", "")
            subject = write_email_task.params.get("subject", "")
            response_parts.append(f"已创建邮件草稿")
            if to:
                response_parts.append(f"收件人：{to}")
            if subject:
                response_parts.append(f"主题：{subject}")
            response_parts.append("请在 Outlook 草稿箱中查看并发送")
        else:
            error = result.get("error", "未知错误")
            response_parts.append(f"创建邮件草稿失败: {error}")

    elif llm_judge_task and llm_judge_task.result:
        # LLM 判断结果 - 这是用户需要的关键信息
        result = llm_judge_task.result
        judge_result = result.get("judge_result")
        reason = result.get("reason", "")
        details = result.get("details", "")

        if judge_result is not None:
            # 如果判断结果是布尔值
            if isinstance(judge_result, bool):
                if judge_result:
                    if details:
                        response_parts.append(details)
                    elif reason:
                        response_parts.append(reason)
                    else:
                        response_parts.append("是的，找到了相关结果")
                else:
                    response_parts.append("没有找到相关结果")
            else:
                # 判断结果是其他类型（如字符串），直接使用
                if judge_result:
                    response_parts.append(str(judge_result))
        elif reason:
            response_parts.append(reason)

    elif browser_fetch_task and browser_fetch_task.result:
        # 网页抓取结果 - 生成内容摘要
        result = browser_fetch_task.result
        if result.get("success"):
            title = result.get("title", "")
            content = result.get("content", "") or result.get("raw_text", "")
            
            if content:
                # 调用 LLM 生成摘要
                from .llm_config import get_llm
                try:
                    summary_prompt = f"""请总结以下网页内容的主要信息：

标题：{title}

内容：
{content[:8000]}

请用简洁的语言总结页面的主要内容，包括：
1. 主题是什么
2. 主要讲述了哪些要点
3. 适合的读者群体（如有）"""
                    
                    active_llm = get_llm()
                    llm_response = active_llm.invoke([
                        SystemMessage(content=summary_prompt)
                    ])
                    summary = llm_response.content if hasattr(llm_response, 'content') else ""
                    
                    if summary:
                        response_parts.append(summary)
                    else:
                        # Fallback to raw content preview
                        response_parts.append(f"【{title}】\n\n{content[:1000]}...")
                except Exception as e:
                    # 如果 LLM 总结失败，返回原始内容
                    response_parts.append(f"【{title}】\n\n{content[:1000]}...")
            else:
                response_parts.append("网页内容为空")
        else:
            error = result.get("error", "未知错误")
            response_parts.append(f"无法访问该网页: {error}")

    elif email_task and email_task.result:
        # 邮件结果 - 显示找到的邮件信息
        result = email_task.result
        if result.get("success"):
            messages = result.get("messages", [])
            if messages:
                response_parts.append(f"找到 {len(messages)} 封邮件")
                # 显示前几封邮件的主题
                subjects = [m.get("subject", "无主题") for m in messages[:3]]
                if subjects:
                    response_parts.append(f"主题: {', '.join(subjects)}")
            else:
                response_parts.append("没有找到符合条件的邮件")
        else:
            error = result.get("error", "未知错误")
            response_parts.append(f"读取邮件失败: {error}")
    
    # 如果没有特定任务的响应，生成通用响应
    if not response_parts:
        completed = [t for t in plan.task_dag.values() if t.status == TaskStatus.COMPLETED]
        skipped = [t for t in plan.task_dag.values() if t.status == TaskStatus.SKIPPED]
        
        # 特别处理 browser_fetch 任务
        for task in plan.task_dag.values():
            if task.tool_name == "browser_fetch" and task.result:
                result = task.result
                if result.get("success"):
                    title = result.get("title", "")
                    content = result.get("content", "") or result.get("raw_text", "")
                    if content:
                        # 调用 LLM 生成摘要
                        from .llm_config import get_llm
                        try:
                            summary_prompt = f"""请总结以下网页内容的主要信息：

标题：{title}

内容：
{content[:8000]}

请用简洁的语言总结页面的主要内容，包括：
1. 主题是什么
2. 主要讲述了哪些要点
3. 适合的读者群体（如有）"""
                            
                            active_llm = get_llm()
                            llm_response = active_llm.invoke([
                                SystemMessage(content=summary_prompt)
                            ])
                            summary = llm_response.content if hasattr(llm_response, 'content') else ""
                            
                            if summary:
                                return summary
                            else:
                                return f"【{title}】\n\n{content[:1000]}..."
                        except Exception as e:
                            return f"【{title}】\n\n{content[:1000]}..."
                    else:
                        return "网页内容为空"
                else:
                    error = result.get("error", "未知错误")
                    return f"无法访问该网页: {error}"
        
        if skipped:
            response_parts.append(f"已完成 {len(completed)} 个任务")
            response_parts.append(f"跳过了 {len(skipped)} 个任务（条件不满足）")
        elif completed:
            response_parts.append(f"已完成全部 {len(completed)} 个任务")
    
    if not response_parts:
        response_parts = ["任务执行完成"]
    
    return "。".join(response_parts)


def _execute_web_search(plan: ExecutionPlan, task: TaskNode, params: dict) -> dict:
    """
    执行 web_search 任务并自动更新后续 browser_fetch 任务的 URL
    """
    from .tools import web_search_tool
    
    try:
        result = web_search_tool.execute(**params)
        task.result = result
        plan.intermediate_results = plan.intermediate_results or {}
        plan.intermediate_results[task.task_id] = result
        
        if result.get("success"):
            task.status = TaskStatus.COMPLETED
            plan.completed_count += 1
            print(f"[SUCCESS] Web search completed, found {len(result.get('results', []))} results")
            
            # 自动更新后续 browser_fetch 任务的 URL
            results = result.get("results", [])
            if results:
                first_url = results[0].get("url", "")
                if first_url:
                    _update_browser_fetch_urls(plan, first_url, results)
                    print(f"[WEB_SEARCH] Updated next browser_fetch with URL: {first_url}")
            
            return {"tool_result": result}
        else:
            task.status = TaskStatus.FAILED
            print(f"[FAILED] Web search failed: {result.get('error')}")
            return {"tool_result": result}
            
    except Exception as e:
        task.status = TaskStatus.FAILED
        task.result = {"error": str(e)}
        print(f"[ERROR] Web search error: {e}")
        return {"tool_result": task.result}


def _update_browser_fetch_urls(plan: ExecutionPlan, url: str, all_results: list = None):
    """
    更新后续 browser_fetch 任务的 URL
    如果有明确的 URL 选择，优先使用
    """
    for task_id, node in plan.task_dag.items():
        if node.tool_name == "browser_fetch":
            current_url = node.params.get("url", "")
            # 更新为新选择的 URL
            node.params["url"] = url
            print(f"[UPDATE] Updated task {task_id} URL to: {url}")
            
            # 保存搜索结果到 plan 的 intermediate_results（不传给工具）
            if all_results:
                plan.intermediate_results = plan.intermediate_results or {}
                plan.intermediate_results[f"{task_id}_search_results"] = all_results


def _execute_browser_fetch_with_retry(plan: ExecutionPlan, task: TaskNode, params: dict) -> dict:
    """
    执行 browser_fetch，支持超时重试下一个 URL
    """
    from .tools import TOOL_MAP, browser_fetch_tool
    
    # 获取搜索结果作为备用 URL 列表
    search_results = plan.intermediate_results.get(f"{task.task_id}_search_results", []) if plan.intermediate_results else []
    
    url_to_try = params.get("url", "")
    tried_urls = set()
    max_retries = 3
    retry_count = 0
    
    while retry_count < max_retries:
        if not url_to_try or url_to_try in tried_urls:
            # 从搜索结果中选择下一个未尝试的 URL
            for result in search_results:
                next_url = result.get("url", "")
                if next_url and next_url not in tried_urls:
                    url_to_try = next_url
                    break
            if not url_to_try or url_to_try in tried_urls:
                break  # 没有更多 URL 可尝试
        
        tried_urls.add(url_to_try)
        print(f"[BROWSER_FETCH] Attempt {retry_count + 1}: trying {url_to_try}")
        
        try:
            # 执行 fetch
            result = browser_fetch_tool.execute(
                url=url_to_try,
                extract_content=params.get("extract_content", True),
                max_content_length=params.get("max_content_length", 50000)
            )
            
            if result.get("success"):
                task.result = result
                task.status = TaskStatus.COMPLETED
                plan.completed_count += 1
                return {"tool_result": result}
            else:
                error_msg = result.get("error", "")
                print(f"[BROWSER_FETCH] Failed: {error_msg}")
                # 如果是超时错误，尝试下一个 URL
                if "Timeout" in error_msg or "timeout" in error_msg.lower():
                    retry_count += 1
                    url_to_try = ""  # 触发选择下一个 URL
                    continue
                else:
                    # 其他错误也重试
                    retry_count += 1
                    url_to_try = ""
                    continue
                    
        except Exception as e:
            print(f"[BROWSER_FETCH] Exception: {e}")
            retry_count += 1
            url_to_try = ""
            continue
    
    # 所有 URL 都失败了
    task.status = TaskStatus.FAILED
    task.result = {
        "success": False,
        "error": f"无法访问任何提供的 URL（尝试了 {len(tried_urls)} 个）",
        "tried_urls": list(tried_urls)
    }
    return {"tool_result": task.result}


def _execute_tool(plan: ExecutionPlan, task: TaskNode) -> dict:
    """执行工具任务"""
    from .tools import TOOL_MAP
    
    tool_name = task.tool_name
    params = _resolve_params(task.params, plan)
    
    print(f"[TOOL] Params: {params}")
    
    # 特殊处理 web_search：自动更新后续 browser_fetch 任务的 URL
    if tool_name == "web_search":
        return _execute_web_search(plan, task, params)
    
    # 特殊处理 browser_fetch：支持超时重试
    if tool_name == "browser_fetch":
        return _execute_browser_fetch_with_retry(plan, task, params)
    
    # 判断是读操作还是写操作
    write_tools = ["create_todo", "write_file", "write_email_draft", "update_outlook_calendar_event", "create_outlook_calendar_event"]
    is_write = tool_name in write_tools
    
    # 直接从 TOOL_MAP 查找（已包含预览工具）
    tool = TOOL_MAP.get(tool_name)
    
    if not tool:
        task.status = TaskStatus.FAILED
        task.result = {"error": f"Unknown tool: {tool_name}"}
        print(f"[TOOL] Tool not found: {tool_name}")
        print(f"[TOOL] Available in TOOL_MAP: {list(TOOL_MAP.keys())}")
        return {"tool_result": task.result}
    
    if is_write:
        # 写操作需要确认
        preview_result = tool.execute(**params) if hasattr(tool, 'execute') else {"success": False, "error": "Cannot execute"}
        
        op_type = "create_todo" if tool_name == "create_todo" else "write_file"
        
        if tool_name == "create_outlook_calendar_event":
            op_type = "create_calendar_event"
        elif tool_name == "update_outlook_calendar_event":
            op_type = "update_calendar_event"
        
        plan.pending_confirmation = {
            "operation": op_type,
            "tool_name": tool_name,
            "args": params,
            "preview": preview_result.get("preview") if isinstance(preview_result, dict) else None,
            "success": preview_result.get("success", True),
            "task_id": task.task_id
        }
        
        task.status = TaskStatus.WAITING_CONFIRMATION
        
        # 根据操作类型生成预览文本
        if tool_name == "create_outlook_calendar_event":
            preview_text = f"""**Preview: 创建日历事件**

标题: {params.get('subject', 'N/A')}
开始时间: {params.get('start', 'N/A')}
结束时间: {params.get('end', 'N/A')}
地点: {params.get('location', '无')}
全天事件: {'是' if params.get('all_day') else '否'}
备注: {params.get('body', '无')[:100] if params.get('body') else '无'}

确认创建此日程吗？"""
        elif tool_name == "update_outlook_calendar_event":
            preview_text = f"""**Preview: 更新日历事件**

ID: {params.get('entry_id', 'N/A')[:20]}...
"""
            changes = []
            if params.get('subject'):
                changes.append(f"标题 -> {params.get('subject')}")
            if params.get('start'):
                changes.append(f"开始时间 -> {params.get('start')}")
            if params.get('end'):
                changes.append(f"结束时间 -> {params.get('end')}")
            if params.get('location'):
                changes.append(f"地点 -> {params.get('location')}")
            if params.get('all_day') is not None:
                changes.append(f"全天事件 -> {'是' if params.get('all_day') else '否'}")
            if params.get('body'):
                changes.append(f"备注 -> {params.get('body')[:50]}...")
            
            if changes:
                preview_text += "\n修改内容:\n" + "\n".join(f"- {c}" for c in changes)
            
            preview_text += "\n\n确认更新此日程吗？"""
        elif tool_name == "create_todo":
            preview_text = f"""**Preview: 创建待办事项**

内容: {params.get('content', 'N/A')}
截止日期: {params.get('due_date', '无')}
截止时间: {params.get('due_time', '无')}
优先级: {params.get('priority', 'normal')}

确认创建此待办吗？"""
        elif tool_name == "write_email_draft":
            body = params.get('body', 'N/A')
            body_preview = body[:200] + '...' if len(body) > 200 else body
            cc = params.get('cc', '')
            bcc = params.get('bcc', '')
            preview_text = f"""**Preview: 创建邮件草稿**

收件人: {params.get('to', 'N/A')}
主题: {params.get('subject', 'N/A')}
抄送: {cc if cc else '（无）'}
密送: {bcc if bcc else '（无）'}

正文预览:
{body_preview}

确认创建此邮件草稿吗？（创建后可在 Outlook 草稿箱中找到并编辑发送）"""
        elif tool_name == "write_file":
            # write_file 特殊处理：避免显示过长的 content
            file_path = params.get('file_path', 'N/A')
            content = params.get('content', '')
            content_len = len(content)
            lines = content.split('\n') if content else []
            line_count = len(lines)

            # 如果内容超过 500 字符，只显示前 500 字符
            if len(content) > 500:
                content_preview = content[:500] + f"\n... (内容过长，已截断，共 {content_len} 字符)"
            elif content:
                content_preview = content[:200] + ("..." if len(content) > 200 else "")
            else:
                content_preview = "（空内容）"

            preview_text = f"""**Preview: 写入文件**

目标文件: {file_path}
内容统计: {line_count} 行，{content_len} 字符

内容预览:
{content_preview}

确认写入此文件吗？"""
        else:
            preview_text = f"""**Preview: {task.description}**

文件路径: {params.get('file_path', 'N/A')}

确认操作吗？"""
        
        return {"tool_result": preview_result, "response_text": preview_text}
    
    else:
        # 读操作直接执行
        task.status = TaskStatus.RUNNING
        
        try:
            result = tool.execute(**params) if hasattr(tool, 'execute') else {"success": False, "error": "Cannot execute"}
            
            task.result = result
            plan.intermediate_results = plan.intermediate_results or {}
            plan.intermediate_results[task.task_id] = result
            
            if result.get("success", False):
                task.status = TaskStatus.COMPLETED
                plan.completed_count += 1
                print(f"[SUCCESS] Tool executed successfully")
            else:
                task.status = TaskStatus.FAILED
                print(f"[FAILED] Tool failed: {result.get('error')}")
            
            return {"tool_result": result}
            
        except Exception as e:
            task.status = TaskStatus.FAILED
            task.result = {"error": str(e)}
            print(f"[ERROR] Tool error: {e}")
            return {"tool_result": task.result}


def _resolve_params(params: dict, plan: ExecutionPlan) -> dict:
    """解析参数中的依赖引用

    支持的格式：
    - {{task_id.field}} 或 {{task_id:field}} - 引用指定任务的指定字段
    - {task_id.field} 或 {task_id:field} - 同上，单大括号版本
    """
    import re

    resolved = {}

    for key, value in params.items():
        if isinstance(value, str):
            # 处理双大括号格式 {{task:field}} 和单大括号格式 {task:field}
            patterns = [
                (r'\{\{(\w+)[.:](\w+)\}\}', 4),  # {{task:field}} 或 {{task.field}}
                (r'\{(\w+)[.:](\w+)\}', 2),       # {task:field} 或 {task.field}
            ]

            for pattern, brace_count in patterns:
                matches = re.findall(pattern, value)

                for task_id, field in matches:
                    # 构建完整的占位符
                    if brace_count == 4:
                        p1 = '{{' + task_id + ':' + field + '}}'
                        p2 = '{{' + task_id + '.' + field + '}}'
                        placeholder = p1 if p1 in value else p2
                    else:
                        p1 = '{' + task_id + ':' + field + '}'
                        p2 = '{' + task_id + '.' + field + '}'
                        placeholder = p1 if p1 in value else p2

                    if placeholder not in value:
                        continue

                    # 获取任务结果
                    task_result = plan.intermediate_results.get(task_id)
                    if not task_result:
                        task_node = plan.task_dag.get(task_id)
                        if task_node:
                            task_result = task_node.result

                    if task_result and isinstance(task_result, dict):
                        if field == "result":
                            # result 字段：优先使用 response，否则用 weather_data、summary 等
                            field_value = (
                                task_result.get("response") or
                                task_result.get("weather_data") or
                                task_result.get("summary") or
                                str(task_result)
                            )
                        else:
                            field_value = task_result.get(field)

                        if field_value is not None:
                            value = value.replace(placeholder, str(field_value))

        resolved[key] = value

    return resolved


# =============================================================================
# Conditional Edge 函数
# =============================================================================

def should_continue(state: PlanningState) -> str:
    """决定下一步"""
    execution_plan = state.get("execution_plan")

    # 需要确认时结束
    if state.get("needs_confirmation") and state.get("pending_confirmation"):
        return "end"

    # 无计划时结束
    if not execution_plan:
        return "end"

    plan = _deserialize_plan(execution_plan)

    # 完成时结束
    if plan.is_complete():
        return "end"

    # 检查是否有需要重试的任务
    retry_tasks = [
        tid for tid, node in plan.task_dag.items()
        if node.status == TaskStatus.NEEDS_RETRY
    ]
    if retry_tasks:
        print(f"[SHOULD_CONTINUE] 发现 {len(retry_tasks)} 个重试任务: {retry_tasks}")
        return "executor"

    # 有可运行任务时继续执行
    if plan.get_next_runnable_tasks():
        return "executor"

    # 否则结束
    return "end"


# =============================================================================
# 序列化/反序列化
# =============================================================================

def _serialize_plan(plan: ExecutionPlan) -> dict:
    """序列化计划"""
    return {
        "task_dag": {k: v.to_dict() for k, v in plan.task_dag.items()},
        "root_tasks": plan.root_tasks,
        "execution_order": plan.execution_order,
        "completed_count": plan.completed_count,
        "total_count": plan.total_count,
        "pending_confirmation": plan.pending_confirmation,
        "intermediate_results": plan.intermediate_results,
        "step_count": plan.step_count
    }


def _deserialize_plan(plan_dict: dict) -> ExecutionPlan:
    """反序列化计划"""
    plan = ExecutionPlan()
    plan.root_tasks = plan_dict.get("root_tasks", [])
    plan.execution_order = plan_dict.get("execution_order", [])
    plan.completed_count = plan_dict.get("completed_count", 0)
    plan.total_count = plan_dict.get("total_count", 0)
    plan.pending_confirmation = plan_dict.get("pending_confirmation")
    plan.intermediate_results = plan_dict.get("intermediate_results", {})
    plan.step_count = plan_dict.get("step_count", 0)
    
    for task_id, node_data in plan_dict.get("task_dag", {}).items():
        try:
            task_type = TaskType(node_data.get("task_type", "read"))
        except ValueError:
            task_type = TaskType.READ
        
        try:
            status = TaskStatus(node_data.get("status", "pending"))
        except ValueError:
            status = TaskStatus.PENDING
        
        node = TaskNode(
            task_id=task_id,
            task_type=task_type,
            tool_name=node_data.get("tool_name", ""),
            params=node_data.get("params", {}),
            depends_on=node_data.get("depends_on", []),
            description=node_data.get("description", ""),
            status=status,
            result=node_data.get("result"),
            condition_result=node_data.get("condition_result")
        )
        plan.task_dag[task_id] = node
    
    return plan


# =============================================================================
# 创建 Agent
# =============================================================================

def create_planner_agent() -> StateGraph:
    """创建复杂规划 Agent"""
    graph = StateGraph(PlanningState)
    
    graph.add_node("planner", planner_node)
    graph.add_node("executor", executor_node)
    
    graph.set_entry_point("planner")
    
    # Planner 完成后进入 Executor
    graph.add_edge("planner", "executor")
    
    # Conditional edge: 决定是否继续
    graph.add_conditional_edges(
        "executor",
        should_continue,
        {
            "executor": "executor",
            "end": END
        }
    )
    
    return graph


planner_agent_graph = create_planner_agent().compile()


# =============================================================================
# 运行函数
# =============================================================================

def run_planner_agent(user_input: str, history: list = None, cached_tool_results: dict = None) -> dict:
    """运行复杂规划 Agent
    
    Args:
        user_input: 用户输入
        history: 对话历史
        cached_tool_results: 跨请求的工具结果缓存（如 browser_fetch_last）
    """
    # ========== [DEBUG] Planner 模式触发调试 ==========
    import sys
    print("\n" + "=" * 60)
    print("[DEBUG] ⚡ Planner Agent 被触发！")
    print("=" * 60)
    print(f"用户输入: {user_input}")
    print(f"历史消息数: {len(history) if history else 0}")
    print(f"缓存的工具结果数: {len(cached_tool_results) if cached_tool_results else 0}")
    if cached_tool_results:
        print(f"缓存的缓存键: {list(cached_tool_results.keys())}")
    print("=" * 60)
    # ========== [DEBUG] 结束 ==========
    
    initial_state: PlanningState = {
        "messages": [],
        "user_input": user_input,
        "execution_plan": None,
        "current_task_id": None,
        "tool_result": None,
        "response_text": None,
        "pending_confirmation": None,
        "needs_confirmation": False,
        "plan_description": None,
        "intermediate_results": {},
        "error": None,
        "step_count": 0,
        "cached_tool_results": cached_tool_results or {},  # 跨请求的工具结果缓存
        # 自我反思与纠错相关状态
        "reflection_enabled": True,  # 默认启用反思机制
        "reflection_result": None,
        "retry_count": 0,
        "max_retries": 2,  # 最多重试2次
        "reflection_history": [],  # 反思历史记录
        "failed_tasks_needing_review": []  # 需要审查的失败任务
    }
    
    if history:
        for msg in history:
            if msg.get("role") == "user":
                initial_state["messages"].append({"role": "user", "content": msg["content"]})
            elif msg.get("role") == "assistant":
                initial_state["messages"].append({"role": "assistant", "content": msg["content"]})
    
    result = planner_agent_graph.invoke(initial_state)
    
    # ========== [DEBUG] Planner 结果调试 ==========
    response = result.get("response_text") or ""
    needs_conf = result.get("needs_confirmation", False)
    pending_conf = result.get("pending_confirmation")
    has_plan = result.get("execution_plan") is not None

    # 如果没有响应文本但有执行计划，尝试生成一个
    if not response and has_plan:
        plan_dict = result.get("execution_plan", {})
        tasks = plan_dict.get("task_dag", {})
        completed = [t for t in tasks.values() if t.get("status") == "completed"]
        failed = [t for t in tasks.values() if t.get("status") == "failed"]
        
        # 特别处理 browser_fetch 任务
        for task_id, task in tasks.items():
            if task.get("tool_name") == "browser_fetch":
                task_result = task.get("result")
                if task_result and task_result.get("success"):
                    title = task_result.get("title", "")
                    content = task_result.get("content", "") or task_result.get("raw_text", "")
                    if content:
                        from .llm_config import get_llm
                        try:
                            summary_prompt = f"""请总结以下网页内容的主要信息：

标题：{title}

内容：
{content[:8000]}

请用简洁的语言总结页面的主要内容，包括：
1. 主题是什么
2. 主要讲述了哪些要点
3. 适合的读者群体（如有）"""
                            
                            active_llm = get_llm()
                            llm_response = active_llm.invoke([
                                SystemMessage(content=summary_prompt)
                            ])
                            response = llm_response.content if hasattr(llm_response, 'content') else ""
                            
                            if response:
                                print(f"[BROWSER_FETCH] Generated summary ({len(response)} chars)")
                                break
                            else:
                                response = f"【{title}】\n\n{content[:1000]}..."
                                break
                        except Exception as e:
                            response = f"【{title}】\n\n{content[:1000]}..."
                            break
                    else:
                        response = "网页内容为空"
                        break
                else:
                    error = task_result.get("error", "未知错误") if task_result else "未知错误"
                    response = f"无法访问该网页: {error}"
                    break
        
        # 如果还没有响应，使用默认逻辑
        if not response:
            if failed:
                error_tasks = [t.get("description", "未知任务") for t in failed]
                response = f"部分任务执行失败: {', '.join(error_tasks)}"
            elif completed:
                response = f"已完成全部 {len(completed)} 个任务"

    # 最终保底响应
    if not response:
        response = "任务执行完成"

    print("\n" + "=" * 60)
    print("[DEBUG] Planner Agent 执行结果:")
    print("=" * 60)
    print(f"响应内容 (前200字符): {response[:200]}")
    print(f"需要确认: {needs_conf}")
    print(f"待确认数据: {'有' if pending_conf else '无'}")
    print(f"执行计划: {'有' if has_plan else '无'}")
    print("=" * 60)
    # ========== [DEBUG] 结束 ==========

    return {
        "response": response,
        "execution_plan": result.get("execution_plan"),
        "needs_confirmation": result.get("needs_confirmation", False),
        "pending_confirmation": result.get("pending_confirmation"),
        "error": result.get("error"),
        "plan_description": result.get("plan_description")
    }


def handle_confirmation_result(pending_confirmation: dict, confirmed: bool) -> dict:
    """处理确认结果"""
    if not confirmed:
        return {"success": True, "message": "Cancelled", "preview": pending_confirmation.get("preview")}
    
    tool_name = pending_confirmation.get("tool_name")
    args = pending_confirmation.get("args", {})
    
    # 导入实际工具
    from .tools import (
        TOOL_MAP, 
        create_calendar_event_tool,
        update_calendar_event_tool,
        create_todo_tool,
        write_file_tool,
        write_email_draft_tool
    )
    
    # 实际工具映射
    ACTUAL_TOOL_MAP = {
        "create_outlook_calendar_event": create_calendar_event_tool,
        "update_outlook_calendar_event": update_calendar_event_tool,
        "create_todo": create_todo_tool,
        "write_file": write_file_tool,
        "write_email_draft": write_email_draft_tool,
    }
    
    tool = ACTUAL_TOOL_MAP.get(tool_name)
    
    if not tool:
        tool = TOOL_MAP.get(tool_name)
    
    if not tool:
        return {"success": False, "error": f"Unknown tool: {tool_name}"}
    
    # 日历工具需要解析自然语言时间
    if tool_name in ["create_outlook_calendar_event", "update_outlook_calendar_event"]:
        from datetime import datetime, timedelta
        import re
        
        def parse_natural_time(time_str: str) -> str:
            """将自然语言时间转换为 YYYY-MM-DD HH:MM:SS 格式"""
            if not time_str:
                return time_str
            
            # 如果已经是标准格式，直接返回
            if re.match(r'\d{4}-\d{2}-\d{2}', time_str):
                return time_str
            
            now = datetime.now()
            
            # 解析 "明天早上9点" -> 明天 9:00
            if "明天" in time_str:
                target_date = now + timedelta(days=1)
                time_str = time_str.replace("明天", "").strip()
            else:
                target_date = now
            
            # 提取时间
            hour, minute = 0, 0
            # 匹配 "早上9点", "上午9点", "9点", "9:30" 等
            match = re.search(r'(\d{1,2})[:\：]?(\d{0,2})', time_str)
            if match:
                hour = int(match.group(1))
                minute = int(match.group(2)) if match.group(2) else 0
            
            # 下午时间调整
            if "下午" in time_str or "晚上" in time_str:
                if hour < 12:
                    hour += 12
            
            return target_date.strftime(f"%Y-%m-%d {hour:02d}:{minute:02d}:00")
        
        # 解析时间参数
        if "start" in args and args["start"]:
            args["start"] = parse_natural_time(args["start"])
        if "end" in args and args["end"]:
            args["end"] = parse_natural_time(args["end"])
    
    try:
        result = tool.execute(**args) if hasattr(tool, 'execute') else {"success": False, "error": "Cannot execute"}
        result["preview"] = pending_confirmation.get("preview")
        return result
    except Exception as e:
        return {"success": False, "error": str(e)}


def continue_after_confirmation(
    pending_confirmation: dict,
    confirmation_result: dict,
    previous_state: dict
) -> dict:
    """确认后继续执行"""
    execution_plan = previous_state.get("execution_plan")
    
    if not execution_plan:
        return {
            **previous_state,
            "response_text": "Done" if confirmation_result.get("success") else f"Failed: {confirmation_result.get('error')}",
            "needs_confirmation": False,
            "pending_confirmation": None
        }
    
    plan = _deserialize_plan(execution_plan)
    task_id = pending_confirmation.get("task_id")
    task = None
    
    if task_id and task_id in plan.task_dag:
        task = plan.task_dag[task_id]
        
        if confirmation_result.get("success"):
            task.status = TaskStatus.COMPLETED
            task.result = confirmation_result
            plan.completed_count += 1
        else:
            task.status = TaskStatus.FAILED
            task.result = {"error": confirmation_result.get("error")}
    
    plan.pending_confirmation = None
    
    # 保存 confirmation_result 到 intermediate_results
    if task_id:
        plan.intermediate_results = plan.intermediate_results or {}
        plan.intermediate_results[task_id] = confirmation_result
    
    # 如果是日历创建/更新操作，提取并保存 entry_id 到 intermediate_results
    # 这样后续请求可以通过引用访问到日历事件 ID
    tool_name = pending_confirmation.get("tool_name", "")
    entry_id = confirmation_result.get("entry_id")
    
    if entry_id and "calendar" in tool_name.lower():
        # 保存 entry_id 到一个可引用的 key
        plan.intermediate_results["_last_calendar_event_id"] = entry_id
        print(f"[CALENDAR] 保存 event_id 到 intermediate_results: {entry_id[:30]}...")
    
    print(f"\n[CONFIRMATION HANDLED] Task [{task_id}] status: {task.status if task else 'N/A'}")
    print(f"Completed: {plan.completed_count}/{plan.total_count}")
    
    # 继续执行后续任务
    runnable = plan.get_next_runnable_tasks()
    print(f"[CONTINUE] Next runnable tasks: {runnable}")
    
    if runnable:
        # 执行下一个任务
        next_task_id = runnable[0]
        next_task = plan.task_dag[next_task_id]
        next_task.status = TaskStatus.RUNNING
        
        print(f"[CONTINUE] Executing: [{next_task_id}] {next_task.description}")
        
        # 根据任务类型执行
        cached_tool_results = previous_state.get("cached_tool_results", {})
        
        if next_task.task_type == TaskType.CONDITION:
            result = _execute_condition(plan, next_task)
        elif next_task.task_type == TaskType.SYNTHESIZE:
            result = _execute_synthesize(previous_state, plan, next_task)
        elif next_task.task_type == TaskType.LLM_JUDGE:
            result = _execute_llm_judge(previous_state, plan, next_task, cached_results=cached_tool_results)
        else:
            result = _execute_tool(plan, next_task)
        
        # 检查是否需要新的确认
        if plan.pending_confirmation:
            return {
                **previous_state,
                "execution_plan": _serialize_plan(plan),
                "tool_result": result.get("tool_result"),
                "current_task_id": next_task_id,
                "needs_confirmation": True,
                "pending_confirmation": plan.pending_confirmation,
                "response_text": result.get("response_text"),
                "step_count": plan.step_count
            }
        
        # 如果计划完成，生成最终响应
        if plan.is_complete():
            final_response = _generate_final_response(plan, previous_state)
            return {
                **previous_state,
                "execution_plan": _serialize_plan(plan),
                "tool_result": result.get("tool_result"),
                "response_text": final_response,
                "needs_confirmation": False,
                "pending_confirmation": None,
                "step_count": plan.step_count
            }
        
        # 返回中间状态，继续执行
        return {
            **previous_state,
            "execution_plan": _serialize_plan(plan),
            "tool_result": result.get("tool_result"),
            "current_task_id": next_task_id,
            "needs_confirmation": False,
            "pending_confirmation": None,
            "response_text": result.get("response_text"),
            "step_count": plan.step_count
        }
    
    # 没有更多任务，生成最终响应
    final_response = _generate_final_response(plan, previous_state)
    return {
        **previous_state,
        "execution_plan": _serialize_plan(plan),
        "intermediate_results": plan.intermediate_results,
        "response_text": final_response,
        "needs_confirmation": False,
        "pending_confirmation": None,
        "step_count": plan.step_count
    }

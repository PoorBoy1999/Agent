"""
增强路由器模块 - 复杂度判断与参数完整性检查

功能：
1. 使用单一 LLM 调用同时判断任务复杂度和参数完整性
2. 当参数缺失时生成追问内容
3. 支持最多2次追问循环
4. 参数完整后智能路由到合适的 Agent

工具参数规范定义在此模块中，供 Router 使用。
"""

from enum import Enum
from typing import Optional, Literal
from pydantic import BaseModel, Field
import re
import threading

# 超时控制
ROUTER_TIMEOUT = 60  # Router LLM 调用超时（秒）- CPU 推理较慢，增加到60秒
llm_lock = threading.Lock()

from .llm_config import router_llm


class ComplexityLevel(str, Enum):
    """复杂度级别"""
    SIMPLE = "simple"
    COMPLEX = "complex"


class ParameterCompleteness(str, Enum):
    """参数完整性状态"""
    READY = "ready"           # 参数完整，可以执行
    NEED_CLARIFY = "need_clarify"  # 需要追问


class RouterResult(BaseModel):
    """增强路由结果"""
    complexity: ComplexityLevel
    complexity_reasoning: str = Field(description="复杂度判断理由")
    complexity_confidence: float = Field(description="复杂度置信度 0-1")
    completeness: ParameterCompleteness
    completeness_reasoning: str = Field(default="", description="参数完整性判断理由")
    recommended_agent: str = Field(description="推荐使用的 Agent: 'simple' 或 'planner'")
    # 解析出的意图
    parsed_intent: dict = Field(default_factory=dict, description="从用户输入解析的工具和参数")
    # 追问内容（当 completeness == need_clarify 时）
    clarification_question: str = Field(default="", description="追问内容")
    # 缺失的参数列表
    missing_params: list[str] = Field(default_factory=list, description="缺失的参数列表")


# ============================================================================
# 工具参数规范定义
# ============================================================================

TOOL_PARAM_SPECS = {
    "read_file": {
        "required_params": ["file_path"],
        "param_descriptions": {
            "file_path": "文件路径（完整路径或相对于 C:\\Users\\Ron\\Desktop）"
        }
    },
    "write_file": {
        "required_params": ["file_path", "content"],
        "param_descriptions": {
            "file_path": "目标文件路径",
            "content": "要写入的内容"
        }
    },
    "read_email": {
        "required_params": [],
        "optional_params": ["from_contains", "subject_contains", "limit", "unread_only", "mailbox", "date_since"],
        "param_descriptions": {
            "from_contains": "发件人筛选关键词（如发件人名称或邮箱域名）",
            "subject_contains": "邮件主题筛选关键词",
            "limit": "返回邮件数量上限（默认10）",
            "unread_only": "是否只返回未读邮件",
            "mailbox": "邮箱文件夹（如 INBOX）",
            "date_since": "起始日期（格式如 '25-May-2026'）"
        },
        "note": "所有参数都是可选的，不提供参数则返回收件箱最新邮件"
    },
    "write_email_draft": {
        "required_params": ["to", "body"],
        "param_descriptions": {
            "to": "收件人邮箱",
            "body": "邮件正文"
        }
    },
    "list_outlook_calendar_events": {
        "required_params": [],
        "optional_params": ["days", "start_date", "timezone"],
        "param_descriptions": {
            "days": "查询天数范围（默认7）",
            "start_date": "起始日期（格式如 '2026-05-30'）",
            "timezone": "时区（如 'Asia/Shanghai'）"
        },
        "note": "所有参数都是可选的，不提供参数则返回未来7天的日程"
    },
    "create_calendar_event": {
        "required_params": ["subject", "start", "end"],
        "param_descriptions": {
            "subject": "日程标题",
            "start": "开始时间（格式如 '2026-05-30 14:00:00'）",
            "end": "结束时间（格式如 '2026-05-30 15:00:00'）"
        },
        "optional_params": ["all_day", "location", "body"],
        "optional_descriptions": {
            "all_day": "是否全天日程",
            "location": "地点",
            "body": "日程描述"
        }
    },
    "update_calendar_event": {
        "required_params": [],
        "optional_params": ["entry_id", "subject", "start", "end", "location", "all_day", "body"],
        "param_descriptions": {
            "entry_id": "日程唯一标识（从 list_outlook_calendar_events 获取）",
            "subject": "日程标题",
            "start": "开始时间",
            "end": "结束时间",
            "location": "地点",
            "all_day": "是否全天",
            "body": "日程描述"
        },
        "note": "entry_id 是可选的但建议提供，其他参数都是可选的"
    },
    "browser_fetch": {
        "required_params": [],
        "optional_params": ["url", "search_term"],
        "param_descriptions": {
            "url": "要访问的网页 URL",
            "search_term": "搜索关键词"
        },
        "note": "参数都是可选的，至少提供一个"
    },
    "create_todo": {
        "required_params": ["content"],
        "param_descriptions": {
            "content": "待办内容"
        },
        "optional_params": ["due_date", "due_time", "priority"],
        "optional_descriptions": {
            "due_date": "截止日期",
            "due_time": "截止时间",
            "priority": "优先级（high/medium/low）"
        }
    },
    "complete_todo": {
        "required_params": ["todo_id"],
        "param_descriptions": {
            "todo_id": "待办 ID"
        }
    },
    "delete_todo": {
        "required_params": ["todo_id"],
        "param_descriptions": {
            "todo_id": "待办 ID"
        }
    },
    "weather_query": {
        "required_params": ["city"],
        "param_descriptions": {
            "city": "城市名称"
        },
        "optional_params": ["forecast_days", "date"],
        "optional_descriptions": {
            "forecast_days": "预报天数（默认3）",
            "date": "查询日期（格式如 '2026-05-30'）"
        }
    }
}


# ============================================================================
# 增强版 Router 提示词
# ============================================================================

ENHANCED_ROUTER_PROMPT_TEMPLATE = """你是一个任务分析专家。请同时完成两个任务：
1. 分析用户输入的任务复杂度（简单/复杂）
2. 提取工具参数：能提取就提取，提取不到才追问

【重要：当前日期】
今天是 {today_date}。计算相对日期（如"明天"、"后天"、"下周三"）时必须基于这个日期。

## 一、复杂度判断

【Complex（复杂）任务】：涉及多个工具组合、有依赖关系、需要条件判断。

【Simple（简单）任务】：单一工具操作、明确的简单指令。

## 二、参数提取（核心任务）

你的核心任务是：**从用户输入和上下文信息中，尽可能提取工具所需的参数**。

【工具参数规范】：
{tool_specs}

【重要规则 - missing_params 必须来自已识别工具】：
1. **missing_params 只能包含已识别工具的必填参数**
2. 如果识别到工具 A 和 B，则 missing_params 只能包含 A 和 B 的必填参数
3. **禁止输出不属于已识别工具的参数**
   - 例如：识别到 read_email + create_calendar_event，则 missing_params 只能是 create_calendar_event 的必填参数 [subject, start, end]
   - 绝对不能输出 write_email_draft 的 to、body 等参数

【参数提取流程】：
对每个工具的每个必填参数，依次尝试以下来源：

1. **从用户输入中直接提取**
   - 扫描用户输入，找出与参数名对应的值
   - 例如：city参数 → 找"上海"、"北京"等城市名
   - 例如：body参数 → 找引号内、冒号后的内容

2. **从会话历史中提取**
   - 之前对话中已提供的信息视为已提取

3. **从工具组合关系中推断**
   - 如果多个工具配合使用，前一个工具的结果可能填充后一个工具的参数
   - 这种情况视为"将由其他工具执行结果填充"

4. **如果以上都没有，才标记为缺失**

【追问原则】：
只有当参数在所有来源都找不到时，才标记为缺失并追问。
不要因为参数分散在不同来源就追问，应该尽量合并。

【常见场景的参数推断】：
- "找/搜索一篇关于XX的文章" → browser_fetch 的 search_term='XX'，不需要 url
- "访问某个网页" → browser_fetch 的 url='具体链接'
- "查收件箱里来自XX的邮件" → read_email 的 from_contains='XX'

## 三、输出格式

```json
{{
    "complexity": "simple"或"complex",
    "complexity_reasoning": "判断理由",
    "complexity_confidence": 0.0到1.0之间的置信度,
    "completeness": "ready"或"need_clarify",
    "completeness_reasoning": "参数完整性判断理由，要说明每个参数从哪里提取",
    "recommended_agent": "simple"或"planner",
    "parsed_intent": {{
        "tool_name": "工具名称，多个工具用 + 连接",
        "params": {{"已提取的参数": "参数值"}}
    }},
    "missing_params": ["缺失的必填参数列表"],
    "clarification_question": "追问内容（仅当 completeness == need_clarify 时）"
}}
```

【判断示例】：

示例1 - 参数完整：
- 输入："帮我读取 C:\\Users\\Ron\\Desktop\\test.txt"
- 输出：
```json
{{
    "complexity": "simple",
    "complexity_reasoning": "单一文件读取操作",
    "complexity_confidence": 0.95,
    "completeness": "ready",
    "completeness_reasoning": "read_file 的 file_path='C:\\Users\\Ron\\Desktop\\test.txt' 从用户输入直接提取",
    "recommended_agent": "simple",
    "parsed_intent": {{"tool_name": "read_file", "params": {{"file_path": "C:\\\\Users\\\\Ron\\\\Desktop\\\\test.txt"}}}},
    "missing_params": [],
    "clarification_question": ""
}}
```

示例2 - 需要追问：
- 输入："帮我发邮件"
- 输出：
```json
{{
    "complexity": "simple",
    "complexity_reasoning": "单一邮件操作",
    "complexity_confidence": 0.9,
    "completeness": "need_clarify",
    "completeness_reasoning": "write_email_draft 需要 to 和 body，但用户输入中没有提供",
    "recommended_agent": "simple",
    "parsed_intent": {{"tool_name": "write_email_draft", "params": {{}}}},
    "missing_params": ["to", "body"],
    "clarification_question": "好的，我来帮您写邮件。请告诉我收件人邮箱和邮件内容。"
}}
```

示例3 - 参数部分缺失：
- 输入："帮我给张三发邮件说下午开会"
- 输出：
```json
{{
    "complexity": "simple",
    "complexity_reasoning": "单一邮件操作",
    "complexity_confidence": 0.85,
    "completeness": "need_clarify",
    "completeness_reasoning": "write_email_draft 的 body='下午开会' 从用户输入提取；to='张三' 提取到人名但缺少邮箱，需要追问",
    "recommended_agent": "simple",
    "parsed_intent": {{"tool_name": "write_email_draft", "params": {{"body": "下午开会"}}}},
    "missing_params": ["to"],
    "clarification_question": "好的，我来帮您发邮件。请告诉我张三的邮箱地址。"
}}
```

示例4 - 复杂任务，参数从多个来源提取：
- 输入："查询上海后天有没有雨，没雨的话就给我老板发邮件告知他：'可以出门，后天没雨'"
- 分析：
  - 工具组合：weather_query + write_email_draft
  - weather_query.city='上海' → 从用户输入提取
  - write_email_draft.body='可以出门，后天没雨' → 从引号内容提取
  - write_email_draft.to → 用户输入中没有邮箱，标记为缺失
- 输出：
```json
{{
    "complexity": "complex",
    "complexity_reasoning": "涉及天气查询和条件发邮件两个工具，有依赖关系",
    "complexity_confidence": 0.95,
    "completeness": "need_clarify",
    "completeness_reasoning": "weather_query.city='上海' 从输入提取；write_email_draft.body='可以出门，后天没雨' 从引号内容提取；write_email_draft.to 无法提取，需要追问",
    "recommended_agent": "planner",
    "parsed_intent": {{"tool_name": "weather_query + write_email_draft", "params": {{"city": "上海", "body": "可以出门，后天没雨"}}}},
    "missing_params": ["to"],
    "clarification_question": "好的，我来帮您查询上海后天的天气。请告诉我收件人的邮箱地址。"
}}
```

示例5 - 识别到的工具没有缺失参数：
- 输入："查收件箱里所有来自 Tesla 的邮件，如果有提到'招聘'的，就在下周五下午安排一个'招聘会'"
- 分析：
  - 识别工具：read_email + create_calendar_event
  - read_email（无必填参数）：
    - from_contains='Tesla' → 从用户输入提取
    - subject_contains='招聘' → 从用户输入提取
  - create_calendar_event（必填：subject, start, end）：
    - subject='招聘会' → 从用户输入提取
    - start='下周五下午' → 从用户输入结合当前日期推断
    - end='下周五下午+时长' → 从用户输入推断
  - 两个工具的必填参数都已提取，无需追问
- 输出：
```json
{{
    "complexity": "complex",
    "complexity_reasoning": "涉及读取邮件和创建日程两个工具组合，有条件依赖关系",
    "complexity_confidence": 0.95,
    "completeness": "ready",
    "completeness_reasoning": "read_email 无必填参数，from_contains='Tesla' 和 subject_contains='招聘' 从输入提取；create_calendar_event 的 subject='招聘会'、start='下周五下午'、end='下周五下午+45分钟' 从输入提取。所有必填参数已完整。",
    "recommended_agent": "planner",
    "parsed_intent": {{"tool_name": "read_email + create_calendar_event", "params": {{"from_contains": "Tesla", "subject_contains": "招聘", "subject": "招聘会", "start": "下周五下午", "end": "下周五下午45分钟"}}}},
    "missing_params": [],
    "clarification_question": ""
}}
```

示例6 - "找文章"类请求不需要追问：
- 输入："帮我找一篇关于'C#中list'的文章"
- 分析：
  - 识别工具：browser_fetch
  - browser_fetch 无必填参数
  - "找文章"意图 → search_term='C#中list' 从用户输入提取
  - 不需要 url，可以直接搜索
- 输出：
```json
{{
    "complexity": "simple",
    "complexity_reasoning": "单一搜索请求",
    "complexity_confidence": 0.95,
    "completeness": "ready",
    "completeness_reasoning": "browser_fetch 无必填参数；'找文章'意图 → search_term='C#中list' 从输入提取。所有参数已完整。",
    "recommended_agent": "simple",
    "parsed_intent": {{"tool_name": "browser_fetch", "params": {{"search_term": "C#中list"}}}},
    "missing_params": [],
    "clarification_question": ""
}}
```

请开始分析用户输入："""


def _build_tool_specs_text() -> str:
    """构建工具参数规范文本"""
    specs_lines = []
    for tool_name, spec in TOOL_PARAM_SPECS.items():
        required = spec.get("required_params", [])
        optional = spec.get("optional_params", [])
        
        if required:
            req_str = ", ".join(required)
            specs_lines.append(f"- {tool_name}: 必填参数 [{req_str}]")
        else:
            specs_lines.append(f"- {tool_name}: 无必填参数（所有参数都是可选的）")
        
        # 必填参数描述
        for param in required:
            desc = spec.get("param_descriptions", {}).get(param, "")
            specs_lines.append(f"    - {param}: {desc}")
        
        # 可选参数描述
        for param in optional:
            desc = spec.get("optional_descriptions", {}).get(param) or spec.get("param_descriptions", {}).get(param, "")
            specs_lines.append(f"    [可选] {param}: {desc}")
        
        if "note" in spec:
            specs_lines.append(f"    注: {spec['note']}")
    return "\n".join(specs_lines)


def _build_router_prompt(tool_specs: str = None) -> str:
    """构建包含当前日期的 Router 提示词

    Args:
        tool_specs: 工具参数规范文本，如果不提供则从默认配置获取
    """
    from datetime import date
    today = date.today().strftime("%Y-%m-%d")

    if tool_specs is None:
        tool_specs = _build_tool_specs_text()

    return ENHANCED_ROUTER_PROMPT_TEMPLATE.format(
        today_date=today,
        tool_specs=tool_specs
    )


# ============================================================================
# 主路由函数
# ============================================================================

def classify_complexity(user_input: str, collected_params: dict = None, history: list = None) -> RouterResult:
    """
    使用 LLM 同时判断复杂度、参数完整性，并生成追问（带超时控制）

    Args:
        user_input: 用户输入文本
        collected_params: 已收集的参数字典（可选），用于告诉 LLM 哪些参数已经提供
        history: 会话历史列表（可选），用于检查参数是否在历史中已提供
            格式: [{"role": "user"/"assistant", "content": "..."}]

    Returns:
        RouterResult: 包含复杂度级别、参数完整性状态和追问内容
    """
    from langchain_core.messages import HumanMessage, SystemMessage
    import time

    print("\n" + "=" * 60)
    print("[ENHANCED ROUTER] 分析任务")
    print("=" * 60)
    print(f"用户输入: {user_input}")

    # 如果有已收集的参数，将其添加到用户输入的上下文中
    collected_context = ""
    if collected_params:
        params_list = []
        for key, value in collected_params.items():
            if value:
                params_list.append(f"- {key}: {value}")
        if params_list:
            collected_context = "\n\n【已提供的参数】（请不要再次询问这些参数）\n" + "\n".join(params_list)

    # 构建会话历史上下文
    history_context = ""
    if history and len(history) > 0:
        history_lines = []
        for i, msg in enumerate(history[-6:]):  # 只取最近6条历史
            role = "用户" if msg.get("role") == "user" else "助手"
            content = msg.get("content", "")[:200]  # 限制每条历史的长度
            if content:
                history_lines.append(f"- [{role}]: {content}")
        if history_lines:
            history_context = "\n\n【会话历史】（请检查这些内容，可能包含已提供的参数）\n" + "\n".join(history_lines)
            print(f"[ENHANCED ROUTER] 已加载 {len(history_lines)} 条会话历史")

    # 构建提示词（包含当前日期）
    tool_specs_text = _build_tool_specs_text()
    prompt = _build_router_prompt(tool_specs=tool_specs_text)
    prompt += collected_context
    prompt += history_context

    # 尝试 LLM 分析（带超时）
    result_container = [None]
    error_container = [None]

    def llm_call():
        try:
            with llm_lock:
                from .llm_config import get_router_llm
                router_llm_instance = get_router_llm()
                messages = [
                    SystemMessage(content=prompt),
                    HumanMessage(content=f"用户输入: {user_input}")
                ]
                response = router_llm_instance.invoke(messages)
                response_content = response.content if hasattr(response, 'content') else str(response)

                print(f"\n[ENHANCED ROUTER] LLM 原始响应:")
                print("-" * 40)
                print(response_content)
                print("-" * 40)

                result_container[0] = _parse_enhanced_router_response(response_content)
        except Exception as e:
            error_container[0] = e

# 启动线程执行 LLM 调用
    llm_thread = threading.Thread(target=llm_call)
    llm_thread.daemon = True
    llm_thread.start()
    
    # 等待结果或超时（优化检查间隔）
    start_time = time.time()
    check_interval = 0.1  # 100ms 检查间隔，更快响应
    while llm_thread.is_alive():
        if time.time() - start_time > ROUTER_TIMEOUT:
            print(f"[ENHANCED ROUTER] LLM 调用超时({ROUTER_TIMEOUT}秒)，使用备用方案")
            print("=" * 60)
            return _fallback_classify(user_input)
        llm_thread.join(timeout=check_interval)

    # 检查结果
    if error_container[0]:
        print(f"[ENHANCED ROUTER] LLM 调用出错: {error_container[0]}")
        print("[ENHANCED ROUTER] 使用备用方案...")
        return _fallback_classify(user_input)

    result = result_container[0]
    if result is None:
        print("[ENHANCED ROUTER] 未获取到结果，使用备用方案...")
        return _fallback_classify(user_input)

    print(f"\n[ENHANCED ROUTER] 分析结果:")
    print(f"  复杂度: {result.complexity.value} ({result.complexity_reasoning})")
    print(f"  置信度: {result.complexity_confidence:.2f}")
    print(f"  参数完整性: {result.completeness.value}")
    if result.completeness == ParameterCompleteness.NEED_CLARIFY:
        print(f"  缺失参数: {result.missing_params}")
        print(f"  追问内容: {result.clarification_question}")
    print(f"  推荐 Agent: {result.recommended_agent}")
    print("=" * 60)

    return result


def _parse_enhanced_router_response(response_content: str) -> RouterResult:
    """解析增强版 LLM 的 JSON 响应"""
    import json
    import re

    # 尝试提取 JSON
    json_pattern = r'```(?:json)?\s*([\s\S]*?)```'
    match = re.search(json_pattern, response_content)

    if match:
        json_str = match.group(1).strip()
    else:
        # 直接查找 JSON 对象
        json_pattern2 = r'\{[\s\S]*"complexity"[\s\S]*"completeness"[\s\S]*\}'
        match2 = re.search(json_pattern2, response_content)
        if match2:
            json_str = match2.group(0)
        else:
            json_str = response_content.strip()

    try:
        data = json.loads(json_str)
        complexity_str = data.get("complexity", "simple").lower()
        complexity = ComplexityLevel.COMPLEX if complexity_str == "complex" else ComplexityLevel.SIMPLE

        completeness_str = data.get("completeness", "ready").lower()
        completeness = ParameterCompleteness.NEED_CLARIFY if completeness_str == "need_clarify" else ParameterCompleteness.READY

        # 解析 parsed_intent
        parsed_intent = data.get("parsed_intent", {})
        if isinstance(parsed_intent, dict):
            # 确保格式正确
            pass
        else:
            parsed_intent = {}

        # 解析 missing_params
        missing_params = data.get("missing_params", [])
        if not isinstance(missing_params, list):
            missing_params = []

        return RouterResult(
            complexity=complexity,
            complexity_reasoning=data.get("complexity_reasoning", ""),
            complexity_confidence=float(data.get("complexity_confidence", 0.5)),
            completeness=completeness,
            completeness_reasoning=data.get("completeness_reasoning", ""),
            recommended_agent=data.get("recommended_agent", "simple"),
            parsed_intent=parsed_intent,
            missing_params=missing_params,
            clarification_question=data.get("clarification_question", "")
        )
    except (json.JSONDecodeError, KeyError, ValueError) as e:
        print(f"[ENHANCED ROUTER] JSON 解析失败: {e}")
        print(f"[ENHANCED ROUTER] 尝试解析内容: {json_str[:200]}")
        # 默认返回简单模式
        return _create_default_result()


def _create_default_result() -> RouterResult:
    """创建默认结果（当解析失败时）"""
    return RouterResult(
        complexity=ComplexityLevel.SIMPLE,
        complexity_reasoning="JSON解析失败，默认简单模式",
        complexity_confidence=0.0,
        completeness=ParameterCompleteness.READY,
        completeness_reasoning="解析失败，假设参数完整",
        recommended_agent="simple",
        parsed_intent={},
        missing_params=[],
        clarification_question=""
    )


def _fallback_classify(user_input: str) -> RouterResult:
    """
    备用分类函数 - 当 LLM 不可用时使用规则匹配

    Args:
        user_input: 用户输入

    Returns:
        RouterResult: 基于规则的分类结果
    """
    text = user_input.lower()

    # 复杂信号模式
    complex_patterns = [
        (r'先.*然后|首先.*接着|先.*再|先后', "连接词序列"),
        (r'如果.*就|假如.*就|要是.*就|当.*时', "条件判断"),
        (r'根据.*结果|基于.*结果|依赖.*结果', "依赖关系"),
        (r'如果有|是否.*有|检查.*是否', "检查型条件"),
        (r'并且|同时|而且', "并列操作"),
    ]

    # 工具关键词
    tool_keywords_map = {
        "read_file": ["读", "查看", "打开"],
        "write_file": ["写", "保存", "创建文件"],
        "write_email_draft": ["写邮件", "发邮件", "创建邮件"],
        "create_outlook_calendar_event": ["创建日程", "新建日程", "添加日程", "安排会议"],
        "update_outlook_calendar_event": ["修改日程", "更新日程", "改时间"],
        "browser_fetch": ["搜索", "打开网页", "访问"],
        "create_todo": ["创建待办", "新建待办", "添加待办"],
        "complete_todo": ["完成待办", "标记完成"],
        "delete_todo": ["删除待办"],
        "weather_query": ["天气", "查天气"]
    }

    complex_score = 0
    complex_reasons = []

    for pattern, reason in complex_patterns:
        if re.search(pattern, text):
            complex_score += 2
            complex_reasons.append(reason)

    # 检测工具
    detected_tool = None
    for tool_name, keywords in tool_keywords_map.items():
        for kw in keywords:
            if kw in text:
                detected_tool = tool_name
                break
        if detected_tool:
            break

    # 检测多工具
    tool_count = sum(1 for keywords in tool_keywords_map.values() for kw in keywords if kw in text)
    if tool_count >= 2:
        complex_score += 3
        complex_reasons.append(f"检测到{tool_count}个工具关键词")

    print(f"[ENHANCED ROUTER] 备用分类: complex_score={complex_score}")
    print(f"[ENHANCED ROUTER] 复杂原因: {complex_reasons}")
    print(f"[ENHANCED ROUTER] 检测到工具: {detected_tool}")

    # ========== 参数完整性检查（备用方案）==========
    missing_params = []
    clarification_question = ""
    completeness = ParameterCompleteness.READY
    completeness_reasoning = "备用模式参数检查通过"

    if detected_tool and detected_tool in TOOL_PARAM_SPECS:
        spec = TOOL_PARAM_SPECS[detected_tool]
        required_params = spec["required_params"]

        # 检查参数是否在用户输入中
        for param in required_params:
            param_found = False

            # 针对不同参数的检查策略
            if param == "file_path":
                # 检查是否包含文件路径模式
                if re.search(r'[A-Za-z]:\\|/home/|Desktop|桌面|文档|文件夹', text):
                    param_found = True
            elif param in ["subject", "title", "content"]:
                # 检查是否有实质性内容
                if len(text) > 5:  # 有实质内容
                    param_found = True
            elif param in ["start", "end", "time", "date"]:
                # 检查是否有时间/日期相关词汇
                time_keywords = ["今天", "明天", "后天", "早上", "下午", "晚上", "点", "时", "分", "号", "日", "周", "月", "年"]
                if any(kw in text for kw in time_keywords):
                    param_found = True
            elif param == "city":
                if len(text) > 3:
                    param_found = True
            elif param == "to":
                if "@" in text or "邮箱" in text or "邮件" in text:
                    param_found = True
            elif param == "todo_id":
                if "id" in text or "编号" in text or re.search(r'\d+', text):
                    param_found = True
            elif param == "entry_id":
                if "id" in text or "编号" in text or re.search(r'\d+', text):
                    param_found = True
            elif param == "search_term":
                if len(text) > 2:
                    param_found = True

            if not param_found:
                missing_params.append(param)

        # 判断是否需要追问
        if missing_params:
            completeness = ParameterCompleteness.NEED_CLARIFY
            completeness_reasoning = f"备用模式检测到缺失参数: {missing_params}"

            # 生成追问
            param_labels = {
                "subject": "日程标题",
                "start": "开始时间",
                "end": "结束时间",
                "file_path": "文件路径",
                "content": "内容",
                "to": "收件人邮箱",
                "city": "城市名称",
                "todo_id": "待办ID",
                "entry_id": "日程ID",
                "search_term": "搜索关键词"
            }

            missing_labels = [param_labels.get(p, p) for p in missing_params]
            if len(missing_labels) == 1:
                clarification_question = f"好的，我来帮您处理。请提供一下{missing_labels[0]}："
            else:
                clarification_question = f"好的，我来帮您处理。请提供以下信息：{', '.join(missing_labels[:-1])}和{missing_labels[-1]}。"

            print(f"[ENHANCED ROUTER] 备用方案检测到缺失参数: {missing_params}")
            print(f"[ENHANCED ROUTER] 追问内容: {clarification_question}")
        else:
            print(f"[ENHANCED ROUTER] 备用方案参数检查通过")
    # ========== 参数完整性检查结束 ==========

    complexity = ComplexityLevel.COMPLEX if complex_score > 2 else ComplexityLevel.SIMPLE
    agent = "planner" if complexity == ComplexityLevel.COMPLEX else "simple"

    return RouterResult(
        complexity=complexity,
        complexity_reasoning=f"备用规则: {', '.join(complex_reasons) if complex_reasons else '规则匹配'}",
        complexity_confidence=0.5,
        completeness=completeness,
        completeness_reasoning=completeness_reasoning,
        recommended_agent=agent,
        parsed_intent={"tool_name": detected_tool, "params": {}} if detected_tool else {},
        missing_params=missing_params,
        clarification_question=clarification_question
    )


def merge_supplemental_info(original_input: str, supplemental: str, history: list = None, collected_params: dict = None) -> str:
    """
    将用户补充的参数信息与原始指令合并成一条新消息

    Args:
        original_input: 原始用户指令
        supplemental: 用户补充的参数信息
        history: 对话历史（可选）
        collected_params: 已收集的参数字典（可选），用于告诉 LLM 哪些参数已经有了

    Returns:
        str: 合并后的新消息
    """
    from langchain_core.messages import HumanMessage, SystemMessage

    # 构建已收集参数的描述
    collected_str = ""
    if collected_params:
        params_list = []
        for key, value in collected_params.items():
            if value:  # 只包含有值的参数
                params_list.append(f"- {key}: {value}")
        if params_list:
            collected_str = "\n\n【已收集的参数】\n" + "\n".join(params_list)

    MERGE_PROMPT = f"""你是一个对话助手，负责将用户补充的信息与原始指令合并成一条完整的新指令。

【任务】
1. 分析原始指令和补充信息
2. 将补充的参数信息自然地融入原始指令
3. 生成一条完整、清晰的新指令
4. 不要添加假设性的信息，只合并用户明确提供的内容
5. 不要询问已收集的参数（见下方列表）{collected_str}

【原始指令】
{{original_input}}

【补充信息】
{{supplemental}}

【重要规则】
- 如果补充信息中包含文件路径（如"桌面"、"C:\\"等），应该包含在合并后的指令中
- 如果补充信息是对之前问题的回答，应该将答案整合到原始指令中
- 已收集的参数不要再问，直接使用

【输出要求】
直接输出一条合并后的完整指令，不要解释，不要添加额外内容。
"""

    try:
        with llm_lock:
            from .llm_config import get_router_llm
            router_llm_instance = get_router_llm()
            messages = [
                HumanMessage(content=MERGE_PROMPT.format(
                    original_input=original_input,
                    supplemental=supplemental
                ))
            ]
            response = router_llm_instance.invoke(messages)
            merged = response.content if hasattr(response, 'content') else str(response)
            return merged.strip()
    except Exception as e:
        print(f"[MERGE] 合并失败: {e}")
        # 降级：简单拼接
        return f"{original_input}。{supplemental}"


# ============================================================================
# 保留旧的导出（兼容性）
# ============================================================================

route = classify_complexity  # 别名，保持向后兼容


def route_old(user_input: str) -> str:
    """
    旧版路由函数 - 仅返回 Agent 类型
    """
    result = classify_complexity(user_input)
    return result.recommended_agent

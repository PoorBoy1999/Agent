"""
LLM 配置模块 - Router/工具调用使用本地模型，其余使用云端 API

配置策略：
1. Router（路由决策）- 本地 Ollama 模型（qwen2.5:7b）
   - 负责判断用户意图和决定是否调用工具
   - 使用本地模型可以快速响应

2. 工具调用（Tool Calling）- 本地 Ollama 模型（qwen2.5:7b）
   - 执行具体工具时使用
   - 本地模型响应快，适合频繁调用

3. Planner（任务规划）- 云端 API（qwen-plus）
   - 负责制定复杂任务计划
   - 使用云端模型获得更好的规划能力

4. 其余地方（普通对话）- 云端 API（qwen-plus）
   - 日常对话和通用任务
   - 使用云端模型获得更好的对话质量

本地模型配置：
- 模型名称：qwen2.5:7b
- 服务地址：http://localhost:11434/v1
- 需要先启动 Ollama 服务

使用说明：
- Router 和工具调用强制使用本地模型
- Planner 和其余地方使用云端 API
"""

import os
import time
import threading
from datetime import date
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage

# ============================================================================
# 配置选项：选择使用本地模型还是云端 API
# ============================================================================
# Router 和工具调用强制使用本地模型
# Planner 和其余地方使用云端 API

# 本地模型配置 - Ollama（用于 Router 和工具调用）
LOCAL_MODEL_CONFIG = {
    "model": "qwen2.5:7b",  # Ollama 模型名称
    "api_key": "ollama",  # Ollama 不需要真正的 key
    "base_url": "http://localhost:11434/v1",  # Ollama 服务地址
    "temperature": 0.7,
    "max_tokens": 81920,
    "timeout": 60,  # GPU 推理快速响应
}

# 云端 API 配置（用于 Planner 和其余地方）
CLOUD_MODEL_CONFIG = {
    "model": "qwen-plus",  # 通义千问模型
    "api_key": "sk-431b6b8e41714305972b956749b48fc6",  # API Key
    "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    "temperature": 0,
    "timeout": 60,  # 请求超时（秒）
}

# ============================================================================
# Router 专用 LLM（强制使用本地模型）
# ============================================================================
ROUTER_MODEL_CONFIG = {
    "model": "qwen2.5:7b",
    "api_key": "ollama",
    "base_url": "http://localhost:11434/v1",
    "temperature": 0.3,
    "max_tokens": 2048,
    "timeout": 30,  # GPU 推理快速响应
}

# ============================================================================
# Planner 专用 LLM（使用云端 API）
# ============================================================================
PLANNER_MODEL_CONFIG = {
    "model": "qwen-plus",
    "api_key": "sk-431b6b8e41714305972b956749b48fc6",
    "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    "temperature": 0.7,
    "max_tokens": 8192,
    "timeout": 60,
}

# ============================================================================
# 本地模型连接状态检查
# ============================================================================
_local_model_available = None
_check_lock = threading.Lock()
_last_check_time = 0
_CHECK_INTERVAL = 30  # 30秒内不重复检查


def is_local_model_available() -> bool:
    """
    检查本地模型服务是否可用
    使用缓存避免频繁检查
    """
    global _local_model_available, _last_check_time
    
    current_time = time.time()
    
    if _local_model_available is not None and (current_time - _last_check_time) < _CHECK_INTERVAL:
        return _local_model_available
    
    with _check_lock:
        if _local_model_available is not None and (current_time - _last_check_time) < _CHECK_INTERVAL:
            return _local_model_available
        
        try:
            import urllib.request
            # 检查 Ollama 默认端口 11434
            req = urllib.request.Request(
                "http://localhost:11434/api/tags",  # Ollama 模型列表接口
                headers={"Authorization": "Bearer ollama"}
            )
            response = urllib.request.urlopen(req, timeout=3)
            _local_model_available = response.status == 200
            if _local_model_available:
                import json
                data = json.loads(response.read().decode())
                models = [m.get('name', 'unknown') for m in data.get('models', [])]
                print(f"[LLM Config] Ollama 服务可用，已加载模型: {models}")
            else:
                print("[LLM Config] Ollama 服务响应异常")
        except Exception as e:
            _local_model_available = False
            print(f"[LLM Config] Ollama 服务不可用: {e}")
        
        _last_check_time = time.time()
        return _local_model_available


def get_active_llm_config() -> dict:
    """
    获取主 LLM 配置（用于普通对话，默认使用云端 API）
    """
    return CLOUD_MODEL_CONFIG

# ============================================================================
# 创建 LLM 实例（延迟加载，支持动态切换）
# ============================================================================

# 缓存的 LLM 实例
_cached_llm = None  # 主 LLM（云端 API）
_cached_router_llm = None  # Router LLM（本地模型）
_cached_planner_llm = None  # Planner LLM（云端 API）
_cached_tool_llm = None  # 工具调用 LLM（本地模型）


def create_llm(config: dict = None) -> ChatOpenAI:
    """创建 LLM 实例"""
    if config is None:
        config = get_active_llm_config()
    return ChatOpenAI(**config)


def get_llm() -> ChatOpenAI:
    """
    获取主 LLM 实例（云端 API，用于普通对话和其余地方）
    """
    global _cached_llm
    if _cached_llm is None:
        config = CLOUD_MODEL_CONFIG
        _cached_llm = ChatOpenAI(**config)
        print(f"[LLM] 主 LLM 已初始化，使用云端 API: {config.get('model')}")
    return _cached_llm


def get_router_llm() -> ChatOpenAI:
    """
    获取 Router 专用 LLM 实例（本地模型）
    Router 负责判断用户意图和决定是否调用工具
    """
    global _cached_router_llm

    if _cached_router_llm is None:
        config = ROUTER_MODEL_CONFIG
        print(f"[LLM] Router LLM 使用本地模型: {config.get('model')}")
        _cached_router_llm = ChatOpenAI(**config)

    return _cached_router_llm


def get_planner_llm() -> ChatOpenAI:
    """
    获取 Planner 专用 LLM 实例（云端 API）
    Planner 负责制定任务计划
    """
    global _cached_planner_llm

    if _cached_planner_llm is None:
        config = PLANNER_MODEL_CONFIG
        print(f"[LLM] Planner LLM 使用云端 API: {config.get('model')}")
        _cached_planner_llm = ChatOpenAI(**config)

    return _cached_planner_llm


def get_tool_llm() -> ChatOpenAI:
    """
    获取工具调用专用 LLM 实例（本地模型）
    工具调用需要快速响应，使用本地模型
    """
    global _cached_tool_llm

    if _cached_tool_llm is None:
        config = LOCAL_MODEL_CONFIG
        print(f"[LLM] 工具调用 LLM 使用本地模型: {config.get('model')}")
        _cached_tool_llm = ChatOpenAI(**config)

    return _cached_tool_llm


def reset_llm_cache():
    """重置 LLM 缓存（用于配置更改后重新初始化）"""
    global _cached_llm, _cached_router_llm, _cached_planner_llm, _cached_tool_llm
    _cached_llm = None
    _cached_router_llm = None
    _cached_planner_llm = None
    _cached_tool_llm = None
    print("[LLM] LLM 缓存已重置")


# 为了向后兼容，保留旧的实例变量
# 但实际使用时应该调用 get_llm() 和 get_router_llm()
llm = None  # 占位符，实际通过 get_llm() 获取
router_llm = None  # 占位符，实际通过 get_router_llm() 获取

# 【重要】不要在这里计算 TODAY_DATE！因为模块加载时只计算一次
# 日期信息将在运行时通过 get_system_prompt() 动态获取

# 系统提示模板（不包含日期）
_SYSTEM_PROMPT_TEMPLATE = """你是一个本地超级助手，可以帮助用户完成各种任务。

【核心规则 - 必须调用工具，禁止文字描述】
当用户请求创建待办事项时，你必须：
1. 调用 CreateTodoInput 工具（参数：content, due_date, due_time, priority）
2. 禁止用文字描述"将要创建"或"会创建"待办
3. 禁止说"我现在就为你创建..."然后不调用工具

❌ 错误做法（LLM常犯）：
   "好的，我为你创建明天早上9点的待办..."
   （然后没有调用任何工具）

✅ 正确做法：
   直接调用 CreateTodoInput 工具

【重要：邮件筛选规则】
当用户说"来自XXX的紧急邮件"时：
- "紧急"是描述邮件内容/重要性的词，不是搜索关键词
- 不要把"紧急"放进 subject_contains！
- 只用 from_contains 筛选发件人
- 邮件读取后，由你判断内容是否紧急

你可以通过调用工具来扩展你的能力。可用的工具包括：
- ReadFileInput: 读取本地文件内容
- ReadEmailInput: 通过 IMAP 读取邮箱（收件箱等）中的邮件摘要与正文预览；需用户已在 backend/config/email_config.toml 中配置服务器与账号
- WriteEmailDraftInput: 写邮件草稿（仅保存到 Outlook 草稿箱，不发送）；草稿可在 Outlook 草稿箱中找到
- ListOutlookCalendarEventsInput: 列出本机 Microsoft Outlook 默认日历中的日程；Windows 上通过 pywin32 读 Outlook，需 calendar_config.toml（无需 Azure）
- WriteFileInput: 写入或创建本地文本文件（需要用户确认）
- UpdateCalendarEventInput: 更新 Outlook 日历中的日程（需要用户确认）；必须先通过 ListOutlookCalendarEventsInput 获取有效的日程 EntryID
- CreateCalendarEventInput: 在 Outlook 日历中创建新日程（需要用户确认）
- BrowserFetchInput: 访问网页并提取正文内容；仅支持 GET 请求，超时 15 秒
- CreateTodoInput: 创建待办事项（需要用户确认）
- ListTodoInput: 列出待办事项
- CompleteTodoInput: 标记待办事项为已完成
- DeleteTodoInput: 删除待办事项

【重要：读取文件结果必须展示】
当用户请求读取文件并成功获取内容后，你必须：
1. 在回复中展示文件内容（至少展示前200行）
2. 如果文件过长，标注"（共 XXX 行，仅展示前 200 行）"
3. 禁止只说"已完成 1 个任务"而不展示内容

❌ 错误做法：
   "已完成 1 个任务"

✅ 正确做法：
   "已读取文件 `C:\\Users\\Ron\\Desktop\\职场\\sql.txt`，内容如下：
   ```
   [文件内容...]
   ```"

【重要：日期参数计算规则】
当用户提到"明天"、"下周"、"下个月"等相对日期时，你必须：
1. 计算出具体的日期（基于当前日期：{TODAY_DATE}）
2. 将计算后的日期填入工具的 start_date 参数（格式：YYYY-MM-DD）
3. days 参数应根据查询范围设置：
   - "明天" → start_date=明天的日期, days=1
   - "今天" → start_date=今天日期（或留空）, days=1
   - "这周" → start_date=今天日期, days=7
   - "下周" → start_date=下周一的日期, days=7
   - "这个月" → start_date=今天日期, days=30
4. timezone 参数通常使用 "Asia/Shanghai"

示例：
- 用户问"我明天有哪些日程" → ListOutlookCalendarEventsInput(start_date="明天的日期", days=1, timezone="Asia/Shanghai")
- 用户问"下周会议" → ListOutlookCalendarEventsInput(start_date="下周一的日期", days=7, timezone="Asia/Shanghai")
- 用户说"帮我创建日历事件，下周三下午3点有项目评审" → CreateCalendarEventInput(subject="项目评审", start="下周三日期 15:00:00", end="下周三日期 16:00:00")

使用工具时的规则：
1. 当用户请求读取文件时，调用 ReadFileInput 工具
2. 当用户请求查看邮件、收件箱、未读邮件时，调用 ReadEmailInput 工具
   - limit: 最多返回多少封（默认20）
   - unread_only: 是否仅未读（默认false）
   - subject_contains: **除非用户明确指定主题关键词，否则不要填写！**
   - from_contains: 当用户提到「某人的邮件」「谁发的」「来自某人」时填写
   - mailbox: 邮件夹（通常留空使用默认配置）
   - date_since: **当用户提到时间范围时必须填写！**
     * "今天" → date_since=今天的日期 (格式: DD-MMM-YYYY，如 18-May-2026)
     * "昨天" → date_since=昨天的日期
     * "最近一周" → date_since=7天前的日期
     * "最近一个月" → date_since=30天前的日期
     * 今天是 2026-05-18
2a. 当用户请求写邮件草稿（创建邮件、起草邮件、写信）时，调用 WriteEmailDraftInput（需要用户确认）
   - to: 收件人邮箱地址（必填）
   - subject: 邮件主题（必填）
   - body: 邮件正文内容（必填）
   - cc: 抄送地址（可选）
   - bcc: 密送地址（可选）
   - **重要**：此工具将邮件保存到 Outlook 草稿箱，不实际发送邮件
   - **关键触发词**："写邮件"、"创建邮件"、"起草邮件"、"写信给"、"给 XXX 发邮件"
3. 当用户请求查看 Outlook/微软日历、近期会议、日程安排时，调用 ListOutlookCalendarEventsInput
   - **必须正确计算日期参数**：
     * "明天" → start_date=明天的日期(YYYY-MM-DD格式), days=1
     * "下周" → start_date=下周一的日期, days=7
     * "本月" → start_date=本月1日的日期, days=30
   - timezone: 通常使用 "Asia/Shanghai"
4. 当用户请求修改/更新/调整某个日历日程时：
   a. **首先查看对话历史**：如果在之前的对话中创建过相关日程（如"修车"、"会议"等），那个日程就是需要修改的目标
   b. 调用 ListOutlookCalendarEventsInput 获取日程列表，通过对比标题/时间来确认目标日程的 EntryID
   c. 再调用 UpdateCalendarEventInput 执行修改（需要用户确认）
   - entry_id: **必填，必须从 ListOutlookCalendarEventsInput 的返回结果中提取！**
   - **重要：绝对不能自己生成或猜测 entry_id，必须使用 events 列表中对应日程的 id 字段值**
   - 可选字段：subject/start/end/location/all_day/body（至少填一个）
   - ⚠️ 【关键】"改"、"修改"、"调整"、"更新"、"取消"日程 → 必须用 UpdateCalendarEventInput！
     * "改到八点" → UpdateCalendarEventInput（先 list 找 id）
     * "把时间改一下" → UpdateCalendarEventInput（先 list 找 id）
     * "换个时间" → UpdateCalendarEventInput（先 list 找 id）
     * "删除这个会议" → UpdateCalendarEventInput（先 list 找 id）
     * ❌ "再创建一个" → CreateCalendarEventInput（新建）
5. 当用户请求创建新的日历日程时：
   - 调用 CreateCalendarEventInput（需要用户确认）
   - subject: 日程标题（必填）
   - start: 开始时间（必填，格式：YYYY-MM-DD HH:MM:SS 或 YYYY-MM-DD）
   - end: 结束时间（必填，格式：YYYY-MM-DD HH:MM:SS 或 YYYY-MM-DD）
   - location: 地点（可选）
   - all_day: 是否全天事件（可选，默认 false）
   - body: 备注内容（可选）
   - **关键触发词**："创建日历"、"新建日程"、"添加会议"、"安排会议"、"我创建XXX会议"
   - **日期解析**：
     * "后天" → 今天 + 2天
     * "下周三" → 找到下一个周三的日期
     * "下午3点" → 15:00:00
   - ⚠️ **【绝对禁止】除非用户明确说"再创建"、"额外创建"、"另外再创建"，否则不要用 Create 创建与现有日程相同/相似的日程！**
6. 当用户请求创建待办事项时，调用 CreateTodoInput（需要用户确认）
   - content: 待办内容（必填）
   - due_date: 截止日期 (YYYY-MM-DD格式)
   - due_time: 截止时间 (HH:MM格式)
   - priority: 优先级 (low/normal/high/urgent)
7. 当用户请求查看待办列表时，调用 ListTodoInput
   - status: 筛选状态 (all/pending/completed)
8. 当用户请求完成或删除待办时，调用 CompleteTodoInput 或 DeleteTodoInput
   - todo_id: 待办事项 ID（必填）
9. 当用户请求创建或修改文件时，调用 WriteFileInput（需要用户确认）
9. 文件路径必须在允许范围内：C:/Users/Ron/Desktop/
11. 传给工具的 file_path 必须与用户给出的路径完全一致（含空格、大小写、反斜杠）；不要用你认为的"规范形式"改写路径
12. 如果用户没有明确指定文件路径，请询问用户
13. 当用户请求查看网页内容、了解某个网站的信息、总结网页时，调用 BrowserFetchInput
    - url: 必须提供完整的 HTTP/HTTPS 网址
    - extract_content: 默认为 True，会自动提取正文内容
    - 返回结果包含：title（标题）、content（正文）、raw_text（原始文本）
    - **当用户要求"总结"网页时**：工具会返回 raw_text，由你后续调用 LLM 总结
14. 每次只调用一个工具
15. 当工具执行完成后，如果用户需要，再考虑是否需要进一步处理

当不需要调用工具时，直接回答用户的问题。回答要简洁、有帮助。"""


def get_system_prompt() -> str:
    """
    获取系统提示（动态包含当前日期）
    
    每次调用时都会重新计算日期，确保 LLM 使用正确的"今天"来计算相对日期
    """
    from datetime import date, timedelta
    
    # 动态计算当前日期
    today = date.today()
    today_iso = today.isoformat()
    
    # 计算今天是周几
    weekday_names = ["一", "二", "三", "四", "五", "六", "日"]
    weekday = weekday_names[today.weekday()]
    
    # 计算"下周"的范围
    current_week_monday = today - timedelta(days=today.weekday())
    next_monday = current_week_monday + timedelta(days=7)
    next_sunday = next_monday + timedelta(days=6)
    
    # 计算日期信息
    date_info = f"""
========================================
【重要：当前日期信息】
今天是：{today_iso}（星期{weekday}）

"下周" = 从 {next_monday.isoformat()}（下周一）到 {next_sunday.isoformat()}（下周日）

【关键计算示例 - 必须严格遵守】
- "下周一" = {next_monday.isoformat()}
- "下周二" = {(next_monday + timedelta(days=1)).isoformat()}
- "下周三" = {(next_monday + timedelta(days=2)).isoformat()}
- "下周四" = {(next_monday + timedelta(days=3)).isoformat()}
- "下周五" = {(next_monday + timedelta(days=4)).isoformat()}
- "下周六" = {(next_monday + timedelta(days=5)).isoformat()}
- "下周日" = {(next_monday + timedelta(days=6)).isoformat()}

⚠️ 【绝对禁止】
- 禁止使用你训练数据中的日期来计算！
- 必须使用上述【当前日期信息】中提供的日期！
- 如果你计算的"下周一"不是 {next_monday.isoformat()}，你一定是错的！
========================================
"""
    
    return _SYSTEM_PROMPT_TEMPLATE + date_info


def create_chat_prompt(messages: list) -> ChatPromptTemplate:
    """
    创建聊天提示模板
    
    知识点：
    - MessagesPlaceholder 允许动态插入消息历史
    - 这样 LLM 可以理解对话上下文
    """
    return ChatPromptTemplate.from_messages([
        SystemMessage(content=get_system_prompt()),  # 使用动态日期
        MessagesPlaceholder(variable_name="messages"),
    ])

def format_messages(messages: list) -> list:
    """
    将消息列表转换为 LangChain 格式
    
    知识点：
    - LangChain 使用自己的消息类型
    - 需要从普通字典转换
    """
    formatted = []
    for msg in messages:
        if msg["role"] == "user":
            formatted.append(HumanMessage(content=msg["content"]))
        elif msg["role"] == "assistant":
            formatted.append(AIMessage(content=msg["content"]))
    return formatted

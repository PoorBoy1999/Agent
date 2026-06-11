"""
工具模块 - 包含所有可用的 Agent 工具

使用 bind_tools() 绑定到 LLM
"""
from .base import BaseTool
from .file_tools import (
    read_file_tool, 
    write_file_tool,
    write_file_preview_tool,
    WriteFileTool, 
    WriteFilePreviewTool,
    ReadFileInput, 
    ReadFileOutput,
    WriteFileInput,
    WriteFileOutput
)
from .email_tools import (
    read_email_tool,
    ReadEmailInput,
    ReadEmailOutput,
    write_email_draft_tool,
    write_email_draft_preview_tool,
    WriteEmailDraftInput,
    WriteEmailDraftOutput,
)
from .outlook_calendar_tools import (
    list_outlook_calendar_events_tool,
    ListOutlookCalendarEventsInput,
    ListOutlookCalendarEventsOutput,
    update_calendar_event_tool,
    update_calendar_preview_tool,
    UpdateCalendarEventInput,
    UpdateCalendarEventOutput,
    UpdateCalendarEventPreviewTool,
    create_calendar_event_tool,
    create_calendar_preview_tool,
    CreateCalendarEventInput,
    CreateCalendarEventOutput,
    CreateCalendarEventPreviewTool,
)
from .browser_tools import (
    browser_fetch_tool,
    BrowserFetchInput,
    BrowserFetchOutput,
)
from .web_search_tool import (
    web_search_tool,
    WebSearchInput,
    WebSearchOutput,
)
from .todo_tools import (
    create_todo_tool,
    create_todo_preview_tool,
    list_todo_tool,
    complete_todo_tool,
    delete_todo_tool,
    CreateTodoInput,
    ListTodoInput,
    CompleteTodoInput,
    DeleteTodoInput,
)
from .weather_tools import (
    weather_query_tool,
    WeatherQueryInput,
    WeatherQueryOutput,
)

# 工具列表 - 用于实际执行
TOOLS = [
    read_file_tool, 
    read_email_tool, 
    write_email_draft_tool,
    list_outlook_calendar_events_tool, 
    browser_fetch_tool,
    web_search_tool,
    create_todo_tool,
    list_todo_tool,
    complete_todo_tool,
    delete_todo_tool,
    weather_query_tool,
]

# 预览工具列表 - 用于 LLM 调用（需要用户确认）
PREVIEW_TOOLS = [
    WriteFilePreviewTool(), 
    UpdateCalendarEventPreviewTool(),
    CreateCalendarEventPreviewTool(),
    write_email_draft_preview_tool,
]

# 工具 Schema 列表 - 用于绑定到 LLM（LLM 需要 Pydantic 模型）
# 注意：写入操作使用 preview 版本，需要用户确认
TOOL_SCHEMAS = [
    ReadFileInput, 
    ReadEmailInput, 
    ListOutlookCalendarEventsInput, 
    WriteFileInput, 
    UpdateCalendarEventInput, 
    BrowserFetchInput,
    WebSearchInput,
    CreateTodoInput,
    ListTodoInput,
    CompleteTodoInput,
    DeleteTodoInput,
    CreateCalendarEventInput,
    WriteEmailDraftInput,
    WeatherQueryInput,
]

# 写操作工具 Schema（需要确认）
WRITE_TOOL_SCHEMAS = [
    WriteFileInput,
    UpdateCalendarEventInput,
    CreateTodoInput,
    CreateCalendarEventInput,
    WriteEmailDraftInput,
]

# 工具名称列表 - 用于提示
TOOL_NAMES = [tool.name for tool in TOOLS + PREVIEW_TOOLS]

# 工具映射 - 用于根据名称查找工具
# 实际执行工具
TOOL_MAP = {tool.name: tool for tool in TOOLS}

# 预览工具 - 名称映射到预览工具实例（用于写操作预览）
# 预览工具不实际创建内容，只验证参数并返回预览信息
TOOL_PREVIEW_MAP = {
    "write_email_draft": write_email_draft_preview_tool,
    "write_file": write_file_preview_tool,
    "create_todo": create_todo_preview_tool,
    "create_outlook_calendar_event": create_calendar_preview_tool,
    "update_outlook_calendar_event": update_calendar_preview_tool,
}
# 预览工具覆盖实际工具（写操作需要先预览再确认）
TOOL_MAP.update(TOOL_PREVIEW_MAP)

__all__ = [
    "BaseTool", 
    "ReadFileTool", 
    "WriteFileTool",
    "WriteFilePreviewTool",
    "read_file_tool", 
    "write_file_tool",
    "write_file_preview_tool",
    "ReadFileInput",
    "ReadFileOutput",
    "WriteFileInput",
    "WriteFileOutput",
    "read_email_tool",
    "ReadEmailInput",
    "ReadEmailOutput",
    "write_email_draft_tool",
    "write_email_draft_preview_tool",
    "WriteEmailDraftInput",
    "WriteEmailDraftOutput",
    "list_outlook_calendar_events_tool",
    "ListOutlookCalendarEventsInput",
    "ListOutlookCalendarEventsOutput",
    "update_calendar_event_tool",
    "update_calendar_preview_tool",
    "UpdateCalendarEventInput",
    "UpdateCalendarEventOutput",
    "UpdateCalendarEventPreviewTool",
    "create_calendar_event_tool",
    "create_calendar_preview_tool",
    "CreateCalendarEventInput",
    "CreateCalendarEventOutput",
    "CreateCalendarEventPreviewTool",
    "browser_fetch_tool",
    "BrowserFetchInput",
    "BrowserFetchOutput",
    "create_todo_tool",
    "create_todo_preview_tool",
    "list_todo_tool",
    "complete_todo_tool",
    "delete_todo_tool",
    "CreateTodoInput",
    "ListTodoInput",
    "CompleteTodoInput",
    "DeleteTodoInput",
    "weather_query_tool",
    "WeatherQueryInput",
    "WeatherQueryOutput",
    "TOOLS",
    "PREVIEW_TOOLS",
    "TOOL_SCHEMAS",
    "WRITE_TOOL_SCHEMAS",
    "TOOL_NAMES",
    "TOOL_MAP"
]

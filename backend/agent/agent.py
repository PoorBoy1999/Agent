"""
简化版 Agent - 支持操作确认的工作流

架构：
1. 接收消息
2. 调用 LLM（带工具绑定）
3. 如果 LLM 返回 tool_calls：
   - 读操作：直接执行，返回结果
   - 写操作：仅预览，设置 pending_confirmation 后结束；用户在前端确认后由 main.py 调用 execute_pending_confirmation 真正写入
4. 用户确认后继续执行（经 WebSocket，不在 LangGraph 内）
"""

from typing import TypedDict, Optional, Any
from langgraph.graph import StateGraph, END
from langchain_core.messages import HumanMessage, AIMessage, ToolMessage, SystemMessage

from .llm_config import llm as _placeholder_llm, get_system_prompt, get_llm

# llm 变量保留用于向后兼容
llm = _placeholder_llm  # 占位符，实际调用时用 get_llm()
from .tools import (
    TOOL_SCHEMAS, 
    read_file_tool,
    read_email_tool,
    write_email_draft_tool,
    write_email_draft_preview_tool,
    list_outlook_calendar_events_tool,
    write_file_tool, 
    write_file_preview_tool,
    WriteFileInput,
    update_calendar_event_tool,
    update_calendar_preview_tool,
    UpdateCalendarEventInput,
    browser_fetch_tool,
    create_calendar_event_tool,
    create_calendar_preview_tool,
    CreateCalendarEventInput,
    create_todo_tool,
    create_todo_preview_tool,
    list_todo_tool,
    complete_todo_tool,
    delete_todo_tool,
    weather_query_tool,
    WriteEmailDraftInput,
)


class AgentState(TypedDict):
    """Agent 状态"""
    messages: list  # 对话消息
    tool_result: Optional[dict]  # 工具执行结果
    response_text: Optional[str]  # 最终响应文本
    pending_confirmation: Optional[dict]  # 等待确认的操作
    session_state: Optional[dict]  # 会话状态（跨请求保存的上下文）


def agent_node(state: AgentState) -> AgentState:
    """
    主 Agent 节点 - 处理所有逻辑（支持多轮工具调用）
    """
    messages = state["messages"]
    session_state = state.get("session_state", {})
    
    # 转换消息格式
    formatted_messages = []
    
    # 添加系统提示（使用动态日期）
    formatted_messages.append(SystemMessage(content=get_system_prompt()))
    
    # 添加会话状态上下文（如果有最近创建的日程）
    if session_state:
        recent_calendar_events = session_state.get("recent_calendar_events", [])
        if recent_calendar_events:
            context = "\n\n【会话上下文 - 最近创建的日程】\n"
            for event in recent_calendar_events[-3:]:  # 最多显示最近3个
                context += f"- 标题: {event.get('subject', '未知')}, "
                context += f"时间: {event.get('start', '未知')}, "
                context += f"EntryID: {event.get('entry_id', '')[:30]}...\n"
            context += "\n当用户说要修改/调整/改时间时，必须使用上述日程的 EntryID 调用 UpdateCalendarEventInput！\n"
            formatted_messages.append(SystemMessage(content=context))
    
    for msg in messages:
        if isinstance(msg, dict):
            role = msg.get("role")
            content = msg.get("content", "")
            if role == "user":
                formatted_messages.append(HumanMessage(content=content))
            elif role == "assistant":
                formatted_messages.append(AIMessage(content=content))
            elif role == "tool":
                # 处理 tool 角色的消息（从 run_agent 返回的历史消息）
                tool_name = msg.get("tool_name", "unknown")
                # 使用 tool_call_id 来标识工具调用，content 包含工具返回结果
                formatted_messages.append(ToolMessage(content=content, name=tool_name, tool_call_id="history_tool"))
        elif hasattr(msg, 'content'):
            formatted_messages.append(msg)
    
    # ========== Debug: Tool Binding Info ==========
    print("\n[DEBUG: Tool Binding Info]")
    print("=" * 60)
    print(f"TOOL_SCHEMAS count: {len(TOOL_SCHEMAS)}")
    for i, schema in enumerate(TOOL_SCHEMAS):
        schema_name = getattr(schema, '__name__', str(schema))
        print(f"  [{i}] {schema_name}")
        if hasattr(schema, 'model_fields'):
            for field_name, field_info in schema.model_fields.items():
                print(f"      - {field_name}: {field_info.annotation}")
    print("=" * 60)
    
    # 获取动态 LLM 并绑定工具
    active_llm = get_llm()
    print(f"[AGENT] 使用 LLM: {active_llm.model_name if hasattr(active_llm, 'model_name') else 'default'}")
    llm_with_tools = active_llm.bind_tools(TOOL_SCHEMAS)
    
    # ========== 多轮工具调用循环 ==========
    MAX_TOOL_CALLS = 10  # 最多调用 10 次工具，防止无限循环
    tool_call_count = 0
    
    while tool_call_count < MAX_TOOL_CALLS:
        tool_call_count += 1
        
        print(f"\n{'='*60}")
        print(f"[Round {tool_call_count}] LLM Call")
        print("=" * 60)
        
        # ========== [DEBUG] 检查 formatted_messages 状态 ==========
        print(f"\n[DEBUG] formatted_messages 状态检查:")
        print(f"  - 当前长度: {len(formatted_messages)}")
        for i, msg in enumerate(formatted_messages):
            if isinstance(msg, SystemMessage):
                role = "System"
            elif isinstance(msg, HumanMessage):
                role = "Human"
            elif isinstance(msg, AIMessage):
                role = "AI"
            elif isinstance(msg, ToolMessage):
                role = "Tool"
            else:
                role = f"Unknown({type(msg).__name__})"
            print(f"  [{i}] {role}: {msg.content[:50]}..." if len(msg.content) > 50 else f"  [{i}] {role}: {msg.content}")
        
        # ========== LLM 输入日志 ==========
        print("\n[LLM Input Messages]")
        for i, msg in enumerate(formatted_messages):
            if isinstance(msg, SystemMessage):
                role = "System"
            elif isinstance(msg, HumanMessage):
                role = "User"
            elif isinstance(msg, AIMessage):
                role = "Assistant"
            elif isinstance(msg, ToolMessage):
                role = "Tool"
            else:
                role = "Unknown"
            content = msg.content[:300] + "..." if len(msg.content) > 300 else msg.content
            print(f"\n[{i}] {role}:")
            print(content)
        print("-" * 60)
        
        # 调用 LLM
        ai_message = llm_with_tools.invoke(formatted_messages)
        
        # ========== LLM 输出日志 ==========
        print("\n[LLM Output]")
        print("=" * 60)
        
        # 打印原始 LLM 响应
        print(f"AI Message Type: {type(ai_message)}")
        print(f"Has tool_calls: {hasattr(ai_message, 'tool_calls') and bool(ai_message.tool_calls)}")
        
        # 打印 reasoning（如果有）
        if hasattr(ai_message, 'additional_kwargs'):
            reasoning = ai_message.additional_kwargs.get('thinking') or ai_message.additional_kwargs.get('reasoning')
            if reasoning:
                print(f"\n[LLM Reasoning]:")
                print(str(reasoning)[:2000])
        
        # 检查是否有工具调用
        if not isinstance(ai_message, AIMessage) or not ai_message.tool_calls:
            # LLM 没有调用工具，直接返回
            content = ai_message.content if hasattr(ai_message, 'content') else ""
            
            # ========== [DEBUG] 原始响应调试 ==========
            print("\n[DEBUG] 原始 LLM 响应内容 (前500字符):")
            print("=" * 60)
            raw_preview = content[:500] if len(content) > 500 else content
            print(f"类型: {type(content)}")
            print(f"长度: {len(content)} 字符")
            print(f"内容预览:\n{raw_preview}")
            print("=" * 60)
            # ========== [DEBUG] 结束 ==========
            
            # 安全截断，使用 UTF-8 编码
            safe_content = content[:2000]
            print(f"\nDirect response (UTF-8):\n{safe_content}")
            
            messages = messages + [ai_message]
            print("\n" + "=" * 60 + "\n")
            
            return {
                "messages": messages,
                "tool_result": None,
                "response_text": safe_content or "Done",
                "pending_confirmation": None,
                "session_state": session_state  # 保留会话状态
            }
        
        # 处理工具调用
        for tc in ai_message.tool_calls:
            tool_name = tc['name']
            tool_args = tc['args']
            
            print(f"\nTool Call: {tool_name}")
            print(f"Args: {tool_args}")
            
            # ========== Debug: Tool Selection Analysis ==========
            print("\n[DEBUG: Tool Selection Analysis]")
            user_message = None
            for msg in formatted_messages:
                if isinstance(msg, HumanMessage):
                    user_message = msg.content
                    break
            
            if user_message:
                print(f"User Request: {user_message}")
                
                # 分析关键词
                update_keywords = ['改', '修改', '更新', '变动', '调整', '移到', '改到', '推迟', '提前']
                list_keywords = ['查看', '有什么', '有哪些', '列出', '日程', '会议']
                
                has_update_keyword = any(kw in user_message for kw in update_keywords)
                
                print(f"Has update keyword: {has_update_keyword}")
                
                # 判断 LLM 是否正确理解
                if '改' in user_message or '修改' in user_message:
                    if tool_name == 'ListOutlookCalendarEventsInput':
                        print("[!] NOTE: User wants to 'modify' calendar, but LLM first called ListOutlookCalendarEventsInput")
                        print("    -> This is NORMAL: need to get entry_id first before UpdateCalendarEventInput")
                        print("    -> Next round should call UpdateCalendarEventInput")
                    elif tool_name == 'UpdateCalendarEventInput':
                        print("[OK] Correctly called UpdateCalendarEventInput")
            
            # 判断操作类型
            is_write_operation = tool_name in ("WriteFileInput", "UpdateCalendarEventInput", "CreateCalendarEventInput", "CreateTodoInput", "CompleteTodoInput", "DeleteTodoInput", "WriteEmailDraftInput")
            
            if is_write_operation:
                # ========== Write operation: preview and wait for confirmation ==========
                print("\n[Write operation detected, entering confirmation flow]")

                # 根据工具类型选择预览工具
                if tool_name == "WriteFileInput":
                    preview_tool = write_file_preview_tool
                elif tool_name == "UpdateCalendarEventInput":
                    preview_tool = update_calendar_preview_tool
                elif tool_name == "CreateCalendarEventInput":
                    preview_tool = create_calendar_preview_tool
                elif tool_name == "CreateTodoInput":
                    preview_tool = create_todo_preview_tool
                elif tool_name == "WriteEmailDraftInput":
                    preview_tool = write_email_draft_preview_tool
                else:
                    preview_result = {"success": False, "error": f"未知写操作: {tool_name}", "preview": None}
                    preview_tool = None

                if preview_tool:
                    preview_result = preview_tool.execute(**tool_args)
                
                # ========== Tool Preview Result Log ==========
                print("\n[Tool Preview Result]")
                print("=" * 60)
                print(f"Tool: {tool_name}")
                print(f"Args: {tool_args}")
                print(f"Preview Result: {'Success' if preview_result.get('success') else 'Failed'}")
                if preview_result.get("success"):
                    preview_info = preview_result.get("preview", {})
                    
                    if tool_name == "UpdateCalendarEventInput":
                        # Calendar update preview info
                        print(f"Event ID (entry_id): {preview_info.get('entry_id', '')}")
                        print(f"Changed Fields:")
                        for change in preview_info.get('changes', []):
                            print(f"  - {change.get('field')}: {change.get('new_value')}")
                    else:
                        # File write preview info
                        print(f"File Path: {preview_info.get('file_path', '')}")
                        print(f"File Exists: {preview_info.get('file_exists', False)}")
                        print(f"Content Lines: {preview_info.get('stats', {}).get('lines', 0)}")
                        print(f"Content Preview:\n{preview_info.get('content_preview', '')[:300]}")
                else:
                    print(f"Error: {preview_result.get('error', 'Unknown error')}")
                print("=" * 60 + "\n")
                
                # 创建工具消息（预览结果）
                tool_msg = ToolMessage(
                    content=str(preview_result),
                    name=tool_name,
                    tool_call_id=""
                )
                formatted_messages.append(ai_message)
                formatted_messages.append(tool_msg)
                messages = messages + [ai_message, tool_msg]
                
                # 构建待确认信息
                operation_type = "update_calendar_event" if tool_name == "UpdateCalendarEventInput" else "create_calendar_event" if tool_name == "CreateCalendarEventInput" else "create_todo" if tool_name == "CreateTodoInput" else "write_email_draft" if tool_name == "WriteEmailDraftInput" else "write_file"
                
                # 对于 update_calendar_event，如果 LLM 没有提供 subject_hint，则从 session_state 获取
                args = tool_args.copy()
                if operation_type == "update_calendar_event":
                    # 只有当 LLM 没有传 subject_hint 时，才从 session_state 获取
                    if not args.get("subject_hint") and session_state:
                        recent_events = session_state.get("recent_calendar_events", [])
                        if recent_events:
                            # 找到匹配的日程（通过 entry_id）
                            entry_id = tool_args.get("entry_id", "")
                            for event in recent_events:
                                if event.get("entry_id", "").startswith(entry_id[:20]) if entry_id else False:
                                    args["subject_hint"] = event.get("subject", "")
                                    print(f"[DEBUG] 从 session_state 添加 subject_hint: {args['subject_hint']}")
                                    break
                    else:
                        print(f"[DEBUG] 保留 LLM 提供的 subject_hint: {args.get('subject_hint')}")
                
                pending_confirmation = {
                    "operation": operation_type,
                    "tool_name": tool_name,
                    "args": args,
                    "preview": preview_result.get("preview"),
                    "success": preview_result.get("success"),
                    "error": preview_result.get("error")
                }
                
                # 格式化响应
                if preview_result.get("success"):
                    preview_info = preview_result.get("preview", {})
                    
                    if tool_name == "UpdateCalendarEventInput":
                        # 日历更新预览
                        changes = preview_info.get("changes", [])
                        entry_id = preview_info.get("entry_id", "")

                        changes_lines = []
                        for change in changes:
                            field = change.get("field", "")
                            new_value = change.get("new_value", "")
                            changes_lines.append(f"- **{field}**：{new_value}")

                        response_text = f"""**日历更新预览**

**日程 ID：** `{entry_id}`

**将要修改的字段：**
{chr(10).join(changes_lines) if changes_lines else "（无）"}

**请确认是否执行此更新？**"""
                    elif tool_name == "CreateCalendarEventInput":
                        # 日历创建预览
                        response_text = f"""**日历创建预览**

**标题：** {preview_info.get('subject', 'N/A')}
**开始时间：** {preview_info.get('start', 'N/A')}
**结束时间：** {preview_info.get('end', 'N/A')}
**地点：** {preview_info.get('location', '无')}
**全天事件：** {'是' if preview_info.get('all_day') else '否'}
**备注：** {preview_info.get('body', '无')[:100] if preview_info.get('body') else '无'}

**请确认是否创建此日程？**"""
                    elif tool_name == "WriteEmailDraftInput":
                        # 写邮件草稿预览
                        response_text = f"""**邮件草稿预览**

**收件人：** {preview_info.get('to', 'N/A')}
**主题：** {preview_info.get('subject', 'N/A')}
**抄送：** {preview_info.get('cc', '（无）')}
**密送：** {preview_info.get('bcc', '（无）')}

**正文预览：**
```
{preview_info.get('body_preview', preview_info.get('body', ''))}
```

**保存位置：** {preview_info.get('save_location', 'N/A')}

**请确认是否创建此邮件草稿？**"""
                    else:
                        # 文件写入预览（原有逻辑）
                        file_path = preview_info.get("file_path", "")
                        stats = preview_info.get("stats", {})
                        content_preview = preview_info.get("content_preview", "")
                        will_overwrite = preview_info.get("will_overwrite", False)
                        
                        response_text = f"""**文件写入预览**

**目标文件：** `{file_path}`

**操作类型：** {'覆盖已有文件' if will_overwrite else '创建新文件'}

**内容统计：**
- 行数：{stats.get('lines', 0)}
- 字符数：{stats.get('characters', 0)}

**内容预览：**
```
{content_preview}
```

**请确认是否执行此操作？**"""
                else:
                    response_text = f"预览失败：{preview_result.get('error', '未知错误')}"
                
                print("\n" + "=" * 60 + "\n")
                
                return {
                    "messages": messages,
                    "tool_result": preview_result,
                    "response_text": response_text,
                    "pending_confirmation": pending_confirmation,
                    "session_state": session_state  # 保留会话状态
                }
            
            else:
                # ========== Read operation: execute directly ==========
                print("\n[Read operation detected, executing directly]")
                
                try:
                    if tool_name == "ReadFileInput":
                        result = read_file_tool.execute(**tool_args)
                    elif tool_name == "ReadEmailInput":
                        result = read_email_tool.execute(**tool_args)
                    elif tool_name == "ListOutlookCalendarEventsInput":
                        result = list_outlook_calendar_events_tool.execute(**tool_args)
                    elif tool_name == "BrowserFetchInput":
                        result = browser_fetch_tool.execute(**tool_args)
                    elif tool_name == "ListTodoInput":
                        result = list_todo_tool.execute(**tool_args)
                    elif tool_name == "WeatherQueryInput":
                        print(f"[DEBUG] 开始执行天气查询工具，参数: {tool_args}")
                        result = weather_query_tool.execute(**tool_args)
                        print(f"[DEBUG] 天气查询工具执行完成，结果: {result}")
                    else:
                        result = {"success": False, "error": f"Unknown tool: {tool_name}"}
                except Exception as e:
                    print(f"[ERROR] 工具执行异常: {e}")
                    import traceback
                    traceback.print_exc()
                    result = {"success": False, "error": str(e)}
                
                # ========== Tool Execution Result Log ==========
                print("\n[Tool Execution Result]")
                print("=" * 60)
                print(f"Tool: {tool_name}")
                print(f"Args: {tool_args}")
                print(f"Result: {'Success' if result.get('success') else 'Failed'}")
                if result.get("success"):
                    if tool_name == "ReadEmailInput":
                        print(f"Mailbox: {result.get('mailbox', '')}")
                        print(f"Email Count: {result.get('total_fetched', 0)}")
                    elif tool_name == "ListOutlookCalendarEventsInput":
                        print(f"Outlook Events Count: {result.get('count', 0)}")
                        print(f"Time Range: {result.get('range_description', '')}")
                        # Print found events
                        events = result.get('events', [])
                        print("\nFound Events:")
                        for i, ev in enumerate(events):
                            ev_id = ev.get('id', '')
                            print(f"  [{i+1}] {ev.get('subject')} | {ev.get('start')} | ID: {ev_id}")
                        
                        print("\n[DEBUG: ToolMessage content for LLM]")
                        tool_msg_content = str(result)
                        print(f"Content Length: {len(tool_msg_content)} chars")
                        print(f"Content Preview (first 500 chars):")
                        print(tool_msg_content[:500])
                        # ========== End Debug ==========
                    elif tool_name == "WeatherQueryInput":
                        # 天气查询工具日志
                        weather_data = result.get("weather_data", "")
                        print(f"City: {result.get('city', '')}")
                        print(f"Forecast Type: {result.get('forecast_type', '')}")
                        print(f"Weather Data Length: {len(weather_data)} chars")
                        print(f"Summary: {result.get('summary', '')}")
                    elif tool_name == "WebSearchInput":
                        # 搜索工具日志
                        search_result = result.get("content", "") or result.get("results", "")
                        print(f"Search Result Length: {len(str(search_result))} chars")
                        print(f"Content Preview:\n{str(search_result)[:300]}")
                    elif tool_name == "ListTodoInput":
                        # 待办列表日志
                        todos = result.get("todos", [])
                        print(f"Todo Count: {len(todos)}")
                        for i, todo in enumerate(todos[:5]):
                            print(f"  [{i+1}] {todo.get('content', 'N/A')} - {todo.get('status', 'N/A')}")
                    elif tool_name == "BrowserFetchInput":
                        # 浏览器获取日志
                        content = result.get("content", "")
                        print(f"URL: {result.get('url', '')}")
                        print(f"Content Length: {len(content)} chars")
                        print(f"Content Preview:\n{content[:300]}")
                    else:
                        # 默认处理
                        content_preview = result.get("content", "")[:200] + "..." if len(result.get("content", "")) > 200 else result.get("content", "")
                        print(f"File Path: {result.get('file_path', '')}")
                        print(f"Content Length: {len(result.get('content', ''))} chars")
                        print(f"Content Preview:\n{content_preview}")
                else:
                    print(f"Error: {result.get('error', 'Unknown error')}")
                print("=" * 60 + "\n")
                
                # Create tool message
                tool_msg = ToolMessage(
                    content=str(result),
                    name=tool_name,
                    tool_call_id=""
                )
                formatted_messages.append(ai_message)
                formatted_messages.append(tool_msg)
                messages = messages + [ai_message, tool_msg]
                
                # ========== [DEBUG] 工具执行后检查 ==========
                print(f"\n[DEBUG] 工具执行后的状态:")
                print(f"  - formatted_messages 长度: {len(formatted_messages)}")
                print(f"  - 最后一条消息类型: {type(formatted_messages[-1]).__name__}")
                if isinstance(formatted_messages[-1], ToolMessage):
                    print(f"  - 最后一条 ToolMessage name: {formatted_messages[-1].name}")
                    print(f"  - 最后一条 ToolMessage content (前100字符): {formatted_messages[-1].content[:100]}")
                
                # Continue loop, LLM will decide next step based on tool result
                print("[Continue waiting for next LLM decision...]")
    
    # Exceeded max tool calls
    print("\n[WARNING] Exceeded max tool calls, forcing end")
    print(f"[DEBUG] 退出循环时的 messages 长度: {len(messages)}")
    messages = messages + [ai_message]
    return {
        "messages": messages,
        "tool_result": None,
        "response_text": "Timeout, please try again.",
        "pending_confirmation": None,
        "session_state": session_state  # 保留会话状态
    }


def create_agent() -> StateGraph:
    """
    Create Agent workflow.

    Write operations only preview and set pending_confirmation within the agent node,
    then the graph ends directly. Actual writes must be executed by the client
    after confirmation via WebSocket call to execute_pending_confirmation.
    """
    graph = StateGraph(AgentState)
    graph.add_node("agent", agent_node)
    graph.set_entry_point("agent")
    graph.add_edge("agent", END)
    return graph


# 创建编译后的 agent
agent_graph = create_agent().compile()


def run_agent(user_input: str, history: list = None, session_state: dict = None) -> dict:
    """
    运行 Agent
    
    Args:
        user_input: 用户输入
        history: 对话历史
        session_state: 会话状态（包含跨请求的上下文，如最近创建的日程 ID）
    
    Returns:
        dict: {
            "response": str,               # 最终响应文本
            "tool_calls": list,            # 调用的工具列表
            "tool_results": list,          # 工具执行结果
            "messages": list,              # 更新后的消息历史
            "pending_confirmation": dict,  # 等待确认的操作（如果有）
            "needs_confirmation": bool,    # 是否需要用户确认
            "session_state": dict          # 更新后的会话状态
        }
    """
    # ========== DEBUG: run_agent 入口日志 ==========
    print("\n" + "=" * 60)
    print("[DEBUG] run_agent 被调用")
    print(f"  user_input: {user_input[:50]}...")
    print(f"  history 条目数: {len(history) if history else 0}")
    if history:
        for i, h in enumerate(history):
            role = h.get("role", "unknown")
            content = h.get("content", "")[:50]
            tool_name = h.get("tool_name", "")
            print(f"    [{i}] role={role}, content='{content}...', tool_name={tool_name}")
    print("=" * 60)
    
    # 初始化消息
    messages = []
    
    # 添加历史消息
    if history:
        for msg in history:
            if msg["role"] == "user":
                messages.append({"role": "user", "content": msg["content"]})
            elif msg["role"] == "assistant":
                messages.append({"role": "assistant", "content": msg["content"]})
            elif msg["role"] == "tool":
                # 添加 tool 消息到历史
                messages.append({
                    "role": "tool",
                    "content": msg["content"],
                    "tool_name": msg.get("tool_name", "unknown")
                })
    
    # 添加当前消息
    messages.append({"role": "user", "content": user_input})
    
    # ========== DEBUG: 构建的 messages ==========
    print("\n[DEBUG] 构建的 messages:")
    for i, m in enumerate(messages):
        role = m.get("role", "unknown")
        content = m.get("content", "")[:50] if m.get("content") else ""
        tool_name = m.get("tool_name", "")
        print(f"  [{i}] role={role}, content='{content}...', tool_name={tool_name}")
    
    # 初始化状态
    initial_state = {
        "messages": messages,
        "tool_result": None,
        "response_text": None,
        "pending_confirmation": None,
        "session_state": session_state or {}  # 传递会话状态
    }
    
    # 运行 Agent
    result = agent_graph.invoke(initial_state)
    
    # 提取结果
    response_messages = result["messages"]
    response_text = result.get("response_text", "处理完成")
    tool_result = result.get("tool_result")
    pending_confirmation = result.get("pending_confirmation")
    
    # ========== DEBUG: 返回的 response_messages ==========
    print("\n[DEBUG] 返回的 response_messages:")
    for i, m in enumerate(response_messages):
        role = "unknown"
        content = ""
        tool_name = ""
        if isinstance(m, dict):
            role = m.get("role", "unknown")
            content = m.get("content", "")[:50] if m.get("content") else ""
            tool_name = m.get("tool_name", "")
        elif hasattr(m, 'content'):
            content = m.content[:50] if m.content else ""
            if isinstance(m, ToolMessage):
                role = "tool"
                tool_name = getattr(m, 'name', '')
            elif isinstance(m, HumanMessage):
                role = "user"
            elif isinstance(m, AIMessage):
                role = "assistant"
        print(f"  [{i}] role={role}, content='{content}...', tool_name={tool_name}")
    
    # 收集 tool_calls（从 AI 消息中）
    tool_calls = []
    
    for msg in response_messages:
        if isinstance(msg, AIMessage) and msg.tool_calls:
            for tc in msg.tool_calls:
                tool_calls.append({
                    "name": tc["name"],
                    "args": tc["args"]
                })
    
    # WebSocket 层按 { name, args, result } 解析，需与 main.py 一致
    tool_results: list[dict[str, Any]] = []
    if tool_result is not None:
        if tool_calls:
            tc0 = tool_calls[0]
            tool_results.append({
                "name": tc0["name"],
                "args": tc0.get("args") or {},
                "result": tool_result,
            })
        else:
            tool_results.append({
                "name": "",
                "args": {},
                "result": tool_result,
            })
    
    return {
        "response": response_text,
        "tool_calls": tool_calls,
        "tool_results": tool_results,
        "messages": [
            {
                # 检查消息是否为 ToolMessage 或包含 tool 角色标记
                "role": "tool" if (
                    isinstance(m, ToolMessage) or 
                    (isinstance(m, dict) and m.get("role") == "tool")
                ) else (
                    "user" if isinstance(m, dict) and m.get("role") == "user" else "assistant"
                ),
                "content": m.content if hasattr(m, "content") else (m.get("content") if isinstance(m, dict) else str(m)),
                "tool_name": getattr(m, 'name', None) if isinstance(m, ToolMessage) else (
                    m.get("tool_name") if isinstance(m, dict) else None
                )
            }
            for m in response_messages
        ],
        "pending_confirmation": pending_confirmation,
        "needs_confirmation": pending_confirmation is not None,
        "session_state": result.get("session_state", initial_state.get("session_state", {}))  # 返回更新后的 session_state
    }


def execute_tool(tool_name: str, args: dict) -> dict:
    """
    直接执行工具（不通过 LangGraph）
    """
    if tool_name == "ReadFileInput" or tool_name == "read_file":
        return read_file_tool.execute(**args)
    elif tool_name == "ReadEmailInput" or tool_name == "read_email":
        return read_email_tool.execute(**args)
    elif tool_name == "ListOutlookCalendarEventsInput" or tool_name == "list_outlook_calendar_events":
        return list_outlook_calendar_events_tool.execute(**args)
    elif tool_name == "WriteFileInput" or tool_name == "write_file":
        return write_file_tool.execute(**args)
    elif tool_name == "UpdateCalendarEventInput" or tool_name == "update_outlook_calendar_event":
        return update_calendar_event_tool.execute(**args)
    elif tool_name == "CreateCalendarEventInput" or tool_name == "create_outlook_calendar_event":
        return create_calendar_event_tool.execute(**args)
    elif tool_name == "BrowserFetchInput" or tool_name == "browser_fetch":
        return browser_fetch_tool.execute(**args)
    elif tool_name == "CreateTodoInput" or tool_name == "create_todo":
        return create_todo_tool.execute(**args)
    elif tool_name == "ListTodoInput" or tool_name == "list_todo":
        return list_todo_tool.execute(**args)
    elif tool_name == "CompleteTodoInput" or tool_name == "complete_todo":
        return complete_todo_tool.execute(**args)
    elif tool_name == "DeleteTodoInput" or tool_name == "delete_todo":
        return delete_todo_tool.execute(**args)
    elif tool_name == "WriteEmailDraftInput" or tool_name == "write_email_draft":
        return write_email_draft_tool.execute(**args)
    return {"success": False, "error": f"Unknown tool: {tool_name}"}


def execute_pending_confirmation(pending_confirmation: dict) -> dict:
    """
    执行等待确认的操作
    
    Args:
        pending_confirmation: 待确认操作的信息
    
    Returns:
        dict: 执行结果
    """
    print(f"\n[DEBUG] execute_pending_confirmation called")
    print(f"  pending_confirmation: {pending_confirmation}")
    
    if not pending_confirmation:
        return {"success": False, "error": "没有待确认的操作"}
    
    operation = pending_confirmation.get("operation")
    args = pending_confirmation.get("args", {})
    print(f"  operation: {operation}")
    print(f"  args: {args}")
    
    if operation == "write_file":
        result = write_file_tool.execute(**args)
    elif operation == "update_calendar_event":
        result = update_calendar_event_tool.execute(**args)
    elif operation == "create_calendar_event":
        result = create_calendar_event_tool.execute(**args)
    elif operation == "create_todo":
        print(f"[DEBUG] Calling create_todo_tool.execute with args: {args}")
        result = create_todo_tool.execute(**args)
        print(f"[DEBUG] create_todo result: {result}")
    elif operation == "write_email_draft":
        print(f"[DEBUG] Calling write_email_draft_tool.execute with args: {args}")
        result = write_email_draft_tool.execute(**args)
        print(f"[DEBUG] write_email_draft result: {result}")
    else:
        result = {"success": False, "error": f"未知的操作类型: {operation}"}
    
    return result

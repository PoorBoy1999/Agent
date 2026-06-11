"""
FastAPI 主服务 + WebSocket 通信
支持操作确认流程和复杂规划

功能：
1. 简单 Agent - 原有功能
2. 复杂规划 Agent - 支持 DAG 工作流和条件执行
"""

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime
import json

from agent.agent import run_agent, execute_pending_confirmation
from agent.planner_agent import (
    run_planner_agent, 
    handle_confirmation_result,
    continue_after_confirmation,
    _deserialize_plan,
    _serialize_plan,
    _execute_tool,
    _execute_condition,
    _execute_synthesize,
    TaskStatus,
    ExecutionPlan,
    PlanningState
)
from agent.router import classify_complexity, RouterResult, ParameterCompleteness, merge_supplemental_info

# 创建 FastAPI 应用
app = FastAPI(title="本地超级助手 API", version="0.3.0")

# 配置 CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class ChatMessage(BaseModel):
    """聊天消息模型"""
    role: str  # "user" | "assistant"
    content: str


class ChatRequest(BaseModel):
    """聊天请求模型"""
    message: str
    history: Optional[List[ChatMessage]] = []


class ConfirmationRequest(BaseModel):
    """确认请求模型"""
    confirmed: bool  # True = 确认执行, False = 取消
    pending_data: Optional[dict] = None  # 待确认的操作数据


# WebSocket 连接管理器
class ConnectionManager:
    """WebSocket 连接管理器"""
    
    def __init__(self):
        self.active_connections: List[WebSocket] = []
    
    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)
    
    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
    
    async def send_json(self, message: dict, websocket: WebSocket):
        await websocket.send_json(message)


manager = ConnectionManager()


@app.get("/")
async def root():
    """API 根路径"""
    return {"message": "本地超级助手 API 运行中", "version": "0.2.0"}


@app.get("/health")
async def health_check():
    """健康检查"""
    return {"status": "healthy"}


# 当前会话状态（用于维护复杂规划的状态）
_session_states: dict[str, dict] = {}

# 跨会话的短期记忆（工具执行结果缓存）
_tool_result_cache: dict[str, dict] = {}  # session_id -> { "browser_fetch_last": {...}, ... }


@app.websocket("/ws/chat")
async def websocket_chat(websocket: WebSocket):
    """
    WebSocket 聊天端点

    工作流程：
    1. 客户端连接
    2. 客户端发送消息（包含 history）
    3. 服务器调用 Agent 处理
    4. 如果需要确认：发送 pending_confirmation 事件
    5. 客户端确认后：发送 confirm 消息
    6. 服务器执行实际操作并返回结果
    
    支持两种模式：
    - simple: 使用原有简单 Agent
    - planner: 使用复杂规划 Agent（DAG 工作流）
    """
    await manager.connect(websocket)
    print("客户端连接")

    # 会话状态管理
    current_pending = None
    session_id = str(id(websocket))
    agent_mode = "auto"
    planner_session_state = None
    # 追问状态（持久化到会话级别）
    clarification_state = {
        "original_message": "",
        "question_count": 0,
        "max_questions": 2,
        "in_progress": False,  # 是否正在进行追问流程
        "collected_params": {}  # 已收集的参数 {param_name: value}
    }

    try:
        while True:
            # 接收消息
            data = await websocket.receive_text()
            request_data = json.loads(data)

            # 处理确认消息
            if request_data.get("type") == "confirm":
                confirmed = request_data.get("confirmed", False)
                pending_data = request_data.get("pending_data")

                print(f"收到确认消息: confirmed={confirmed}")

                if confirmed and pending_data:
                    # 用户确认，执行实际操作
                    print("用户确认，执行实际操作...")

                    # 从会话状态获取 planner 相关信息
                    saved_session_state = _session_states.get(session_id, {})
                    saved_agent_mode = saved_session_state.get("agent_mode", "simple")
                    saved_planner_state = saved_session_state.get("planner_session_state")

                    # 获取工具名称
                    tool_name = pending_data.get("tool_name") or pending_data.get("operation", "unknown")
                    
                    # 发送工具调用开始消息
                    await manager.send_json({
                        "type": "tool_call_start",
                        "tool_name": tool_name,
                        "params": pending_data.get("args", {})
                    }, websocket)

                    if saved_agent_mode == "planner" and saved_planner_state:
                        # 复杂规划 Agent 的确认处理
                        result = handle_confirmation_result(pending_data, True)
                        planner_session_state = continue_after_confirmation(
                            pending_data,
                            result,
                            saved_planner_state
                        )
                        
                        # 保存状态
                        _session_states[session_id] = {
                            **saved_session_state,
                            "planner_session_state": planner_session_state
                        }
                        
                        # 【调试】打印结果
                        print(f"[DEBUG] planner confirmation result: {result}")
                        print(f"[DEBUG] planner_session_state: {planner_session_state}")
                        
                        # 检查是否还有任务
                        if planner_session_state.get("needs_confirmation"):
                            await manager.send_json({
                                "type": "pending_confirmation",
                                "content": planner_session_state.get("response_text", "请确认"),
                                "pending_data": planner_session_state.get("pending_confirmation")
                            }, websocket)
                        else:
                            # 从执行计划中提取工具调用信息
                            tool_calls = []
                            if planner_session_state.get("execution_plan"):
                                try:
                                    execution_plan = planner_session_state.get("execution_plan")
                                    task_dag = execution_plan.get("task_dag", {})
                                    for task_id, task_data in task_dag.items():
                                        tool_name = task_data.get("tool_name", "")
                                        if tool_name:
                                            tool_calls.append({
                                                "name": tool_name,
                                                "status": task_data.get("status", "").value if hasattr(task_data.get("status"), "value") else str(task_data.get("status", ""))
                                            })
                                except Exception as e:
                                    print(f"[DEBUG] 提取 tool_calls 失败: {e}")
                            
                            # 发送工具调用结果消息
                            await manager.send_json({
                                "type": "tool_call_result",
                                "tool_name": tool_name,
                                "result": result
                            }, websocket)
                            
                            # 发送最终响应（planner 模式）
                            await manager.send_json({
                                "type": "response",
                                "content": planner_session_state.get("response_text", "执行完成"),
                                "mode": "planner",
                                "tool_calls": tool_calls,
                                "messages": planner_session_state.get("messages", [])
                            }, websocket)
                    else:
                        # 简单 Agent 的确认处理
                        print(f"[DEBUG] execute_pending_confirmation called with operation: {pending_data.get('operation')}")
                        
                        # 发送工具调用开始消息
                        tool_name = pending_data.get("tool_name", pending_data.get("operation", "unknown"))
                        await manager.send_json({
                            "type": "tool_call_start",
                            "tool_name": tool_name,
                            "params": pending_data.get("args", {})
                        }, websocket)
                        
                        # 执行操作
                        result = execute_pending_confirmation(pending_data)
                        print(f"[DEBUG] execute_pending_confirmation result: {result}")
                        
                        # 发送工具调用结果消息
                        await manager.send_json({
                            "type": "tool_call_result",
                            "tool_name": tool_name,
                            "result": result
                        }, websocket)

                        if result.get("success"):
                            operation_type = pending_data.get("operation", "write_file")
                            
                            if operation_type == "update_calendar_event":
                                response_text = "**日历更新成功！**\n\n日程已成功修改。"
                            elif operation_type == "create_calendar_event":
                                response_text = "**日程创建成功！**\n\n已在日历中添加新日程。"
                                # 【新增】保存创建的日程 ID 到 session_state，供后续修改使用
                                entry_id = result.get("entry_id")
                                pending_args = pending_data.get("args", {})
                                if entry_id:
                                    # 初始化会话状态中的日程列表
                                    if session_id not in _session_states:
                                        _session_states[session_id] = {}
                                    if "recent_calendar_events" not in _session_states[session_id]:
                                        _session_states[session_id]["recent_calendar_events"] = []
                                    
                                    # 添加新创建的日程
                                    _session_states[session_id]["recent_calendar_events"].append({
                                        "subject": pending_args.get("subject", ""),
                                        "start": pending_args.get("start", ""),
                                        "end": pending_args.get("end", ""),
                                        "entry_id": entry_id
                                    })
                                    print(f"[DEBUG] 保存创建的日程到 session_state: entry_id={entry_id[:30]}...")
                            elif operation_type == "create_todo":
                                response_text = "**待办创建成功！**\n\n待办事项已添加到列表。"
                                # 【修复】发送刷新待办列表信号
                                await manager.send_json({
                                    "type": "refresh_todos",
                                    "action": "created",
                                    "todo": result.get("todo")
                                }, websocket)
                            else:
                                response_text = f"""**文件写入成功！**

**文件路径：** `{result.get('file_path', '')}`
**写入字节：** {result.get('bytes_written', 0)} 字节"""
                        else:
                            operation_type = pending_data.get("operation", "write_file")
                            if operation_type == "update_calendar_event":
                                response_text = f"更新日历失败：{result.get('error', '未知错误')}"
                            elif operation_type == "create_todo":
                                response_text = f"创建待办失败：{result.get('error', '未知错误')}"
                            else:
                                response_text = f"写入文件失败：{result.get('error', '未知错误')}"

                        # 发送执行结果
                        await manager.send_json({
                            "type": "confirmation_result",
                            "content": response_text,
                            "result": result,
                            "confirmed": True,
                            "tool_calls": [{
                                "name": tool_name,
                                "status": "completed" if result.get("success") else "failed"
                            }]
                        }, websocket)

                else:
                    # 用户取消
                    print("用户取消操作")
                    
                    # 从会话状态获取 planner 相关信息
                    saved_session_state = _session_states.get(session_id, {})
                    saved_agent_mode = saved_session_state.get("agent_mode", "simple")
                    saved_planner_state = saved_session_state.get("planner_session_state")
                    
                    if saved_agent_mode == "planner" and saved_planner_state:
                        # 规划模式下取消
                        planner_session_state = continue_after_confirmation(
                            pending_data,
                            {"success": True, "message": "已取消"},
                            saved_planner_state
                        )
                        _session_states[session_id] = {
                            **saved_session_state,
                            "planner_session_state": planner_session_state
                        }
                        
                        await manager.send_json({
                            "type": "confirmation_result",
                            "content": "操作已取消",
                            "confirmed": False,
                            "mode": "planner"
                        }, websocket)
                    else:
                        await manager.send_json({
                            "type": "confirmation_result",
                            "content": "操作已取消",
                            "confirmed": False
                        }, websocket)

                current_pending = None
                continue
            
            # 【修复】处理模式切换（仅用于切换模式，不影响正常消息处理）
            # 前端不应该强制指定 Agent 模式，统一由后端 Router 判断
            # if request_data.get("mode"):
            #     agent_mode = request_data.get("mode", "simple")
            #     print(f"切换 Agent 模式: {agent_mode}")
            #     
            #     # 清除会话状态
            #     if agent_mode != "planner":
            #         planner_session_state = None
            #         if session_id in _session_states:
            #             del _session_states[session_id]
            #     
            #     continue

            # 处理继续执行（规划模式下）
            if request_data.get("type") == "continue":
                if agent_mode == "planner" and planner_session_state:
                    execution_plan = planner_session_state.get("execution_plan")
                    if execution_plan:
                        plan = _deserialize_plan(execution_plan)
                        
                        # 继续执行下一个任务
                        runnable = plan.get_next_runnable_tasks()
                        while runnable:
                            task_id = runnable[0]
                            task = plan.task_dag[task_id]
                            
                            # 根据任务类型执行
                            if task.task_type.value == "condition":
                                planner_session_state = _execute_condition_task(
                                    planner_session_state, plan, task
                                )
                            elif task.task_type.value == "synthesize":
                                planner_session_state = _execute_synthesize_task(
                                    planner_session_state, plan, task
                                )
                            else:
                                planner_session_state = _execute_tool_task(
                                    planner_session_state, plan, task
                                )
                            
                            # 保存状态
                            _session_states[session_id] = planner_session_state
                            
                            # 检查是否需要确认
                            if planner_session_state.get("needs_confirmation"):
                                await manager.send_json({
                                    "type": "pending_confirmation",
                                    "content": planner_session_state.get("response_text", "请确认"),
                                    "pending_data": planner_session_state.get("pending_confirmation")
                                }, websocket)
                                break
                            
                            # 检查是否完成
                            if plan.is_complete():
                                break
                            
                            # 继续下一个任务
                            runnable = plan.get_next_runnable_tasks()
                        
                        if not planner_session_state.get("needs_confirmation"):
                            # 从执行计划中提取工具调用信息
                            tool_calls = []
                            if planner_session_state.get("execution_plan"):
                                try:
                                    execution_plan = planner_session_state.get("execution_plan")
                                    task_dag = execution_plan.get("task_dag", {})
                                    for task_id, task_data in task_dag.items():
                                        tool_name = task_data.get("tool_name", "")
                                        if tool_name:
                                            tool_calls.append({
                                                "name": tool_name,
                                                "status": task_data.get("status", "").value if hasattr(task_data.get("status"), "value") else str(task_data.get("status", ""))
                                            })
                                except Exception as e:
                                    print(f"[DEBUG] 提取 tool_calls 失败: {e}")
                            
                            await manager.send_json({
                                "type": "response",
                                "content": planner_session_state.get("response_text", "执行完成"),
                                "mode": "planner",
                                "tool_calls": tool_calls,
                                "messages": planner_session_state.get("messages", [])
                            }, websocket)
                continue

            # 处理普通消息
            message = request_data.get("message", "")
            history = request_data.get("history", [])

            # 【修复】每次都重置为 auto，强制 Router 重新判断
            agent_mode = "auto"

            print(f"收到消息: {message}")
            print(f"Agent 模式(前端指定): {agent_mode}")
            
            # ========== [增强Router] 参数追问流程 ==========
            # 检查是否正在进行追问流程
            if clarification_state["question_count"] > 0:
                # 用户在回答追问，直接合并信息并继续
                print(f"[ENHANCED ROUTER] 用户补充参数，合并到原始指令...")

                # 通知前端正在处理
                await manager.send_json({
                    "type": "status",
                    "content": "正在分析您补充的信息..."
                }, websocket)

                original_msg = clarification_state["original_message"]

                # 用LLM合并补充信息与原始指令（传入已收集的参数和会话历史）
                merged_message = merge_supplemental_info(
                    original_msg,
                    message,
                    history=history,
                    collected_params=clarification_state.get("collected_params", {})
                )
                print(f"[ENHANCED ROUTER] 原始指令: {original_msg}")
                print(f"[ENHANCED ROUTER] 补充信息: {message}")
                print(f"[ENHANCED ROUTER] 已收集参数: {clarification_state.get('collected_params', {})}")
                print(f"[ENHANCED ROUTER] 合并后: {merged_message}")
                message = merged_message

                # 调用 Router 重新分析合并后的消息（传入已收集的参数和会话历史）
                print(f"[ENHANCED ROUTER] 重新分析合并后的指令...")
                route_result = classify_complexity(
                    message,
                    collected_params=clarification_state.get("collected_params", {}),
                    history=history
                )
            else:
                # 首次调用 Router 分析原始消息（传入会话历史）
                print(f"[ENHANCED ROUTER] 使用增强版 Router 分析...")
                route_result = classify_complexity(message, history=history)
            
            # 打印分析结果
            print(f"[ENHANCED ROUTER] 复杂度: {route_result.complexity.value}")
            print(f"[ENHANCED ROUTER] 复杂度理由: {route_result.complexity_reasoning}")
            print(f"[ENHANCED ROUTER] 置信度: {route_result.complexity_confidence:.2f}")
            print(f"[ENHANCED ROUTER] 参数完整性: {route_result.completeness.value}")
            print(f"[ENHANCED ROUTER] 推荐Agent: {route_result.recommended_agent}")
            
            if route_result.completeness == ParameterCompleteness.NEED_CLARIFY:
                print(f"[ENHANCED ROUTER] 缺失参数: {route_result.missing_params}")
                print(f"[ENHANCED ROUTER] 追问内容: {route_result.clarification_question}")
            
            # 判断是否需要追问
            if route_result.completeness == ParameterCompleteness.NEED_CLARIFY:
                # 检查追问次数
                if clarification_state["question_count"] < clarification_state["max_questions"]:
                    # 保存原始消息
                    if not clarification_state["original_message"]:
                        clarification_state["original_message"] = message

                    # 从已填充的参数中提取信息，更新 collected_params
                    parsed_params = route_result.parsed_intent.get("params", {}) if route_result.parsed_intent else {}
                    for param_name, param_value in parsed_params.items():
                        if param_value and param_name not in clarification_state.get("collected_params", {}):
                            clarification_state.setdefault("collected_params", {})[param_name] = param_value

                    clarification_state["question_count"] += 1

                    print(f"[ENHANCED ROUTER] 第 {clarification_state['question_count']} 次追问")
                    print(f"[ENHANCED ROUTER] 已收集参数: {clarification_state.get('collected_params', {})}")

                    # 发送追问给用户
                    print(f"[ENHANCED ROUTER] 发送追问...")
                    missing_params_count = len(route_result.missing_params)
                    try:
                        await manager.send_json({
                            "type": "clarification",
                            "content": route_result.clarification_question,
                            "missing_params": route_result.missing_params,
                            "missing_params_count": missing_params_count,
                            "current_param_index": 1,
                            "question_number": clarification_state["question_count"],
                            "max_questions": clarification_state["max_questions"],
                            "collected_params": clarification_state.get("collected_params", {})
                        }, websocket)
                        print(f"[ENHANCED ROUTER] 追问发送成功")
                    except Exception as e:
                        print(f"[ENHANCED ROUTER] 追问发送失败: {e}")
                        import traceback
                        traceback.print_exc()
                    print(f"[ENHANCED ROUTER] 追问已发送，等待用户回复")
                    continue
                else:
                    # 超过最大追问次数，返回错误
                    print(f"[ENHANCED ROUTER] 已达最大追问次数({clarification_state['max_questions']})")
                    await manager.send_json({
                        "type": "response",
                        "content": "抱歉，经过多次尝试仍无法获取完整的参数信息。请重新描述您的需求，包括所有必要的参数（如：标题、时间、文件路径等）。",
                        "mode": "clarification_failed"
                    }, websocket)
                    # 重置追问状态
                    clarification_state = {
                        "original_message": "",
                        "question_count": 0,
                        "max_questions": 2,
                        "in_progress": False,
                        "collected_params": {}
                    }
                    continue
            else:
                # 参数完整，清理追问状态
                if clarification_state["question_count"] > 0:
                    print(f"[ENHANCED ROUTER] 参数已补充完整，清除追问状态")

                clarification_state = {
                    "original_message": "",
                    "question_count": 0,
                    "max_questions": 2,
                    "in_progress": False,
                    "collected_params": {}
                }
            
            # 根据路由结果选择Agent模式
            agent_mode = route_result.recommended_agent
            print(f"[ENHANCED ROUTER] 最终使用的 Agent 模式: {'Planner (复杂规划)' if agent_mode == 'planner' else 'Simple (简单模式)'}")

            # 发送处理中状态
            await manager.send_json({
                "type": "status",
                "content": "正在思考..." if agent_mode == "simple" else "正在规划任务..."
            }, websocket)

            try:
                if agent_mode == "planner":
                    # 复杂规划 Agent - 传入短期记忆
                    cached_tool_results = _tool_result_cache.get(session_id, {})
                    
                    result = run_planner_agent(
                        user_input=message,
                        history=history,
                        cached_tool_results=cached_tool_results
                    )
                    
                    print(f"[PLANNER] needs_confirmation: {result.get('needs_confirmation')}")
                    print(f"[PLANNER] pending_confirmation: {result.get('pending_confirmation') is not None}")
                    print(f"[PLANNER] execution_plan: {result.get('execution_plan') is not None}")
                    
                    # 从执行计划中提取工具结果并保存到缓存
                    execution_plan = result.get("execution_plan", {})
                    task_dag = execution_plan.get("task_dag", {})
                    new_tool_results = {}
                    for task_id, task_data in task_dag.items():
                        tool_name = task_data.get("tool_name", "")
                        task_result = task_data.get("result")
                        if task_result and tool_name:
                            # 保存最后执行的 browser_fetch 结果
                            if tool_name == "browser_fetch":
                                new_tool_results["browser_fetch_last"] = task_result
                    
                    # 合并到缓存（保留旧缓存 + 新结果）
                    _tool_result_cache[session_id] = {**cached_tool_results, **new_tool_results}
                    
                    # 保存会话状态
                    if result.get("execution_plan"):
                        planner_session_state = {
                            "messages": history + [{"role": "user", "content": message}],
                            "user_input": message,
                            "execution_plan": result.get("execution_plan"),
                            "needs_confirmation": result.get("needs_confirmation", False),
                            "pending_confirmation": result.get("pending_confirmation"),
                            "response_text": result.get("response"),
                            "intermediate_results": {},
                            "error": result.get("error")
                        }
                        # 保存 agent_mode 和 planner_session_state 到会话
                        _session_states[session_id] = {
                            "agent_mode": agent_mode,
                            "planner_session_state": planner_session_state
                        }
                        
                        # 发送计划信息
                        if result.get("plan_description"):
                            await manager.send_json({
                                "type": "plan_info",
                                "content": result.get("plan_description"),
                                "plan": result.get("execution_plan")
                            }, websocket)
                    
                    # 检查是否需要用户确认
                    if result.get("needs_confirmation") and result.get("pending_confirmation"):
                        # 【新增】发送已执行任务的工具调用消息（不包含需要确认的任务）
                        execution_plan = result.get("execution_plan", {})
                        pending_task_id = result.get("pending_confirmation", {}).get("task_id")
                        if execution_plan:
                            task_dag = execution_plan.get("task_dag", {})
                            for task_id, task_data in task_dag.items():
                                # 跳过需要确认的任务
                                if task_id == pending_task_id:
                                    continue
                                tool_name = task_data.get("tool_name", "")
                                if tool_name:
                                    # 发送 tool_call_start
                                    await manager.send_json({
                                        "type": "tool_call_start",
                                        "tool_name": tool_name,
                                        "params": task_data.get("params", {}),
                                        "task_id": task_id,
                                        "description": task_data.get("description", "")
                                    }, websocket)
                                    # 发送 tool_call_result
                                    task_result = task_data.get("result", {})
                                    await manager.send_json({
                                        "type": "tool_call_result",
                                        "tool_name": tool_name,
                                        "task_id": task_id,
                                        "status": "success" if task_data.get("status") == "completed" else "failed",
                                        "result": task_result
                                    }, websocket)
                        
                        # 生成确认内容（使用 preview 而非 judge 结果）
                        pending_conf = result.get("pending_confirmation", {})
                        preview = pending_conf.get("preview", {})
                        confirmation_content = "请确认操作"
                        if preview:
                            op_type = preview.get("operation", "")
                            if op_type == "create_calendar_event":
                                confirmation_content = f"**确认创建日历事件**\n\n标题: {preview.get('subject', '无标题')}\n时间: {preview.get('start', '')}"
                            elif op_type == "update_calendar_event":
                                confirmation_content = f"**确认更新日历事件**\n\n标题: {preview.get('subject', '无标题')}\n时间: {preview.get('start', '')}"
                            elif op_type == "create_todo":
                                confirmation_content = f"**确认创建待办**\n\n内容: {preview.get('content', '')}"
                            else:
                                confirmation_content = f"**确认操作**\n\n{preview.get('subject', preview.get('content', '请确认'))}"
                        
                        await manager.send_json({
                            "type": "pending_confirmation",
                            "content": confirmation_content,
                            "pending_data": pending_conf
                        }, websocket)

                    else:
                        # ========== [修复] 自动执行所有任务 ==========
                        # 即使不需要确认，也要继续执行所有任务
                        execution_plan = result.get("execution_plan", {})
                        planner_session_state = {
                            "messages": history + [{"role": "user", "content": message}],
                            "user_input": message,
                            "execution_plan": execution_plan,
                            "needs_confirmation": False,
                            "pending_confirmation": None,
                            "response_text": result.get("response"),
                            "intermediate_results": {},
                            "error": result.get("error")
                        }
                        
                        # 自动执行所有可运行的任务
                        final_result = _auto_execute_all_tasks(
                            planner_session_state, 
                            cached_tool_results or {}
                        )
                        
                        # 处理返回结果
                        final_response = final_result.get("response", "执行完成")
                        pending_messages = final_result.get("pending_messages", [])
                        
                        # 【新增】发送待发送的实时工具调用消息
                        for msg in pending_messages:
                            try:
                                await manager.send_json(msg, websocket)
                            except Exception as e:
                                print(f"[DEBUG] 发送实时消息失败: {e}")
                        
                        # 发送计划信息（如果有）
                        if result.get("plan_description"):
                            await manager.send_json({
                                "type": "plan_info",
                                "content": result.get("plan_description"),
                                "plan": result.get("execution_plan")
                            }, websocket)
                        
                        # 从执行计划中提取工具调用信息
                        tool_calls = []
                        if planner_session_state.get("execution_plan"):
                            try:
                                execution_plan = planner_session_state.get("execution_plan")
                                task_dag = execution_plan.get("task_dag", {})
                                for task_id, task_data in task_dag.items():
                                    tool_name = task_data.get("tool_name", "")
                                    if tool_name:
                                        tool_calls.append({
                                            "name": tool_name,
                                            "status": task_data.get("status", "").value if hasattr(task_data.get("status"), "value") else str(task_data.get("status", ""))
                                        })
                            except Exception as e:
                                print(f"[DEBUG] 提取 tool_calls 失败: {e}")
                        
                        print(f"[DEBUG] 发送 response，tool_calls 数量: {len(tool_calls)}")
                        print(f"[DEBUG] tool_calls 内容: {tool_calls}")
                        
                        # 发送最终响应（包含 messages 和 tool_calls）
                        await manager.send_json({
                            "type": "response",
                            "content": final_response,
                            "mode": "planner",
                            "tool_calls": tool_calls,
                            "messages": planner_session_state.get("messages", [])
                        }, websocket)
                        
                else:
                    # 简单 Agent（原逻辑）
                    # 发送开始思考状态
                    await manager.send_json({
                        "type": "status",
                        "content": "正在分析您的请求..."
                    }, websocket)

                    result = run_agent(
                        user_input=message,
                        history=history,
                        session_state=_session_states.get(session_id, {})
                    )

                    print(f"[DEBUG] run_agent 返回")
                    print(f"  - needs_confirmation: {result.get('needs_confirmation')}")
                    print(f"  - pending_confirmation: {result.get('pending_confirmation') is not None}")

                    # 【新增】保存更新后的 session_state
                    if result.get("session_state"):
                        _session_states[session_id] = result.get("session_state")
                        print(f"[DEBUG] 保存 session_state: {list(_session_states.get(session_id, {}).keys())}")

                    # 检查是否需要用户确认
                    if result.get("needs_confirmation") and result.get("pending_confirmation"):
                        # 需要确认的操作
                        current_pending = result.get("pending_confirmation")

                        # 发送待确认信息
                        await manager.send_json({
                            "type": "pending_confirmation",
                            "content": result.get("response", ""),
                            "pending_data": current_pending
                        }, websocket)

                    else:
                        # 直接返回结果（读操作或普通对话）
                        tool_results = result.get("tool_results", [])
                        if tool_results:
                            for tr in tool_results:
                                if not isinstance(tr, dict):
                                    continue
                                tool_name = tr.get("name", "")
                                tool_args = tr.get("args", {})
                                tool_result = tr.get("result", {})

                                # 发送工具调用状态
                                tool_display_name = tool_name.replace("_", " ").title()
                                await manager.send_json({
                                    "type": "status",
                                    "content": f"正在调用 {tool_display_name}..."
                                }, websocket)

                                await manager.send_json({
                                    "type": "tool_call_start",
                                    "tool_name": tool_name,
                                    "params": tool_args
                                }, websocket)

                                await manager.send_json({
                                    "type": "tool_call_result",
                                    "tool_name": tool_name,
                                    "result": tool_result
                                }, websocket)

                        # 发送最终响应
                        await manager.send_json({
                            "type": "response",
                            "content": result.get("response", "处理完成"),
                            "tool_calls": result.get("tool_calls", []),
                            "messages": result.get("messages", [])  # 包含完整消息历史，包括 tool 消息
                        }, websocket)

            except Exception as e:
                print(f"处理出错: {e}")
                import traceback
                traceback.print_exc()
                await manager.send_json({
                    "type": "error",
                    "content": f"处理消息时出错：{str(e)}"
                }, websocket)

    except WebSocketDisconnect:
        manager.disconnect(websocket)
        print("客户端断开连接")
        
        # 清理会话状态
        if session_id in _session_states:
            del _session_states[session_id]


@app.post("/api/chat")
async def chat(request: ChatRequest):
    """REST API 备选方案"""
    return {
        "response": "请使用 WebSocket 连接进行实时对话",
        "websocket_url": "/ws/chat"
    }


@app.post("/api/confirm")
async def confirm_operation(request: ConfirmationRequest):
    """
    确认操作 API
    
    用于前端确认或取消待执行的操作
    """
    if request.confirmed and request.pending_data:
        result = execute_pending_confirmation(request.pending_data)
        return {
            "success": True,
            "confirmed": True,
            "result": result
        }
    else:
        return {
            "success": True,
            "confirmed": False,
            "message": "操作已取消"
        }


def _auto_execute_all_tasks(planner_session_state: dict, cached_tool_results: dict) -> dict:
    """
    [修复] 自动执行所有可运行的任务，直到计划完成
    这解决了 Planner Agent 只执行第一个任务就返回的问题
    支持自我反思与纠错机制
    
    返回: {"response": str, "pending_messages": list}
    """
    from agent.planner_agent import (
        _deserialize_plan,
        _serialize_plan,
        _execute_tool,
        _execute_condition,
        _execute_synthesize,
        _execute_reflection,
        _create_retry_task,
        TaskType,
        TaskStatus,
        _generate_final_response
    )

    # 收集要发送的实时消息
    pending_messages = []
    
    execution_plan = planner_session_state.get("execution_plan")
    if not execution_plan:
        return {
            "response": planner_session_state.get("response_text", "没有执行计划"),
            "pending_messages": pending_messages
        }

    plan = _deserialize_plan(execution_plan)
    user_input = planner_session_state.get("user_input", "")
    reflection_enabled = planner_session_state.get("reflection_enabled", True)
    max_retries = planner_session_state.get("max_retries", 2)
    reflection_history = planner_session_state.get("reflection_history", [])

    print(f"\n[AUTO EXECUTE] 开始自动执行所有任务...")
    print(f"[AUTO EXECUTE] 反思机制: {'启用' if reflection_enabled else '禁用'}")
    print(f"[AUTO EXECUTE] 最大重试次数: {max_retries}")
    print(f"[AUTO EXECUTE] 初始任务数: {len(plan.task_dag)}, 已完成: {plan.completed_count}")

    max_steps = 25  # 增加步数限制以支持重试
    step = 0

    while step < max_steps:
        step += 1

        # 检查是否需要确认
        if plan.pending_confirmation:
            print(f"[AUTO EXECUTE] Step {step}: 需要确认，停止自动执行")
            planner_session_state["needs_confirmation"] = True
            planner_session_state["pending_confirmation"] = plan.pending_confirmation
            planner_session_state["execution_plan"] = _serialize_plan(plan)
            return {
                "response": planner_session_state.get("response_text", "请确认操作"),
                "pending_messages": pending_messages
            }

        # 检查是否完成
        if plan.is_complete():
            print(f"[AUTO EXECUTE] Step {step}: 计划完成")
            break

        # 获取可运行任务
        runnable = plan.get_next_runnable_tasks()
        if not runnable:
            print(f"[AUTO EXECUTE] Step {step}: 没有可运行任务")
            break

        # 执行所有可运行的任务
        for task_id in runnable:
            task = plan.task_dag[task_id]
            # 支持 PENDING 和 NEEDS_RETRY 状态
            if task.status not in (TaskStatus.PENDING, TaskStatus.NEEDS_RETRY):
                continue

            print(f"\n[AUTO EXECUTE] Step {step}: 执行 [{task_id}] {task.description}")
            print(f"             Tool: {task.tool_name}, Type: {task.task_type.value}")
            print(f"             状态: {task.status.value}")

            # 【新增】发送工具调用开始消息
            if task.tool_name and task.task_type != TaskType.SYNTHESIZE:
                msg = {
                    "type": "tool_call_start",
                    "tool_name": task.tool_name,
                    "params": task.params,
                    "task_id": task_id,
                    "description": task.description
                }
                pending_messages.append(msg)
            
            # 根据任务类型执行
            if task.task_type == TaskType.CONDITION:
                result = _execute_condition(plan, task)
            elif task.task_type == TaskType.SYNTHESIZE:
                result = _execute_synthesize(planner_session_state, plan, task)
            elif task.task_type == TaskType.LLM_JUDGE:
                from agent.planner_agent import _execute_llm_judge
                result = _execute_llm_judge(planner_session_state, plan, task, cached_results=cached_tool_results)
            else:
                result = _execute_tool(plan, task)

            # 【新增】发送工具调用结果消息
            if task.tool_name and task.task_type != TaskType.SYNTHESIZE:
                status = "success" if task.status == TaskStatus.COMPLETED else "failed"
                pending_messages.append({
                    "type": "tool_call_result",
                    "tool_name": task.tool_name,
                    "task_id": task_id,
                    "status": status,
                    "result": result
                })
            
            # ========== [反思机制] 检查任务是否失败 ==========
            task_failed = False
            tool_result = result.get("tool_result", {})

            # 检查任务状态是否为失败
            if task.status == TaskStatus.FAILED:
                task_failed = True
                print(f"[AUTO EXECUTE] 任务 {task_id} 执行失败")

            # 检查返回结果是否包含错误
            if tool_result and isinstance(tool_result, dict):
                if not tool_result.get("success", True) and tool_result.get("error"):
                    task_failed = True
                    print(f"[AUTO EXECUTE] 任务 {task_id} 返回错误: {tool_result.get('error')}")

            # 如果任务失败且启用反思机制，进行反思
            if task_failed and reflection_enabled:
                print(f"[AUTO EXECUTE] 触发自我反思与纠错...")

                # 获取当前重试次数
                current_retry_count = 0
                for record in reflection_history:
                    if record.get("task_id") == task_id:
                        current_retry_count = record.get("retry_count", 0)
                        break

                # 执行反思
                reflection_outcome = _execute_reflection(
                    plan=plan,
                    failed_task=task,
                    user_input=user_input,
                    max_retries=max_retries
                )

                reflection_result = reflection_outcome.get("reflection_result", {})

                # 更新反思历史
                reflection_history = reflection_history + [{
                    "task_id": task_id,
                    "timestamp": datetime.now().isoformat(),
                    "reflection_result": reflection_result,
                    "retry_count": current_retry_count
                }]
                planner_session_state["reflection_history"] = reflection_history

                # 根据反思结果决定下一步
                if reflection_outcome.get("needs_correction") and not reflection_outcome.get("should_skip"):
                    if current_retry_count < max_retries:
                        # 需要修正，创建重试任务
                        new_retry_count = current_retry_count + 1

                        if reflection_outcome.get("new_params"):
                            retry_task_id = _create_retry_task(
                                plan=plan,
                                original_task=task,
                                new_params=reflection_outcome.get("new_params"),
                                retry_count=new_retry_count
                            )
                            print(f"[AUTO EXECUTE] 创建重试任务 {retry_task_id}")
                            task.status = TaskStatus.NEEDS_RETRY
                            continue  # 继续执行重试任务

                # 不需要修正或已达最大重试次数
                task.status = TaskStatus.RETRY_FAILED
                print(f"[AUTO EXECUTE] 任务 {task_id} 标记为 RETRY_FAILED")

            # 检查是否需要确认
            if plan.pending_confirmation:
                print(f"[AUTO EXECUTE] 任务 {task_id} 需要确认，停止自动执行")
                planner_session_state["needs_confirmation"] = True
                planner_session_state["pending_confirmation"] = plan.pending_confirmation
                planner_session_state["execution_plan"] = _serialize_plan(plan)
                return {
                    "response": result.get("response_text", "请确认操作"),
                    "pending_messages": pending_messages
                }

            # 更新会话状态
            planner_session_state["execution_plan"] = _serialize_plan(plan)

    # 生成最终响应
    print(f"\n[AUTO EXECUTE] 执行完成，共 {step} 步，{plan.completed_count}/{plan.total_count} 任务")
    print(f"[AUTO EXECUTE] 反思历史记录: {len(reflection_history)} 条")

    final_state = {
        "user_input": user_input,
        "cached_tool_results": cached_tool_results
    }
    final_response = _generate_final_response(plan, final_state)

    planner_session_state["response_text"] = final_response
    planner_session_state["execution_plan"] = _serialize_plan(plan)
    planner_session_state["needs_confirmation"] = False
    planner_session_state["pending_confirmation"] = None
    planner_session_state["reflection_history"] = reflection_history

    # 返回响应和待发送的消息
    return {
        "response": final_response,
        "pending_messages": pending_messages
    }


if __name__ == "__main__":
    import uvicorn
    print("=" * 50)
    print("启动本地超级助手服务...")
    print("API 文档: http://localhost:8000/docs")
    print("WebSocket: ws://localhost:8000/ws/chat")
    print("=" * 50)
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=False)

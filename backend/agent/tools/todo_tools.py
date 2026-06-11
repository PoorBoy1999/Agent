"""
待办事项工具
支持通过文件存储待办事项列表

工具类型：
1. CreateTodoTool - 创建待办事项
2. ListTodoTool - 列出待办事项
3. CompleteTodoTool - 标记待办为完成
4. DeleteTodoTool - 删除待办事项
"""

import os
from datetime import datetime
from pathlib import Path
from typing import Type, Optional

from pydantic import BaseModel, Field

from .base import BaseTool


# 待办事项存储文件
TODO_FILE = r"C:\Users\Ron\Desktop\todos.json"


class TodoItem(BaseModel):
    """待办事项数据模型"""
    id: str = Field(description="唯一标识符")
    content: str = Field(description="待办事项内容")
    due_date: Optional[str] = Field(default=None, description="截止日期 (YYYY-MM-DD)")
    due_time: Optional[str] = Field(default=None, description="截止时间 (HH:MM)")
    priority: str = Field(default="normal", description="优先级: low/normal/high/urgent")
    created_at: str = Field(description="创建时间")
    completed: bool = Field(default=False, description="是否已完成")
    completed_at: Optional[str] = Field(default=None, description="完成时间")


def _load_todos() -> list[TodoItem]:
    """加载待办事项列表"""
    if not os.path.exists(TODO_FILE):
        return []
    
    try:
        import json
        with open(TODO_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
            return [TodoItem(**item) for item in data]
    except Exception:
        return []


def _save_todos(todos: list[TodoItem]) -> bool:
    """保存待办事项列表"""
    try:
        # 确保目录存在
        os.makedirs(os.path.dirname(TODO_FILE), exist_ok=True)
        
        import json
        with open(TODO_FILE, 'w', encoding='utf-8') as f:
            json.dump([t.model_dump() for t in todos], f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        print(f"保存待办失败: {e}")
        return False


def _generate_id() -> str:
    """生成唯一 ID"""
    import uuid
    return str(uuid.uuid4())[:8]


def _get_timestamp() -> str:
    """获取当前时间戳"""
    return datetime.now().isoformat()


# =============================================================================
# 创建待办事项
# =============================================================================

class CreateTodoInput(BaseModel):
    """创建待办事项的输入参数"""
    content: str = Field(
        description="""待办事项的内容
        
        重要：
        - 内容应该清晰描述要完成的任务
        - 例如："处理 Alice 的紧急邮件"
        """,
        examples=["处理 Alice 的紧急邮件", "回复客户反馈"]
    )
    due_date: Optional[str] = Field(
        default=None,
        description="截止日期 (YYYY-MM-DD 格式)",
        examples=["2026-05-20", "2026-05-21"]
    )
    due_time: Optional[str] = Field(
        default=None,
        description="截止时间 (HH:MM 格式)",
        examples=["14:00", "15:30"]
    )
    priority: str = Field(
        default="normal",
        description="优先级: low/normal/high/urgent",
        examples=["high", "urgent"]
    )


class CreateTodoOutput(BaseModel):
    """创建待办事项的输出"""
    success: bool = Field(description="操作是否成功")
    todo: Optional[dict] = Field(default=None, description="创建的待办事项")
    error: str = Field(default="", description="错误信息")


class CreateTodoTool(BaseTool):
    """
    创建待办事项工具
    
    功能：
    - 在待办列表中创建新项目
    - 支持设置截止日期、时间和优先级
    - 待办存储在本地 JSON 文件中
    
    适用场景：
    - 用户说"创建待办"、"添加任务"、"记下来"等
    - 需要在日历上安排时间时
    """
    
    @property
    def name(self) -> str:
        return "create_todo"
    
    @property
    def description(self) -> str:
        return """创建待办事项。

适用场景：
- 用户要求创建待办、添加任务
- 需要记录需要完成的事项
- 用户说"帮我记下来"、"创建待办"、"添加任务"

输入参数：
- content: 待办事项内容（必填）
- due_date: 截止日期 (YYYY-MM-DD 格式，可选)
- due_time: 截止时间 (HH:MM 格式，可选)
- priority: 优先级 (low/normal/high/urgent，默认 normal)

待办事项会保存到本地文件中。"""
    
    @property
    def input_schema(self) -> Type[BaseModel]:
        return CreateTodoInput
    
    def execute(self, content: str, due_date: str = None, due_time: str = None, priority: str = "normal") -> dict:
        try:
            todos = _load_todos()
            
            # 创建新待办
            todo = TodoItem(
                id=_generate_id(),
                content=content,
                due_date=due_date,
                due_time=due_time,
                priority=priority,
                created_at=_get_timestamp(),
                completed=False
            )
            
            todos.append(todo)
            
            if _save_todos(todos):
                return {
                    "success": True,
                    "todo": todo.model_dump()
                }
            else:
                return {
                    "success": False,
                    "error": "保存待办失败"
                }
                
        except Exception as e:
            return {
                "success": False,
                "error": str(e)
            }


class CreateTodoPreviewTool(BaseTool):
    """预览创建待办事项"""
    
    @property
    def name(self) -> str:
        return "create_todo"  # 与实际工具同名，会覆盖 TOOL_MAP 中的实际工具
    
    @property
    def description(self) -> str:
        return "预览创建待办事项（不实际创建）"
    
    @property
    def input_schema(self) -> Type[BaseModel]:
        return CreateTodoInput
    
    def execute(self, content: str, due_date: str = None, due_time: str = None, priority: str = "normal") -> dict:
        # 直接执行，因为待办创建是安全的预览操作
        # 返回预览信息
        preview = {
            "action": "创建待办事项",
            "content": content,
            "due_date": due_date,
            "due_time": due_time,
            "priority": priority,
        }
        
        # 实际创建
        todos = _load_todos()
        todo = TodoItem(
            id=_generate_id(),
            content=content,
            due_date=due_date,
            due_time=due_time,
            priority=priority,
            created_at=_get_timestamp(),
            completed=False
        )
        todos.append(todo)
        
        if _save_todos(todos):
            return {
                "success": True,
                "preview": preview,
                "todo": todo.model_dump()
            }
        else:
            return {
                "success": False,
                "error": "保存待办失败",
                "preview": preview
            }


# 实例
create_todo_preview_tool = CreateTodoPreviewTool()


# =============================================================================
# 列出待办事项
# =============================================================================

class ListTodoInput(BaseModel):
    """列出待办事项的输入参数"""
    status: str = Field(
        default="all",
        description="筛选状态: all/pending/completed",
        examples=["pending", "all"]
    )
    priority: Optional[str] = Field(
        default=None,
        description="筛选优先级: low/normal/high/urgent",
        examples=["high", "urgent"]
    )
    limit: int = Field(
        default=50,
        description="最多返回数量"
    )


class ListTodoOutput(BaseModel):
    """列出待办事项的输出"""
    success: bool = Field(description="操作是否成功")
    todos: list[dict] = Field(default_factory=list, description="待办事项列表")
    count: int = Field(description="待办数量")
    error: str = Field(default="", description="错误信息")


class ListTodoTool(BaseTool):
    """
    列出待办事项工具
    
    功能：
    - 列出所有或筛选后的待办事项
    - 支持按状态（待完成/已完成）或优先级筛选
    
    适用场景：
    - 用户说"查看待办"、"我的任务"、"还有哪些待办"等
    """
    
    @property
    def name(self) -> str:
        return "list_todo"
    
    @property
    def description(self) -> str:
        return """列出待办事项。

适用场景：
- 用户想查看待办列表
- 用户说"我的待办"、"有哪些任务"、"还没完成的事"

输入参数：
- status: 筛选状态 (all/pending/completed)
- priority: 筛选优先级 (low/normal/high/urgent)
- limit: 最多返回数量（默认 50）"""
    
    @property
    def input_schema(self) -> Type[BaseModel]:
        return ListTodoInput
    
    def execute(self, status: str = "all", priority: str = None, limit: int = 50) -> dict:
        try:
            todos = _load_todos()
            
            # 筛选
            if status == "pending":
                todos = [t for t in todos if not t.completed]
            elif status == "completed":
                todos = [t for t in todos if t.completed]
            
            if priority:
                todos = [t for t in todos if t.priority == priority]
            
            # 排序：未完成的在前，按优先级和创建时间排序
            todos.sort(key=lambda t: (
                t.completed,
                -["low", "normal", "high", "urgent"].index(t.priority) if t.priority in ["low", "normal", "high", "urgent"] else 0,
                t.created_at
            ))
            
            # 限制数量
            todos = todos[:limit]
            
            return {
                "success": True,
                "todos": [t.model_dump() for t in todos],
                "count": len(todos)
            }
            
        except Exception as e:
            return {
                "success": False,
                "todos": [],
                "count": 0,
                "error": str(e)
            }


# =============================================================================
# 标记待办完成
# =============================================================================

class CompleteTodoInput(BaseModel):
    """标记待办完成的输入参数"""
    todo_id: str = Field(
        description="待办事项的 ID",
        examples=["a1b2c3d4"]
    )


class CompleteTodoOutput(BaseModel):
    """标记待办完成的输出"""
    success: bool = Field(description="操作是否成功")
    todo: Optional[dict] = Field(default=None, description="更新后的待办事项")
    error: str = Field(default="", description="错误信息")


class CompleteTodoTool(BaseTool):
    """
    标记待办事项为已完成
    
    功能：
    - 将指定 ID 的待办标记为已完成
    - 记录完成时间
    """
    
    @property
    def name(self) -> str:
        return "complete_todo"
    
    @property
    def description(self) -> str:
        return """标记待办事项为已完成。

适用场景：
- 用户说"完成"、"做完了"、"标记为完成"

输入参数：
- todo_id: 待办事项的 ID（必填）"""
    
    @property
    def input_schema(self) -> Type[BaseModel]:
        return CompleteTodoInput
    
    def execute(self, todo_id: str) -> dict:
        try:
            todos = _load_todos()
            
            # 找到待办
            found = False
            for todo in todos:
                if todo.id == todo_id:
                    todo.completed = True
                    todo.completed_at = _get_timestamp()
                    found = True
                    break
            
            if not found:
                return {
                    "success": False,
                    "error": f"未找到 ID 为 {todo_id} 的待办事项"
                }
            
            if _save_todos(todos):
                return {
                    "success": True,
                    "todo": todo.model_dump() if found else None
                }
            else:
                return {
                    "success": False,
                    "error": "保存失败"
                }
                
        except Exception as e:
            return {
                "success": False,
                "error": str(e)
            }


# =============================================================================
# 删除待办事项
# =============================================================================

class DeleteTodoInput(BaseModel):
    """删除待办事项的输入参数"""
    todo_id: str = Field(
        description="待办事项的 ID",
        examples=["a1b2c3d4"]
    )


class DeleteTodoOutput(BaseModel):
    """删除待办事项的输出"""
    success: bool = Field(description="操作是否成功")
    error: str = Field(default="", description="错误信息")


class DeleteTodoTool(BaseTool):
    """
    删除待办事项
    
    功能：
    - 从列表中删除指定 ID 的待办
    """
    
    @property
    def name(self) -> str:
        return "delete_todo"
    
    @property
    def description(self) -> str:
        return """删除待办事项。

适用场景：
- 用户说"删除"、"去掉"、"移除"

输入参数：
- todo_id: 待办事项的 ID（必填）"""
    
    @property
    def input_schema(self) -> Type[BaseModel]:
        return DeleteTodoInput
    
    def execute(self, todo_id: str) -> dict:
        try:
            todos = _load_todos()
            
            # 过滤掉要删除的
            original_count = len(todos)
            todos = [t for t in todos if t.id != todo_id]
            
            if len(todos) == original_count:
                return {
                    "success": False,
                    "error": f"未找到 ID 为 {todo_id} 的待办事项"
                }
            
            if _save_todos(todos):
                return {
                    "success": True
                }
            else:
                return {
                    "success": False,
                    "error": "保存失败"
                }
                
        except Exception as e:
            return {
                "success": False,
                "error": str(e)
            }


# =============================================================================
# 创建工具实例
# =============================================================================

create_todo_tool = CreateTodoTool()
list_todo_tool = ListTodoTool()
complete_todo_tool = CompleteTodoTool()
delete_todo_tool = DeleteTodoTool()

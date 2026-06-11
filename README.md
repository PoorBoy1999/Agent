# Agent 项目根目录

```
agent/
├── backend/           # 后端服务
│   ├── agent/        # Agent 核心逻辑
│   │   ├── tools/    # 工具定义
│   │   ├── agent.py # LangGraph 工作流
│   │   ├── llm_config.py  # LLM 配置
│   │   └── state.py # 状态定义
│   ├── schemas/      # Pydantic 模型
│   └── main.py      # FastAPI 主服务
└── frontend/        # React 前端
```

## 重要配置

### 1. 配置通义千问 API Key

编辑 `backend/agent/llm_config.py`，将 `api_key` 替换为你的 API Key：

```python
LLM_CONFIG = {
    "model": "qwen3.5-plus",  # 或其他模型
    "api_key": "sk-xxxxxxxxxxxx",  # ← 替换这里
    "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    "temperature": 0,
}
```

## 快速启动

### 1. 安装后端依赖

```bash
cd agent/backend
pip install -r requirements.txt
```

### 2. 安装前端依赖

```bash
cd agent/frontend
npm install
```

### 3. 启动服务

**终端 1 - 后端：**
```bash
cd agent/backend
python main.py
```

**终端 2 - 前端：**
```bash
cd agent/frontend
npm run dev
```

### 4. 访问

打开浏览器访问 http://localhost:5173

### 5. 测试

在输入框输入：
```
帮我读一下 "AI学习笔记"
```

注意：请先将一个名为 "AI学习笔记.txt" 的文件放在 `C:\Users\Administrator\Desktop\` 目录下。

---

## 知识点总结

### 1. Pydantic 模型校验

```python
class ReadFileInput(BaseModel):
    file_path: str = Field(description="要读取的文件路径")
    max_lines: int = Field(default=1000, description="最多读取的行数")
```

**作用**：在调用工具前自动校验参数是否符合预期。

### 2. Function Calling (bind_tools)

```python
llm_with_tools = llm.bind_tools(TOOL_SCHEMAS)
response = llm_with_tools.invoke(messages)
```

**作用**：让 LLM 知道有哪些工具可用，并能自主决定是否调用工具。

### 3. ToolNode (LangGraph)

```python
tool_node = ToolNode(TOOL_SCHEMAS)
```

**作用**：LangGraph 预构建的工具执行节点，自动处理 tool_calls。

### 4. conditional_edges (条件路由)

```python
graph.add_conditional_edges(
    "router",
    should_continue,
    {"tools": "tools", "__end__": END}
)
```

**作用**：根据 LLM 的决定，路由到不同的节点。

### 5. WebSocket 实时通信

```python
await websocket.send_json({"type": "tool_call_start", ...})
```

**作用**：服务器主动推送工具调用日志到前端。

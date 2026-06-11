"""
完整测试 Agent 流程
"""
import sys
sys.path.insert(0, '.')

from agent.agent import run_agent, execute_tool
from agent.tools import TOOL_SCHEMAS
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage

# 使用和 agent.py 相同的配置
llm = ChatOpenAI(
    model="qwen3.5-plus",
    api_key="sk-431b6b8e41714305972b956749b48fc6",
    base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
    temperature=0,
)

print("=" * 60)
print("完整测试 Agent 流程")
print("=" * 60)

# 测试消息
test_message = '帮我读一下 "AI学习笔记.txt" 这个文件'

print(f"\n测试消息: {test_message}")

# 1. 测试 LLM 是否返回 tool_calls
print("\n1. 测试 LLM 返回:")
llm_with_tools = llm.bind_tools(TOOL_SCHEMAS)
response = llm_with_tools.invoke([HumanMessage(content=test_message)])
print(f"   tool_calls: {bool(response.tool_calls)}")
if response.tool_calls:
    for tc in response.tool_calls:
        print(f"   工具名: {tc['name']}")
        print(f"   参数: {tc['args']}")

# 2. 测试工具执行
print("\n2. 测试工具执行:")
if response.tool_calls:
    tool_call = response.tool_calls[0]
    tool_name = tool_call['name']
    tool_args = tool_call['args']
    tool_result = execute_tool(tool_name, tool_args)
    print(f"   工具执行结果: {tool_result}")

# 3. 测试 run_agent
print("\n3. 测试 run_agent:")
result = run_agent(test_message)
print(f"   tool_calls: {result.get('tool_calls', [])}")
print(f"   response: {result.get('response', '')[:100]}...")

print("\n" + "=" * 60)

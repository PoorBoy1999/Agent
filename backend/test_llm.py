"""
测试 LLM 调用和 Function Calling
"""
import sys
sys.path.insert(0, '.')

from agent.llm_config import llm, SYSTEM_PROMPT
from agent.tools import TOOL_SCHEMAS
from langchain_core.messages import HumanMessage

print("=" * 50)
print("测试 LLM Function Calling")
print("=" * 50)

# 测试 1: 不带工具绑定
print("\n1. 测试普通调用:")
try:
    messages = [HumanMessage(content="你好，请简单介绍一下自己")]
    response = llm.invoke(messages)
    print(f"响应: {response.content[:200]}...")
except Exception as e:
    print(f"错误: {e}")

# 测试 2: 带工具绑定
print("\n2. 测试 Function Calling:")
try:
    llm_with_tools = llm.bind_tools(TOOL_SCHEMAS)
    messages = [
        HumanMessage(content='帮我读一下 "AI学习笔记.txt" 这个文件')
    ]
    response = llm_with_tools.invoke(messages)
    print(f"是否有 tool_calls: {bool(response.tool_calls)}")
    if response.tool_calls:
        for tc in response.tool_calls:
            print(f"工具名: {tc['name']}")
            print(f"参数: {tc['args']}")
    else:
        print(f"响应内容: {response.content[:200] if response.content else '(空)'}")
except Exception as e:
    print(f"错误: {e}")
    import traceback
    traceback.print_exc()

print("\n" + "=" * 50)
print("测试完成")
print("=" * 50)

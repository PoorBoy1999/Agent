"""
测试 Agent 在不需要工具调用时的行为
"""
import sys
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

sys.path.insert(0, '.')

from agent.agent import run_agent

# 测试问题：直接回答，不需要调用工具
test_messages = [
    "你好，请介绍一下自己",
    "今天天气怎么样？",
    "1+1等于几？",
]

for msg in test_messages:
    print("=" * 50)
    print(f"测试问题: {msg}")
    result = run_agent(msg)
    response = result.get('response', '')[:100]
    print(f"response (前100字符): {response}")
    print(f"tool_calls: {result.get('tool_calls', [])}")
    print(f"tool_results: {result.get('tool_results', [])}")
    print()

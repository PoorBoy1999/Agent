"""
测试路径清理
"""
import sys
sys.path.insert(0, '.')

from agent.tools import read_file_tool

# 测试路径清理
test_paths = [
    "C:\\Users\\Ron\\Desktop\\AI 学习笔记\\RAG坑总结.txt",
    "C:\\Users\\Ron\\Desktop\\AI学习笔记\\RAG坑总结.txt",
    "C:\\Users\\Ron\\Desktop\\AI学习笔记\\RAG坑总结.txt",
]

for path in test_paths:
    print(f"原始: {path}")
    # 模拟清理逻辑
    cleaned = path.strip()
    if len(cleaned) > 2 and cleaned[1] == ':':
        cleaned = cleaned[0:3] + cleaned[3:].replace(' ', '')
    else:
        cleaned = cleaned.replace(' ', '')
    print(f"清理后: {cleaned}")
    print()

# 测试实际读取
print("=" * 50)
print("测试文件读取:")
result = read_file_tool.execute("C:\\Users\\Ron\\Desktop\\AI学习笔记\\RAG坑总结.txt")
print(f"成功: {result.get('success')}")
if result.get('success'):
    print(f"内容长度: {len(result.get('content', ''))}")
else:
    print(f"错误: {result.get('error')}")

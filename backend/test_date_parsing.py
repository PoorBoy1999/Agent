"""测试 LLM 是否能正确理解日期语义"""
import sys
sys.path.insert(0, '.')

from agent.llm_config import llm, TOOL_SCHEMAS
from langchain_core.messages import HumanMessage

# 测试用例
test_cases = [
    "我明天有哪些日程安排",
    "下周有什么会议",
    "这个月还剩哪些日程",
]

llm_with_tools = llm.bind_tools(TOOL_SCHEMAS)

for user_input in test_cases:
    print("=" * 60)
    print(f"测试输入: {user_input}")
    print("=" * 60)
    
    messages = [HumanMessage(content=user_input)]
    response = llm_with_tools.invoke(messages)
    
    if response.tool_calls:
        for tc in response.tool_calls:
            print(f"工具: {tc['name']}")
            print(f"参数: {tc['args']}")
            
            if tc['name'] == 'ListOutlookCalendarEventsInput':
                args = tc['args']
                start_date = args.get('start_date', '')
                days = args.get('days', 30)
                timezone = args.get('timezone', '')
                
                print("\n调试分析:")
                print(f"  - start_date: {repr(start_date)}")
                print(f"  - days: {days}")
                print(f"  - timezone: {repr(timezone)}")
                
                if not start_date.strip():
                    print("  ✗ 警告: start_date 为空，LLM 可能没有正确理解相对日期!")
                else:
                    print(f"  ✓ start_date 已填充: {start_date}")
    else:
        print(f"直接响应: {response.content[:200]}...")
    print()

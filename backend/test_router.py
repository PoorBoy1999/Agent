"""
Router 功能测试脚本

测试目的：
1. 验证 Router 能正确判断任务复杂度
2. 测试各种典型场景
3. 检查本地模型连接

使用方法：
1. 先启动本地模型服务：vllm serve ... --port 8000
2. 运行脚本：python test_router.py
"""

import sys
import os

# 添加 backend 目录到路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from agent.router import classify_complexity, quick_classify, route, ComplexityLevel


# 测试用例
TEST_CASES = [
    # ========== Complex 场景 ==========
    {
        "input": "检查是否有来自spaceship的紧急邮件，有的话为我创建明天早上九点的日程安排",
        "expected": "complex",
        "description": "邮件+日历，多工具+条件判断"
    },
    {
        "input": "先查一下我的邮件，然后把重要的邮件创建一个待办事项",
        "expected": "complex",
        "description": "连接词+多步骤+依赖关系"
    },
    {
        "input": "帮我搜索一下百度的新闻，然后保存到桌面的news.txt文件中",
        "expected": "complex",
        "description": "浏览器+文件操作，多工具组合"
    },
    {
        "input": "如果明天有雨，就创建一个待办：记得带伞",
        "expected": "complex",
        "description": "条件判断"
    },
    {
        "input": "先查看我的日程安排，然后根据空闲时间创建新的会议",
        "expected": "complex",
        "description": "查询+创建，依赖关系"
    },
    {
        "input": "根据刚才查到的邮件内容，帮我写一封回复草稿并保存",
        "expected": "complex",
        "description": "基于结果的后续操作"
    },
    {
        "input": "检查我的邮箱，如果有来自老板的邮件就立即创建日历提醒",
        "expected": "complex",
        "description": "条件判断+多工具"
    },

    # ========== Simple 场景 ==========
    {
        "input": "帮我读一下桌面上的 todo.txt 文件",
        "expected": "simple",
        "description": "单一文件读取"
    },
    {
        "input": "打开百度",
        "expected": "simple",
        "description": "简单操作"
    },
    {
        "input": "解释一下什么是 DAG",
        "expected": "simple",
        "description": "知识问答"
    },
    {
        "input": "帮我计算 2+2 等于多少",
        "expected": "simple",
        "description": "简单计算"
    },
    {
        "input": "今天天气怎么样",
        "expected": "simple",
        "description": "简单查询"
    },
    {
        "input": "帮我写一封邮件草稿，内容是感谢你的帮助",
        "expected": "simple",
        "description": "固定模式生成，不涉及实际发送"
    },
    {
        "input": "生成一个会议邀请模板",
        "expected": "simple",
        "description": "模板生成"
    },
    {
        "input": "查看一下我的待办列表",
        "expected": "simple",
        "description": "单一查询操作"
    },
    {
        "input": "帮我创建一个待办：明天早上九点开会",
        "expected": "simple",
        "description": "明确单一操作"
    },
]


def test_quick_classify():
    """测试快速分类（规则匹配）"""
    print("\n" + "=" * 70)
    print("测试 1: 快速分类（规则匹配）")
    print("=" * 70)
    
    correct = 0
    total = len(TEST_CASES)
    
    for i, case in enumerate(TEST_CASES, 1):
        result = quick_classify(case["input"])
        
        if result is None:
            status = "⏭️ 跳过(需LLM)"
            predicted = "N/A"
        else:
            predicted = result.complexity.value
            if predicted == case["expected"]:
                correct += 1
                status = "✅ 正确"
            else:
                status = "❌ 错误"
        
        print(f"\n[{i}/{total}] {status}")
        print(f"  输入: {case['input'][:50]}...")
        print(f"  期望: {case['expected']}, 预测: {predicted}")
        print(f"  原因: {result.reasoning if result else 'N/A'}")
    
    print(f"\n快速分类准确率: {correct}/{total} = {correct/total*100:.1f}%")


def test_llm_classify():
    """测试 LLM 分类"""
    print("\n" + "=" * 70)
    print("测试 2: LLM 分类（深度分析）")
    print("=" * 70)
    
    correct = 0
    total = len(TEST_CASES)
    
    for i, case in enumerate(TEST_CASES, 1):
        print(f"\n[{i}/{total}] 测试中...")
        print(f"  输入: {case['input']}")
        
        try:
            result = classify_complexity(case["input"])
            predicted = result.complexity.value
            
            if predicted == case["expected"]:
                correct += 1
                status = "✅ 正确"
            else:
                status = "❌ 错误"
            
            print(f"  {status}")
            print(f"  期望: {case['expected']}, 预测: {predicted}")
            print(f"  理由: {result.reasoning}")
            print(f"  置信度: {result.confidence:.2f}")
            
        except Exception as e:
            print(f"  ❌ 错误: {e}")
    
    print(f"\nLLM 分类准确率: {correct}/{total} = {correct/total*100:.1f}%")


def test_route():
    """测试完整路由流程"""
    print("\n" + "=" * 70)
    print("测试 3: 完整路由流程")
    print("=" * 70)
    
    test_inputs = [
        "检查是否有来自spaceship的紧急邮件",
        "帮我读一下 todo.txt",
        "解释什么是区块链",
        "先查邮件，然后创建待办",
    ]
    
    for input_text in test_inputs:
        print(f"\n输入: {input_text}")
        try:
            agent_type = route(input_text)
            print(f"  → 路由结果: {agent_type}")
            print(f"  → 使用: {'Planner Agent (复杂规划)' if agent_type == 'planner' else 'Simple Agent (简单模式)'}")
        except Exception as e:
            print(f"  → 错误: {e}")


def test_model_connection():
    """测试本地模型连接"""
    print("\n" + "=" * 70)
    print("测试 0: 本地模型连接测试")
    print("=" * 70)
    
    try:
        from agent.llm_config import router_llm
        from langchain_core.messages import HumanMessage
        
        print("正在连接本地模型...")
        response = router_llm.invoke([
            HumanMessage(content="请回复：OK，如果能正常回复说明连接成功")
        ])
        
        content = response.content if hasattr(response, 'content') else str(response)
        print(f"✅ 模型连接成功！")
        print(f"响应: {content[:200]}...")
        return True
        
    except Exception as e:
        print(f"❌ 模型连接失败: {e}")
        print("\n请确保已启动本地模型服务：")
        print("  vllm serve C:/Users/Ron/Desktop/agent/backend/Qwen3.5-4B --port 8000")
        return False


def main():
    print("=" * 70)
    print(" Router 功能测试脚本")
    print("=" * 70)
    
    # 0. 测试模型连接
    if not test_model_connection():
        print("\n⚠️ 模型连接失败，跳过后续测试")
        return
    
    # 1. 测试快速分类
    test_quick_classify()
    
    # 2. 测试 LLM 分类
    print("\n是否继续进行 LLM 分类测试？(会消耗较多 token)")
    response = input("按 Enter 继续，或输入 q 退出: ").strip().lower()
    if response == 'q':
        print("测试已取消")
        return
    
    test_llm_classify()
    
    # 3. 测试完整路由
    test_route()
    
    print("\n" + "=" * 70)
    print("测试完成！")
    print("=" * 70)


if __name__ == "__main__":
    main()

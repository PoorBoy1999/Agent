"""Router LLM 超时诊断脚本"""
import time
import threading
import sys
sys.path.insert(0, r"C:\Users\Ron\Desktop\agent\backend")

from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, SystemMessage

# Router LLM 配置
ROUTER_MODEL_CONFIG = {
    "model": "qwen2.5:7b",
    "api_key": "ollama",
    "base_url": "http://localhost:11434/v1",
    "temperature": 0.3,
    "max_tokens": 2048,
    "timeout": 30,
}

# 简化的测试提示词
TEST_PROMPT = """分析用户输入，判断复杂度和参数完整性。

输出JSON格式：
{"complexity": "simple", "completeness": "ready", "reason": "test"}
"""

def test_llm_call():
    """测试 LLM 调用"""
    print("=" * 60)
    print("测试 Router LLM 调用")
    print("=" * 60)
    
    start_time = time.time()
    
    try:
        llm = ChatOpenAI(**ROUTER_MODEL_CONFIG)
        print(f"LLM 实例创建成功: {llm.model_name}")
        
        messages = [
            SystemMessage(content=TEST_PROMPT),
            HumanMessage(content="为我创建日程")
        ]
        
        print(f"发送请求... (timeout={ROUTER_MODEL_CONFIG['timeout']}s)")
        response = llm.invoke(messages)
        
        elapsed = time.time() - start_time
        print(f"\n请求成功! 耗时: {elapsed:.2f}秒")
        print(f"响应内容: {response.content}")
        
    except Exception as e:
        elapsed = time.time() - start_time
        print(f"\n请求失败! 耗时: {elapsed:.2f}秒")
        print(f"错误类型: {type(e).__name__}")
        print(f"错误信息: {str(e)}")

def test_llm_call_with_lock():
    """测试带锁的 LLM 调用"""
    print("\n" + "=" * 60)
    print("测试带锁的 Router LLM 调用")
    print("=" * 60)
    
    llm_lock = threading.Lock()
    result_container = [None]
    error_container = [None]
    
    def llm_call():
        try:
            with llm_lock:
                llm = ChatOpenAI(**ROUTER_MODEL_CONFIG)
                messages = [
                    SystemMessage(content=TEST_PROMPT),
                    HumanMessage(content="为我创建日程")
                ]
                print("发送请求... (在锁内)")
                response = llm.invoke(messages)
                result_container[0] = response
        except Exception as e:
            error_container[0] = e
    
    start_time = time.time()
    
    thread = threading.Thread(target=llm_call)
    thread.daemon = True
    thread.start()
    
    # 等待结果
    while thread.is_alive():
        if time.time() - start_time > 35:  # 35秒超时
            print(f"\n等待超时! 已等待: {time.time() - start_time:.2f}秒")
            break
        thread.join(timeout=0.5)
    
    elapsed = time.time() - start_time
    
    if error_container[0]:
        print(f"请求失败! 耗时: {elapsed:.2f}秒")
        print(f"错误: {error_container[0]}")
    elif result_container[0]:
        print(f"请求成功! 耗时: {elapsed:.2f}秒")
        print(f"响应: {result_container[0].content}")
    else:
        print(f"未知状态! 耗时: {elapsed:.2f}秒")

def test_full_prompt():
    """测试完整提示词（模拟增强Router）"""
    print("\n" + "=" * 60)
    print("测试完整增强Router提示词")
    print("=" * 60)
    
    # 读取增强Router提示词
    from agent.router import ENHANCED_ROUTER_PROMPT, _build_tool_specs_text
    
    prompt = ENHANCED_ROUTER_PROMPT.format(tool_specs=_build_tool_specs_text())
    
    print(f"提示词长度: {len(prompt)} 字符")
    
    start_time = time.time()
    
    try:
        llm = ChatOpenAI(**ROUTER_MODEL_CONFIG)
        messages = [
            SystemMessage(content=prompt),
            HumanMessage(content="为我创建日程，我要给外甥买礼物")
        ]
        
        print("发送请求... (完整提示词)")
        response = llm.invoke(messages)
        
        elapsed = time.time() - start_time
        print(f"\n请求成功! 耗时: {elapsed:.2f}秒")
        print(f"响应内容 ({len(response.content)} 字符):")
        print(response.content[:500] + "..." if len(response.content) > 500 else response.content)
        
    except Exception as e:
        elapsed = time.time() - start_time
        print(f"\n请求失败! 耗时: {elapsed:.2f}秒")
        print(f"错误类型: {type(e).__name__}")
        print(f"错误信息: {str(e)}")

if __name__ == "__main__":
    # 测试1: 简单调用
    test_llm_call()
    
    # 测试2: 带锁调用
    test_llm_call_with_lock()
    
    # 测试3: 完整提示词
    test_full_prompt()

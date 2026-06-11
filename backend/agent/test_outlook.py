"""
快速测试 Outlook COM 连接是否正常
运行: python test_outlook.py
"""
import sys
import time

print("=" * 50)
print("Outlook COM 连接测试")
print("=" * 50)

# 检查平台
print(f"\n平台: {sys.platform}")
if sys.platform != "win32":
    print("X 只支持 Windows")
    sys.exit(1)

# 检查 pywin32
print("\n[1] 检查 pywin32...")
try:
    import win32com.client
    print("OK pywin32 已安装")
except ImportError:
    print("X pywin32 未安装")
    print("   运行: pip install pywin32")
    sys.exit(1)

# 初始化 COM
print("\n[2] 初始化 COM...")
try:
    import pythoncom
    pythoncom.CoInitialize()
    print("OK COM 已初始化")
except Exception as e:
    print(f"! COM 初始化: {e}")

# 测试 Outlook 连接
print("\n[3] 连接 Outlook.Application...")
print("   (如果 Outlook 弹出对话框，请点击确认...)")
start = time.time()
try:
    outlook = win32com.client.Dispatch("Outlook.Application")
    elapsed = time.time() - start
    print(f"OK Outlook 连接成功 ({elapsed:.1f}秒)")
except Exception as e:
    elapsed = time.time() - start
    print(f"X 连接失败 ({elapsed:.1f}秒)")
    print(f"   错误: {e}")
    print("\n可能的原因:")
    print("  1. Outlook 未安装或未启动")
    print("  2. 32位/64位 不匹配")
    print("  3. Outlook 弹出了登录对话框")
    sys.exit(1)

# 获取 MAPI namespace
print("\n[4] 获取 MAPI Namespace...")
try:
    namespace = outlook.GetNamespace("MAPI")
    print("OK MAPI 连接成功")
except Exception as e:
    print(f"X MAPI 连接失败: {e}")
    sys.exit(1)

# 获取日历
print("\n[5] 访问日历文件夹...")
OL_FOLDER_CALENDAR = 9
try:
    cal = namespace.GetDefaultFolder(OL_FOLDER_CALENDAR)
    print("OK 日历文件夹访问成功")
except Exception as e:
    print(f"X 日历文件夹访问失败: {e}")
    sys.exit(1)

# 读取日程
print("\n[6] 读取日程...")
try:
    items = cal.Items
    items.Sort("[Start]")
    items.IncludeRecurrences = True
    
    # 安全读取数量，避免 2147483647 的错误值
    raw_count = items.Count
    if raw_count == 2147483647 or raw_count < 0:
        print(f"! 原始数量异常 ({raw_count})，尝试直接读取...")
        # 手动遍历前 100 个来测试
        test_items = []
        for i in range(1, min(101, 5000)):
            try:
                item = items.Item(i)
                if hasattr(item, 'Start'):
                    test_items.append(item)
            except Exception:
                break
        count = len(test_items)
        print(f"OK 实际读取到 {count} 个日程")
        
        if test_items:
            print("\n最近 3 个日程:")
            for item in test_items[:3]:
                try:
                    subj = getattr(item, 'Subject', '(无标题)') or '(无标题)'
                    start = getattr(item, 'Start', None)
                    print(f"  - {start}: {subj}")
                except Exception:
                    pass
    else:
        print(f"OK 日历读取成功，共 {raw_count} 个项目")
        count = raw_count
except Exception as e:
    print(f"X 日历读取失败: {e}")
    sys.exit(1)

# 清理
print("\n[7] 清理 COM...")
try:
    import pythoncom
    pythoncom.CoUninitialize()
    print("OK COM 已清理")
except Exception:
    pass

print("\n" + "=" * 50)
print("所有测试通过！Outlook 日历功能应该正常。")
print("=" * 50)

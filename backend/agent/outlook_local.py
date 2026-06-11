"""
Windows 本机 Microsoft Outlook：通过 pywin32 COM 读取默认日历（无需 Azure / Graph）。
前提：已安装 Outlook 桌面客户端并完成邮箱配置。
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import tomllib

# Outlook OlDefaultFolders
OL_FOLDER_CALENDAR = 9
OL_FOLDER_DRAFTS = 16  # 草稿箱
OL_FOLDER_SENTMAIL = 5  # 已发送邮件


def _backend_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _calendar_config_path() -> Path:
    override = os.environ.get("CALENDAR_CONFIG_PATH", "").strip()
    if override:
        return Path(override)
    return _backend_root() / "config" / "calendar_config.toml"


def load_calendar_config() -> dict[str, Any] | None:
    path = _calendar_config_path()
    if not path.is_file():
        return None
    with open(path, "rb") as f:
        return tomllib.load(f)


# 常见误写 → IANA 标准名（tzdata / zoneinfo）
_IANA_TZ_ALIASES: dict[str, str] = {
    "asia/beijing": "Asia/Shanghai",
    "beijing": "Asia/Shanghai",
    "china": "Asia/Shanghai",
}


def resolve_iana_timezone(timezone_name: str) -> str:
    """
    将配置或参数中的时区名解析为 IANA ID。
    无法识别时抛出 ValueError。
    """
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

    raw = (timezone_name or "").strip()
    if not raw:
        return "UTC"
    lk = raw.lower()
    if lk in _IANA_TZ_ALIASES:
        raw = _IANA_TZ_ALIASES[lk]

    # 直接尝试创建 ZoneInfo 来验证时区（不依赖 available_timezones）
    # available_timezones() 在部分 Windows 环境可能返回空
    try:
        ZoneInfo(raw)
    except (KeyError, ZoneInfoNotFoundError):
        raise ValueError(
            f"无效时区名：{raw!r}。请使用 IANA 名称（如 Asia/Shanghai、America/New_York）。"
        )
    return raw


def compute_window_local(
    timezone_name: str,
    days: int,
    start_date_yyyy_mm_dd: str = "",
) -> tuple[datetime, datetime, str]:
    """
    按本地时区计算 [start, end) 的「墙上时钟」naive datetime（与 Outlook 会话本地时间一致），
    以及人类可读范围说明。
    """
    from zoneinfo import ZoneInfo

    iana = resolve_iana_timezone(timezone_name)

    tz = ZoneInfo(iana)
    if start_date_yyyy_mm_dd.strip():
        d = datetime.strptime(start_date_yyyy_mm_dd.strip(), "%Y-%m-%d").date()
        anchor_aware = datetime(d.year, d.month, d.day, 0, 0, 0, tzinfo=tz)
    else:
        now = datetime.now(tz)
        anchor_aware = datetime(now.year, now.month, now.day, 0, 0, 0, tzinfo=tz)
    end_aware = anchor_aware + timedelta(days=max(1, days))
    anchor_naive = anchor_aware.replace(tzinfo=None)
    end_naive = end_aware.replace(tzinfo=None)
    fmt = "%Y-%m-%d %H:%M"
    desc = (
        f"{anchor_aware.strftime(fmt)} {iana} ～ "
        f"{end_aware.strftime(fmt)} {iana}（共 {days} 天，本机 Outlook）"
    )
    return anchor_naive, end_naive, desc


def _outlook_restrict_range(start_naive: datetime, end_naive: datetime) -> str:
    """Outlook Items.Restrict 常用字面量格式（与多数中文区域 Outlook 兼容；若失败会回退遍历）。"""
    fmt = "%m/%d/%Y %I:%M %p"
    return (
        f"[Start] >= '{start_naive.strftime(fmt)}' AND [Start] < '{end_naive.strftime(fmt)}'"
    )


def _item_start_naive(item: Any) -> datetime | None:
    try:
        st = item.Start
    except Exception:
        return None
    if isinstance(st, datetime):
        # 直接返回 naive datetime，不做时区转换
        # Outlook 的时区标识经常不准确，直接使用返回的时间最可靠
        return st.replace(tzinfo=None) if st.tzinfo is None else st.replace(tzinfo=None)
    try:
        return datetime.fromtimestamp(float(st))
    except Exception:
        return None


def format_outlook_appointment(item: Any, timezone_label: str) -> dict[str, Any]:
    subj = ""
    loc = ""
    org = ""
    web_link = ""
    try:
        subj = (getattr(item, "Subject", None) or "").strip()
    except Exception:
        pass
    try:
        loc = (getattr(item, "Location", None) or "").strip()
    except Exception:
        pass
    try:
        o = getattr(item, "Organizer", None)
        if o is not None:
            org = (getattr(o, "Name", None) or getattr(o, "Address", None) or str(o) or "").strip()
    except Exception:
        pass
    try:
        web_link = (getattr(item, "MeetingWorkspaceURL", None) or "").strip()
    except Exception:
        pass

    start_s = ""
    end_s = ""
    try:
        st = item.Start
        if isinstance(st, datetime):
            # 直接返回 naive datetime，不做时区转换
            start_s = st.replace(tzinfo=None).isoformat(sep=" ", timespec="minutes")
        else:
            start_s = str(st)
    except Exception:
        pass
    try:
        en = item.End
        if isinstance(en, datetime):
            # 直接返回 naive datetime，不做时区转换
            end_s = en.replace(tzinfo=None).isoformat(sep=" ", timespec="minutes")
        else:
            end_s = str(en)
    except Exception:
        pass

    all_day = False
    try:
        all_day = bool(item.AllDayEvent)
    except Exception:
        pass

    entry_id = ""
    try:
        # 确保 EntryID 是字符串类型
        raw_entry_id = getattr(item, "EntryID", None)
        if raw_entry_id is not None:
            entry_id = str(raw_entry_id).strip()
        # ========== 调试：EntryID 格式 ==========
        print(f"【调试：读取日程 EntryID】")
        print(f"  Subject: {subj}")
        print(f"  EntryID 原始值: {repr(raw_entry_id)}")
        print(f"  EntryID 处理后: {repr(entry_id)}")
        # ========== 调试结束 ==========
    except Exception as e:
        print(f"【调试：读取 EntryID 失败】 {e}")
        pass

    return {
        "id": entry_id,
        "subject": subj or "(无标题)",
        "start": start_s,
        "end": end_s,
        "time_zone": timezone_label,
        "is_all_day": all_day,
        "location": loc,
        "organizer": org,
        "web_link": web_link,
    }


def list_local_outlook_calendar_events(
    start_naive: datetime,
    end_naive: datetime,
    cfg: dict[str, Any],
    timezone_label: str,
) -> tuple[list[dict[str, Any]], str | None]:
    if sys.platform != "win32":
        return [], "本机 Outlook 日历仅支持在 Windows 上运行。"

    try:
        import win32com.client  # type: ignore[import-untyped]
    except ImportError:
        return [], "未安装 pywin32。请在 backend 环境执行：pip install pywin32"

    try:
        import pythoncom
        pythoncom.CoInitialize()
    except Exception:
        pass

    outlook = None
    try:
        outlook = win32com.client.Dispatch("Outlook.Application")
        
        # 禁用 Outlook 弹窗警告，避免 COM 调用卡住
        try:
            outlook.SecurityManager = None  # type: ignore
        except Exception:
            pass
        
        namespace = outlook.GetNamespace("MAPI")

        profile = (cfg.get("mapi_profile") or "").strip()
        if profile:
            try:
                namespace.Logon(profile, "", False, False)
            except Exception as e:
                return [], f"MAPI 登录失败（mapi_profile={profile!r}）：{e}"

        cal = namespace.GetDefaultFolder(OL_FOLDER_CALENDAR)
        items = cal.Items
        try:
            items.Sort("[Start]")
            items.IncludeRecurrences = True
        except Exception:
            pass

        collected_raw: list[Any] = []
        flt = _outlook_restrict_range(start_naive, end_naive)
        
        # 安全读取数量，避免 2147483647 (32位整数最大值) 的错误值
        def _safe_count(items_obj: Any) -> int:
            try:
                c = int(items_obj.Count)
                # 2147483647 是 COM 返回的错误值，表示读取失败
                if c == 2147483647 or c < 0:
                    return 0
                return c
            except Exception:
                return 0
        
        try:
            restricted = items.Restrict(flt)
            restricted.Sort("[Start]")
            cnt = _safe_count(restricted)
            if cnt > 0:
                for i in range(1, cnt + 1):
                    try:
                        collected_raw.append(restricted.Item(i))
                    except Exception:
                        continue
            else:
                # Count 返回 0 或异常时，尝试安全遍历 restricted（最多 500 个）
                for i in range(1, 501):
                    try:
                        it = restricted.Item(i)
                        if it is None:
                            break
                        if not hasattr(it, "Start"):
                            continue
                        collected_raw.append(it)
                    except Exception:
                        break
        except Exception:
            # Restrict 在部分区域格式不兼容时，退化为遍历（上限避免极慢）
            try:
                items.Sort("[Start]")
                items.IncludeRecurrences = True
            except Exception:
                pass
            total = _safe_count(items)
            # 如果安全计数返回 0 但我们确实能连接，说明 Count 属性有问题
            # 使用手动遍历来获取日程（最多 500 个）
            cap = min(max(total, 500) if total > 0 else 500, 8000)
            for i in range(1, cap + 1):
                try:
                    it = items.Item(i)
                except Exception:
                    continue
                stn = _item_start_naive(it)
                if stn is None:
                    continue
                if stn >= end_naive:
                    break
                if stn >= start_naive:
                    collected_raw.append(it)

        out: list[dict[str, Any]] = []
        seen: set[str] = set()
        for it in collected_raw:
            try:
                if not hasattr(it, "Start"):
                    continue
                eid = str(getattr(it, "EntryID", "") or "")
                dedup = eid if eid else f"_:{id(it)}"
                if dedup in seen:
                    continue
                seen.add(dedup)
                out.append(format_outlook_appointment(it, timezone_label))
            except Exception:
                continue

        out.sort(key=lambda x: x.get("start") or "")
        return out, None
    except Exception as e:
        return [], f"读取本机 Outlook 日历时出错：{e}"
    finally:
        try:
            import pythoncom
            pythoncom.CoUninitialize()
        except Exception:
            pass


def update_local_outlook_calendar_event(
    entry_id: str,
    subject: str | None,
    start: str | None,
    end: str | None,
    location: str | None,
    all_day: bool | None,
    body: str | None,
    timezone_label: str = "Asia/Shanghai",  # 默认时区
    subject_hint: str | None = None,  # 用于回退搜索的主题提示
) -> tuple[bool, str | None]:
    """
    通过 EntryID 定位并更新日历项。

    参数：
        entry_id:  日历项的唯一标识（从 list_local_outlook_calendar_events 返回的 id）
        subject:   新标题（None 表示不修改）
        start:     新开始时间 ISO 字符串，如 "2024-01-15 09:00:00"（None 表示不修改）
        end:       新结束时间 ISO 字符串，如 "2024-01-15 10:00:00"（None 表示不修改）
        location:  新地点（None 表示不修改）
        all_day:   是否全天事件（None 表示不修改）
        body:      新正文/备注（None 表示不修改）
        subject_hint: 用于回退搜索时按主题匹配（可选）

    返回：
        (success, error_or_none)
    """
    if sys.platform != "win32":
        return False, "本机 Outlook 日历仅支持在 Windows 上运行。"

    try:
        import win32com.client  # type: ignore[import-untyped]
    except ImportError:
        return False, "未安装 pywin32。请在 backend 环境执行：pip install pywin32"

    try:
        import pythoncom
        pythoncom.CoInitialize()
    except Exception:
        pass

    outlook = None
    try:
        outlook = win32com.client.Dispatch("Outlook.Application")
        namespace = outlook.GetNamespace("MAPI")

        # 使用 MAPI GetItemFromID 直接获取日程（更可靠）
        print(f"\n【调试：使用 GetItemFromID 获取日程】")
        print(f"  目标 EntryID: {repr(entry_id)}")
        
        try:
            # GetItemFromID 需要 EntryID 和可选的 Store ID
            target = namespace.GetItemFromID(entry_id)
            print(f"  -> 直接获取成功: {target.Subject}")
        except Exception as e:
            print(f"  GetItemFromID 失败: {e}")
            print(f"  回退到遍历搜索...")
            
            # 回退：遍历搜索
            cal = namespace.GetDefaultFolder(OL_FOLDER_CALENDAR)
            items = cal.Items
            items.Sort("[Start]")
            items.IncludeRecurrences = True

            target = None
            total = items.Count
            
            # ========== 调试：EntryID 查找（回退方案） ==========
            print(f"\n【调试：遍历查找日程】")
            print(f"  日历中共有 {total} 个日程")
            # ========== 调试结束 ==========
            
            # 从 update 参数中提取时间用于匹配
            update_start = None
            update_end = None
            if start:
                try:
                    from datetime import datetime
                    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
                        try:
                            update_start = datetime.strptime(start.strip(), fmt)
                            break
                        except ValueError:
                            continue
                except Exception:
                    pass
            if end:
                try:
                    from datetime import datetime
                    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
                        try:
                            update_end = datetime.strptime(end.strip(), fmt)
                            break
                        except ValueError:
                            continue
                except Exception:
                    pass
            
            print(f"  要更新的时间范围: {update_start} - {update_end}")
            
            # 首先尝试按 EntryID 匹配
            for i in range(1, min(total + 1, 10000)):
                try:
                    item = items.Item(i)
                    if hasattr(item, "EntryID"):
                        current_entry_id = str(item.EntryID) if item.EntryID else ""
                        normalized_current = current_entry_id.strip()
                        normalized_target = entry_id.strip()
                        is_match = (normalized_current == normalized_target)
                        if is_match:
                            target = item
                            print(f"  -> [EntryID 匹配] 找到日程: {item.Subject}")
                            break
                except Exception:
                    continue
            
            # 如果 EntryID 没匹配，尝试按主题提示匹配（优先）或时间匹配
            if target is None:
                print(f"\n【调试】EntryID 未匹配，尝试按主题提示或时间匹配...")
                
                # 首先尝试按主题提示匹配（如果有）
                if subject_hint:
                    print(f"  使用主题提示搜索: {subject_hint}")
                    for i in range(1, min(total + 1, 10000)):
                        try:
                            item = items.Item(i)
                            if hasattr(item, "Subject") and item.Subject:
                                # 按主题关键词匹配（不区分大小写，包含关系）
                                item_subject = item.Subject.lower()
                                hint_lower = subject_hint.lower()
                                if hint_lower in item_subject or item_subject in hint_lower:
                                    target = item
                                    print(f"  -> [主题提示匹配] 找到日程: {item.Subject}, 时间: {item.Start}")
                                    break
                        except Exception:
                            continue
                
                # 如果主题提示搜索也没找到，才按时间匹配
                if target is None and update_start is not None:
                    print(f"  主题搜索未命中，尝试按日期匹配...")
                    for i in range(1, min(total + 1, 10000)):
                        try:
                            item = items.Item(i)
                            if hasattr(item, "Subject") and hasattr(item, "Start"):
                                # 检查时间是否匹配（日期部分）
                                item_start = None
                                if hasattr(item.Start, 'year'):
                                    item_start = item.Start
                                elif isinstance(item.Start, str):
                                    try:
                                        item_start = datetime.strptime(item.Start, "%Y-%m-%d %H:%M:%S")
                                    except:
                                        try:
                                            item_start = datetime.strptime(item.Start, "%Y-%m-%d")
                                        except:
                                            pass
                                
                                if item_start and update_start:
                                    same_date = (item_start.year == update_start.year and 
                                               item_start.month == update_start.month and 
                                               item_start.day == update_start.day)
                                    if same_date:
                                        target = item
                                        print(f"  -> [日期匹配] 找到日程: {item.Subject}, 时间: {item.Start}")
                                        break
                        except Exception:
                            continue

        if target is None:
            print(f"\n【调试】未找到匹配日程")
            print(f"  可能原因：")
            print(f"  1. EntryID 是 LLM 幻觉的虚假 ID")
            print(f"  2. EntryID 已过期")
            print(f"  3. 日程不在默认日历中")
            return False, f"未找到指定的日历项（EntryID={entry_id}）。请确认该日程是否存在。"

        # 应用更新
        if subject is not None:
            target.Subject = subject
        if location is not None:
            target.Location = location
        if start is not None:
            # 支持 "YYYY-MM-DD HH:MM:SS" 和纯 "YYYY-MM-DD"（全天事件）
            parsed_start, _ = _parse_datetime(start, timezone_label)
            if parsed_start is not None:
                print(f"【调试：设置开始时间】")
                print(f"  原始输入时间: {start}")
                print(f"  转换为 UTC 后: {parsed_start}")
                
                # 检查是否有 StartUTC 属性可用
                has_start_utc = hasattr(target, 'StartUTC')
                print(f"  target 有 StartUTC 属性: {has_start_utc}")
                
                if has_start_utc:
                    # StartUTC 需要 aware UTC datetime
                    from datetime import datetime, timezone
                    utc_aware = parsed_start.replace(tzinfo=timezone.utc) if parsed_start.tzinfo is None else parsed_start.astimezone(timezone.utc)
                    target.StartUTC = utc_aware
                    print(f"  使用 StartUTC 设置 (aware UTC): {utc_aware}")
                else:
                    # 退而求其次：直接设置本地时间
                    # 解析原始时间，不转换为 UTC
                    from datetime import datetime
                    s = start.strip()
                    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
                        try:
                            local_dt = datetime.strptime(s, fmt)
                            target.Start = local_dt
                            print(f"  直接设置本地时间: {local_dt}")
                            break
                        except ValueError:
                            continue
                            
        if end is not None:
            parsed_end, _ = _parse_datetime(end, timezone_label)
            if parsed_end is not None:
                print(f"【调试：设置结束时间】")
                print(f"  原始输入时间: {end}")
                print(f"  转换为 UTC 后: {parsed_end}")
                
                has_end_utc = hasattr(target, 'EndUTC')
                print(f"  target 有 EndUTC 属性: {has_end_utc}")
                
                if has_end_utc:
                    from datetime import datetime, timezone
                    utc_aware = parsed_end.replace(tzinfo=timezone.utc) if parsed_end.tzinfo is None else parsed_end.astimezone(timezone.utc)
                    target.EndUTC = utc_aware
                    print(f"  使用 EndUTC 设置 (aware UTC): {utc_aware}")
                else:
                    from datetime import datetime
                    s = end.strip()
                    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
                        try:
                            local_dt = datetime.strptime(s, fmt)
                            target.End = local_dt
                            print(f"  直接设置本地时间: {local_dt}")
                            break
                        except ValueError:
                            continue
                            
        if all_day is not None:
            target.AllDayEvent = all_day
        if body is not None:
            target.Body = body

        print(f"【调试：应用更新后的日程时间】")
        print(f"  target.Start: {target.Start}")
        print(f"  target.End: {target.End}")
        
        target.Save()
        return True, None

    except Exception as e:
        return False, f"更新日历项失败：{e}"
    finally:
        try:
            import pythoncom
            pythoncom.CoUninitialize()
        except Exception:
            pass


def _parse_datetime(value: str, timezone_label: str = None):
    """
    把字符串解析为 datetime（全天事件时只保留日期部分）。
    
    Args:
        value: 时间字符串，如 "2026-05-14 16:00:00"
        timezone_label: 时区标签，如 "Asia/Shanghai"，如果为 None 则使用系统本地时区
    """
    from datetime import datetime, timezone, timedelta
    from zoneinfo import ZoneInfo
    
    s = value.strip()
    parsed_dt = None
    
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            parsed_dt = datetime.strptime(s, fmt)
            break
        except ValueError:
            continue
    
    if parsed_dt is None:
        return None, None
    
    # 如果是全天事件（只有日期），返回纯日期
    if len(s) <= 10:
        return parsed_dt.date() if hasattr(parsed_dt, 'date') else parsed_dt, None
    
    # ========== 调试：时区处理 ==========
    print(f"【调试：时区处理】")
    print(f"  输入时间: {s}")
    print(f"  解析后（无时区）: {parsed_dt}")
    print(f"  系统时区配置: {timezone_label}")
    # ========== 调试结束 ==========
    
    # 确定时区并进行 UTC 转换
    # Outlook 内部用 UTC 存储时间，所以需要把本地时间转为 UTC
    if timezone_label:
        try:
            tz = ZoneInfo(timezone_label)
            parsed_dt = parsed_dt.replace(tzinfo=tz)
            print(f"  添加时区后: {parsed_dt}")
            # 转换为 UTC
            utc_dt = parsed_dt.astimezone(timezone.utc).replace(tzinfo=None)
            print(f"  转换为 UTC naive: {utc_dt}")
            return utc_dt, parsed_dt.tzinfo
        except Exception as e:
            print(f"  时区转换失败: {e}")
            local_tz = datetime.now().astimezone().tzinfo
            parsed_dt = parsed_dt.replace(tzinfo=local_tz)
            utc_dt = parsed_dt.astimezone(timezone.utc).replace(tzinfo=None)
            print(f"  使用本地时区并转 UTC: {utc_dt}")
            return utc_dt, parsed_dt.tzinfo
    else:
        local_tz = datetime.now().astimezone().tzinfo
        parsed_dt = parsed_dt.replace(tzinfo=local_tz)
        utc_dt = parsed_dt.astimezone(timezone.utc).replace(tzinfo=None)
        print(f"  使用本地时区并转 UTC: {utc_dt}")
        return utc_dt, parsed_dt.tzinfo


# ============================================================================
# 创建日历事件
# ============================================================================

def create_local_outlook_calendar_event(
    subject: str,
    start: str,
    end: str,
    location: str | None = None,
    all_day: bool = False,
    body: str | None = None,
    timezone_label: str = "Asia/Shanghai",
) -> tuple[bool, str | None, str | None]:
    """
    在本机 Outlook 默认日历中创建新日程。

    参数：
        subject:     日程标题（必填）
        start:      开始时间 ISO 字符串，如 "2024-01-15 09:00:00" 或 "2024-01-15"（全天）
        end:        结束时间 ISO 字符串，如 "2024-01-15 10:00:00" 或 "2024-01-15"（全天）
        location:   地点（可选）
        all_day:    是否全天事件（默认 False）
        body:       正文/备注（可选）
        timezone_label: 时区标签，如 "Asia/Shanghai"（默认）

    返回：
        (success, error_or_none, entry_id_or_none)
    """
    if sys.platform != "win32":
        return False, "本机 Outlook 日历仅支持在 Windows 上运行。", None

    try:
        import win32com.client  # type: ignore[import-untyped]
    except ImportError:
        return False, "未安装 pywin32。请在 backend 环境执行：pip install pywin32", None

    try:
        import pythoncom
        pythoncom.CoInitialize()
    except Exception:
        pass

    outlook = None
    try:
        outlook = win32com.client.Dispatch("Outlook.Application")
        namespace = outlook.GetNamespace("MAPI")

        # 获取默认日历文件夹
        cal = namespace.GetDefaultFolder(OL_FOLDER_CALENDAR)

        # 创建新日程项
        new_item = cal.Items.Add(1)  # 1 = olAppointmentItem

        # 设置标题
        new_item.Subject = subject.strip()

        # 解析并设置开始时间
        if all_day:
            # 全天事件：只设置日期
            try:
                start_date = datetime.strptime(start.strip(), "%Y-%m-%d").date()
                new_item.Start = start_date.strftime("%Y-%m-%d")
                new_item.AllDayEvent = True
            except ValueError:
                return False, f"全天事件的开始时间格式错误：{start}，应为 YYYY-MM-DD", None

            # 结束时间
            if end:
                try:
                    end_date = datetime.strptime(end.strip(), "%Y-%m-%d").date()
                    new_item.End = end_date.strftime("%Y-%m-%d")
                except ValueError:
                    # 如果结束时间格式不对，设为开始日期 + 1 天
                    from datetime import timedelta
                    end_date = start_date + timedelta(days=1)
                    new_item.End = end_date.strftime("%Y-%m-%d")
            else:
                # 默认结束时间 = 开始时间
                new_item.End = new_item.Start
        else:
            # 非全天事件：设置具体时间
            start_str = start.strip()
            end_str = end.strip()
            
            # 格式化时间字符串，确保格式正确
            # 使用字符串格式直接赋值，避免 datetime 对象的时区问题
            try:
                datetime.strptime(start_str, "%Y-%m-%d %H:%M:%S")
            except ValueError:
                try:
                    datetime.strptime(start_str, "%Y-%m-%d %H:%M")
                    start_str = start_str + ":00"
                except ValueError:
                    return False, f"开始时间格式错误：{start}", None
            
            try:
                datetime.strptime(end_str, "%Y-%m-%d %H:%M:%S")
            except ValueError:
                try:
                    datetime.strptime(end_str, "%Y-%m-%d %H:%M")
                    end_str = end_str + ":00"
                except ValueError:
                    # 如果结束时间格式不对，设为开始时间 + 1 小时
                    start_dt = datetime.strptime(start_str, "%Y-%m-%d %H:%M:%S")
                    end_dt = start_dt + timedelta(hours=1)
                    end_str = end_dt.strftime("%Y-%m-%d %H:%M:%S")
            
            print(f"  设置 Start 字符串: {start_str}")
            print(f"  设置 End 字符串: {end_str}")
            new_item.Start = start_str
            new_item.End = end_str

        # 设置地点
        if location:
            new_item.Location = location.strip()

        # 设置正文
        if body:
            new_item.Body = body.strip()

        # 保存日程
        new_item.Save()

        # 获取新创建的日程的 EntryID
        entry_id = None
        try:
            entry_id = str(new_item.EntryID) if new_item.EntryID else None
        except Exception:
            pass

        print(f"【调试：创建日历事件】")
        print(f"  标题: {subject}")
        print(f"  开始时间: {new_item.Start}")
        print(f"  结束时间: {new_item.End}")
        print(f"  全天事件: {new_item.AllDayEvent}")
        print(f"  EntryID: {entry_id}")

        return True, None, entry_id

    except Exception as e:
        return False, f"创建日历事件失败：{e}", None
    finally:
        try:
            import pythoncom
            pythoncom.CoUninitialize()
        except Exception:
            pass


# ============================================================================
# 创建邮件草稿
# ============================================================================

def create_outlook_email_draft(
    to: str,
    subject: str,
    body: str,
    cc: str | None = None,
    bcc: str | None = None,
) -> tuple[bool, str | None, str | None]:
    """
    在本机 Outlook 默认邮箱的草稿箱中创建邮件草稿。

    参数：
        to:      收件人邮箱地址（必填）
        subject: 邮件主题（必填）
        body:    邮件正文（必填）
        cc:      抄送地址（可选）
        bcc:     密送地址（可选）

    返回：
        (success, error_or_none, entry_id_or_none)
    """
    if sys.platform != "win32":
        return False, "本机 Outlook 邮件仅支持在 Windows 上运行。", None

    try:
        import win32com.client  # type: ignore[import-untyped]
    except ImportError:
        return False, "未安装 pywin32。请在 backend 环境执行：pip install pywin32", None

    try:
        import pythoncom
        pythoncom.CoInitialize()
    except Exception:
        pass

    outlook = None
    try:
        outlook = win32com.client.Dispatch("Outlook.Application")
        namespace = outlook.GetNamespace("MAPI")

        # 获取默认草稿箱文件夹
        drafts_folder = namespace.GetDefaultFolder(OL_FOLDER_DRAFTS)

        # 创建新邮件项 (olMailItem = 0)
        new_item = drafts_folder.Items.Add(0)  # 0 = olMailItem

        # 设置收件人
        new_item.To = to.strip()

        # 设置主题
        new_item.Subject = subject.strip()

        # 设置正文
        new_item.Body = body.strip()

        # 设置抄送
        if cc and cc.strip():
            new_item.CC = cc.strip()

        # 设置密送
        if bcc and bcc.strip():
            new_item.BCC = bcc.strip()

        # 保存到草稿箱
        new_item.Save()

        # 获取草稿的 EntryID
        entry_id = None
        try:
            entry_id = str(new_item.EntryID) if new_item.EntryID else None
        except Exception:
            pass

        print(f"【调试：创建 Outlook 邮件草稿】")
        print(f"  收件人: {to}")
        print(f"  主题: {subject}")
        print(f"  位置: 草稿箱")
        print(f"  EntryID: {entry_id}")

        return True, None, entry_id

    except Exception as e:
        return False, f"创建邮件草稿失败：{e}", None
    finally:
        try:
            import pythoncom
            pythoncom.CoUninitialize()
        except Exception:
            pass

"""
Microsoft Outlook：通过 Windows 本机 COM（pywin32）读取默认日历。
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Type

from pydantic import BaseModel, Field, field_validator

from ..outlook_local import (
    _calendar_config_path,
    compute_window_local,
    list_local_outlook_calendar_events,
    load_calendar_config,
    update_local_outlook_calendar_event,
    create_local_outlook_calendar_event,
)
from .base import BaseTool


class ListOutlookCalendarEventsInput(BaseModel):
    """列出本机 Outlook 默认日历中一段时间内的日程（COM，无需 Azure）。"""

    days: int = Field(
        default=30,
        ge=1,
        le=31,
        description="从起始日 0 点起向后查询的天数（默认 30，约一个月；最大 31）",
    )
    timezone: str = Field(
        default="",
        description="IANA 时区名（如 Asia/Shanghai）；留空则使用 calendar_config.toml 中的 timezone",
    )
    start_date: str = Field(
        default="",
        description="窗口起始日期 YYYY-MM-DD，按上述时区的当天 0 点；留空表示「今天」",
    )

    @field_validator("start_date")
    @classmethod
    def validate_date_format(cls, v: str) -> str:
        """验证 start_date 要么为空，要么为 YYYY-MM-DD 格式。"""
        s = v.strip()
        if not s:
            return ""
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
            raise ValueError(
                "start_date 须为 YYYY-MM-DD 格式（如 2024-01-01），或留空表示今天。"
            )
        try:
            datetime.strptime(s, "%Y-%m-%d")
        except ValueError:
            raise ValueError(
                f"日期 {s!r} 不存在。请检查年份、月份、日期是否有效。"
            )
        return s


class ListOutlookCalendarEventsOutput(BaseModel):
    success: bool
    error: str = ""
    events: list[dict[str, Any]] = Field(default_factory=list)
    range_description: str = ""
    count: int = 0


class ListOutlookCalendarEventsTool(BaseTool):
    @property
    def name(self) -> str:
        return "list_outlook_calendar_events"

    @property
    def description(self) -> str:
        return """列出本机 Microsoft Outlook 默认日历中的会议/日程（只读）。

通过 Windows 上已安装的 Outlook 桌面客户端（pywin32 COM）读取，**不需要** Azure 注册或登录脚本。
时间范围默认从「今天 0 点」起约一个月（可用 days、start_date、timezone 调整）。

前提：在 Windows 运行；已安装并配置 Outlook；已 pip install pywin32；可选填写 config/calendar_config.toml 中的 timezone / mapi_profile。"""

    @property
    def input_schema(self) -> Type[BaseModel]:
        return ListOutlookCalendarEventsInput

    def execute(self, days: int = 30, timezone: str = "", start_date: str = "") -> dict:
        cfg = load_calendar_config()
        if not cfg:
            return ListOutlookCalendarEventsOutput(
                success=False,
                error=(
                    f"未找到日历配置：{_calendar_config_path()}。"
                    "请将 config/calendar_config.example.toml 复制为 calendar_config.toml。"
                ),
            ).model_dump()

        # 时区：优先用参数，其次用配置文件，最后默认 UTC
        if timezone.strip():
            tz_label = timezone.strip()
        else:
            cfg_tz = cfg.get("timezone")
            tz_label = (cfg_tz if cfg_tz else "UTC").strip() or "UTC"

        try:
            start_naive, end_naive, desc = compute_window_local(tz_label, days, start_date)
        except ValueError as e:
            return ListOutlookCalendarEventsOutput(
                success=False,
                error=f"{e}（你传入的值是：{start_date!r}）",
            ).model_dump()
        except Exception as e:
            return ListOutlookCalendarEventsOutput(
                success=False,
                error=f"解析时间范围失败：{e}",
            ).model_dump()

        events, err = list_local_outlook_calendar_events(
            start_naive, end_naive, cfg, timezone_label=tz_label
        )
        if err:
            return ListOutlookCalendarEventsOutput(success=False, error=err).model_dump()

        return ListOutlookCalendarEventsOutput(
            success=True,
            events=events,
            range_description=desc,
            count=len(events),
        ).model_dump()


list_outlook_calendar_events_tool = ListOutlookCalendarEventsTool()


# ============================================================================
# 日历更新工具
# ============================================================================


class UpdateCalendarEventInput(BaseModel):
    """更新日历工具的输入参数"""

    entry_id: str = Field(
        description="""要更新的日历项的唯一标识（EntryID）。
        
        重要：必须从 list_outlook_calendar_events 返回的事件 id 中获取。
        不能随意填写，必须是有效的日历项 ID。"""
    )
    subject: str | None = Field(
        default=None,
        description="""新的日程标题（可选）。
        
        - 留空或 None 表示不修改标题"""
    )
    start: str | None = Field(
        default=None,
        description="""新的开始时间（可选）。
        
        格式要求：
        - 完整格式：YYYY-MM-DD HH:MM:SS（如 2024-01-15 09:00:00）
        - 日期格式：YYYY-MM-DD（全天事件时，如 2024-01-15）
        - 留空或 None 表示不修改开始时间"""
    )
    end: str | None = Field(
        default=None,
        description="""新的结束时间（可选）。
        
        格式要求：
        - 完整格式：YYYY-MM-DD HH:MM:SS（如 2024-01-15 10:00:00）
        - 日期格式：YYYY-MM-DD（全天事件时，如 2024-01-15）
        - 留空或 None 表示不修改结束时间"""
    )
    location: str | None = Field(
        default=None,
        description="""新的会议地点（可选）。
        
        - 留空或 None 表示不修改地点"""
    )
    all_day: bool | None = Field(
        default=None,
        description="""是否为全天事件（可选）。
        
        - true: 全天事件（只保留日期，时分秒为 00:00）
        - false: 非全天事件
        - None 或不填: 不修改"""
    )
    body: str | None = Field(
        default=None,
        description="""新的日程备注/正文内容（可选）。
        
        - 留空或 None 表示不修改备注"""
    )
    subject_hint: str | None = Field(
        default=None,
        description="""主题提示（可选，用于回退搜索）。
        
        当 entry_id 无效或过期时，系统会使用此字段按主题搜索匹配的日程。
        例如：如果要修改"修车"日程，填写"修车"。
        不影响实际更新操作，只用于找不到日程时的备选搜索。"""
    )


class UpdateCalendarEventOutput(BaseModel):
    """更新日历工具的输出"""
    success: bool
    error: str = ""


class UpdateCalendarEventTool(BaseTool):
    """
    更新本机 Outlook 日历中的日程项

    功能：
    - 根据 EntryID 定位并更新日历项
    - 支持更新标题、时间、地点、全天标记、备注
    - 需要用户先通过 list_outlook_calendar_events 获取有效的 EntryID
    """

    @property
    def name(self) -> str:
        return "update_outlook_calendar_event"

    @property
    def description(self) -> str:
        return """更新本机 Microsoft Outlook 日历中的日程项。

适用场景：
- 用户想修改某个日程的时间
- 用户想修改某个日程的标题、地点等
- 用户想将普通会议改为全天事件

前置条件：
1. 必须先通过 list_outlook_calendar_events 获取要更新的日程的 EntryID
2. EntryID 是日历项的唯一标识，不能随意填写

输入参数：
- entry_id: 日历项的唯一标识（必填，必须有效）
- subject: 新标题（可选）
- start: 新开始时间 YYYY-MM-DD HH:MM:SS（可选）
- end: 新结束时间 YYYY-MM-DD HH:MM:SS（可选）
- location: 新地点（可选）
- all_day: 是否全天事件 true/false（可选）
- body: 新备注内容（可选）

注意：
- 至少需要填写一个要修改的字段（subject/start/end/location/all_day/body）
- 必须是本机已安装并配置好的 Outlook
- 仅修改默认日历中的日程"""

    @property
    def input_schema(self) -> Type[BaseModel]:
        return UpdateCalendarEventInput

    def execute(
        self,
        entry_id: str,
        subject: str | None = None,
        start: str | None = None,
        end: str | None = None,
        location: str | None = None,
        all_day: bool | None = None,
        body: str | None = None,
        subject_hint: str | None = None,
    ) -> dict:
        """执行日历项更新"""
        if not entry_id or not entry_id.strip():
            return UpdateCalendarEventOutput(
                success=False,
                error="entry_id 不能为空。必须先通过 list_outlook_calendar_events 获取有效的日程 ID。"
            ).model_dump()

        if all(v is None for v in [subject, start, end, location, all_day, body]):
            return UpdateCalendarEventOutput(
                success=False,
                error="至少需要提供一个要修改的字段（subject/start/end/location/all_day/body）。"
            ).model_dump()

        # 加载配置以获取时区设置
        cfg = load_calendar_config()
        timezone_label = cfg.get("timezone", "Asia/Shanghai") if cfg else "Asia/Shanghai"

        success, err = update_local_outlook_calendar_event(
            entry_id=entry_id.strip(),
            subject=subject.strip() if isinstance(subject, str) else None,
            start=start.strip() if isinstance(start, str) else None,
            end=end.strip() if isinstance(end, str) else None,
            location=location.strip() if isinstance(location, str) else None,
            all_day=all_day,
            body=body.strip() if isinstance(body, str) else None,
            timezone_label=timezone_label,
            subject_hint=subject_hint.strip() if isinstance(subject_hint, str) else None,
        )

        if success:
            return UpdateCalendarEventOutput(success=True).model_dump()
        else:
            return UpdateCalendarEventOutput(success=False, error=err or "未知错误").model_dump()


class UpdateCalendarEventPreviewTool(BaseTool):
    """
    更新 Outlook 日历预览工具（模拟执行，返回待确认内容）
    """

    @property
    def name(self) -> str:
        # 名称与实际工具一致，以便 _execute_tool 正确查找
        return "update_outlook_calendar_event"

    @property
    def description(self) -> str:
        return """预览更新 Outlook 日历操作（待用户确认）。

此工具会检查参数是否合法，并返回将要执行的操作详情。
实际更新操作需要用户确认后才能执行。

适用场景：
- 用户想修改某个日程的时间、标题、地点等
- 需要用户在执行前确认修改内容

输入参数：
- entry_id: 日历项的唯一标识（必填）
- subject: 新标题（可选）
- start: 新开始时间（可选）
- end: 新结束时间（可选）
- location: 新地点（可选）
- all_day: 是否全天事件（可选）
- body: 新备注内容（可选）

返回：
- 操作预览信息，包括要修改的字段和当前值对比
- 用户需确认后才真正执行更新"""

    @property
    def input_schema(self) -> Type[BaseModel]:
        return UpdateCalendarEventInput

    def execute(
        self,
        entry_id: str,
        subject: str | None = None,
        start: str | None = None,
        end: str | None = None,
        location: str | None = None,
        all_day: bool | None = None,
        body: str | None = None,
        subject_hint: str | None = None,  # 用于回退搜索（预览工具忽略此参数）
    ) -> dict:
        """
        模拟执行日历更新，返回预览信息
        """
        # 参数验证
        if not entry_id or not entry_id.strip():
            return {
                "success": False,
                "error": "entry_id 不能为空。必须先通过 list_outlook_calendar_events 获取有效的日程 ID。",
                "preview": None
            }

        if all(v is None for v in [subject, start, end, location, all_day, body]):
            return {
                "success": False,
                "error": "至少需要提供一个要修改的字段（subject/start/end/location/all_day/body）。",
                "preview": None
            }

        # 收集要修改的字段
        changes = []
        if subject is not None:
            changes.append({"field": "标题", "new_value": subject})
        if start is not None:
            changes.append({"field": "开始时间", "new_value": start})
        if end is not None:
            changes.append({"field": "结束时间", "new_value": end})
        if location is not None:
            changes.append({"field": "地点", "new_value": location})
        if all_day is not None:
            changes.append({"field": "全天事件", "new_value": "是" if all_day else "否"})
        if body is not None:
            changes.append({"field": "备注", "new_value": body[:100] + ("..." if len(body) > 100 else "")})

        return {
            "success": True,
            "preview": {
                "operation": "update_calendar_event",
                "entry_id": entry_id.strip(),
                "changes": changes,
                "will_update": True
            }
        }


# 创建工具实例
update_calendar_event_tool = UpdateCalendarEventTool()
update_calendar_preview_tool = UpdateCalendarEventPreviewTool()


# ============================================================================
# 日历创建工具
# ============================================================================


class CreateCalendarEventInput(BaseModel):
    """创建日历事件工具的输入参数"""

    subject: str = Field(
        description="""日程标题/主题（必填）。

        重要：
        - 标题应清晰描述日程内容
        - 例如："团队周会"、"与客户电话沟通"、"项目评审" """
    )
    start: str = Field(
        description="""开始时间（必填）。

        格式要求：
        - 完整格式：YYYY-MM-DD HH:MM:SS（如 2024-01-15 09:00:00）
        - 短格式：YYYY-MM-DD HH:MM（如 2024-01-15 09:00）
        - 日期格式：YYYY-MM-DD（全天事件时，如 2024-01-15）

        示例：
        - "2024-01-15 09:00:00" - 1月15日上午9点
        - "2024-01-15" - 1月15日整天"""
    )
    end: str = Field(
        description="""结束时间（必填）。

        格式要求：
        - 完整格式：YYYY-MM-DD HH:MM:SS（如 2024-01-15 10:00:00）
        - 短格式：YYYY-MM-DD HH:MM（如 2024-01-15 10:00）
        - 日期格式：YYYY-MM-DD（全天事件时，如 2024-01-15）

        示例：
        - "2024-01-15 10:00:00" - 1月15日上午10点
        - "2024-01-15" - 1月15日整天（如果设置全天事件）"""
    )
    location: str | None = Field(
        default=None,
        description="""会议地点（可选）。

        - 例如："会议室A"、"线上会议"、"咖啡厅"
        - 留空表示无地点"""
    )
    all_day: bool = Field(
        default=False,
        description="""是否为全天事件。

        - true: 全天事件（只保留日期，时分秒为 00:00）
        - false: 普通会议事件（有具体时间）"""
    )
    body: str | None = Field(
        default=None,
        description="""日程备注/正文内容（可选）。

        - 可以填写会议议程、注意事项等
        - 留空表示无备注"""
    )


class CreateCalendarEventOutput(BaseModel):
    """创建日历事件工具的输出"""
    success: bool
    error: str = ""
    entry_id: str | None = Field(default=None, description="创建的日程的唯一标识")


class CreateCalendarEventTool(BaseTool):
    """
    在本机 Outlook 默认日历中创建新日程

    功能：
    - 创建新的日历事件/会议
    - 支持设置标题、时间、地点、是否全天、备注
    - 创建一个新的日程条目到 Outlook 日历

    适用场景：
    - 用户说"创建日程"、"新建会议"、"添加日历事件"
    - 需要安排新的会议或活动
    """

    @property
    def name(self) -> str:
        return "create_outlook_calendar_event"

    @property
    def description(self) -> str:
        return """在本机 Microsoft Outlook 日历中创建新的日程/会议。

适用场景：
- 用户想创建新的日程、会议、事件
- 用户说"创建日程"、"新建会议"、"添加日历事件"、"安排会议"
- 需要在日历上添加新的时间安排

输入参数：
- subject: 日程标题（必填）
- start: 开始时间 YYYY-MM-DD HH:MM:SS（必填）
- end: 结束时间 YYYY-MM-DD HH:MM:SS（必填）
- location: 地点（可选）
- all_day: 是否全天事件 true/false（默认 false）
- body: 备注内容（可选）

注意：
- 必须是本机已安装并配置好的 Outlook
- 仅在默认日历中创建日程"""

    @property
    def input_schema(self) -> Type[BaseModel]:
        return CreateCalendarEventInput

    def execute(
        self,
        subject: str,
        start: str,
        end: str,
        location: str | None = None,
        all_day: bool = False,
        body: str | None = None,
    ) -> dict:
        """执行创建日历事件"""
        if not subject or not subject.strip():
            return CreateCalendarEventOutput(
                success=False,
                error="日程标题不能为空。"
            ).model_dump()

        if not start or not start.strip():
            return CreateCalendarEventOutput(
                success=False,
                error="开始时间不能为空。"
            ).model_dump()

        if not end or not end.strip():
            return CreateCalendarEventOutput(
                success=False,
                error="结束时间不能为空。"
            ).model_dump()

        # 加载配置以获取时区设置
        cfg = load_calendar_config()
        timezone_label = cfg.get("timezone", "Asia/Shanghai") if cfg else "Asia/Shanghai"

        success, err, entry_id = create_local_outlook_calendar_event(
            subject=subject.strip(),
            start=start.strip(),
            end=end.strip(),
            location=location.strip() if isinstance(location, str) else None,
            all_day=all_day,
            body=body.strip() if isinstance(body, str) else None,
            timezone_label=timezone_label,
        )

        if success:
            return CreateCalendarEventOutput(
                success=True,
                entry_id=entry_id
            ).model_dump()
        else:
            return CreateCalendarEventOutput(
                success=False,
                error=err or "未知错误"
            ).model_dump()


class CreateCalendarEventPreviewTool(BaseTool):
    """
    创建 Outlook 日历预览工具（模拟执行，返回待确认内容）
    """

    @property
    def name(self) -> str:
        # 名称必须与实际工具一致，以便 _execute_tool 正确查找
        return "create_outlook_calendar_event"

    @property
    def description(self) -> str:
        return """预览创建 Outlook 日历操作（待用户确认）。

此工具会检查参数是否合法，并返回将要执行的操作详情。
实际创建操作需要用户确认后才能执行。

适用场景：
- 用户想创建新的日程、会议
- 需要用户在执行前确认创建内容

输入参数：
- subject: 日程标题（必填）
- start: 开始时间（必填）
- end: 结束时间（必填）
- location: 地点（可选）
- all_day: 是否全天事件（可选）
- body: 备注内容（可选）

返回：
- 操作预览信息，包括要创建的日程详情
- 用户需确认后才真正创建"""

    @property
    def input_schema(self) -> Type[BaseModel]:
        return CreateCalendarEventInput

    def execute(
        self,
        subject: str,
        start: str,
        end: str,
        location: str | None = None,
        all_day: bool = False,
        body: str | None = None,
    ) -> dict:
        """
        模拟执行日历创建，返回预览信息
        """
        # 参数验证
        if not subject or not subject.strip():
            return {
                "success": False,
                "error": "日程标题不能为空。",
                "preview": None
            }

        if not start or not start.strip():
            return {
                "success": False,
                "error": "开始时间不能为空。",
                "preview": None
            }

        if not end or not end.strip():
            return {
                "success": False,
                "error": "结束时间不能为空。",
                "preview": None
            }

        # 收集创建信息
        event_info = []
        event_info.append({"field": "标题", "value": subject.strip()})
        event_info.append({"field": "开始时间", "value": start.strip()})
        event_info.append({"field": "结束时间", "value": end.strip()})

        if location:
            event_info.append({"field": "地点", "value": location.strip()})
        if all_day:
            event_info.append({"field": "全天事件", "value": "是"})
        if body:
            body_preview = body.strip()[:100] + ("..." if len(body.strip()) > 100 else "")
            event_info.append({"field": "备注", "value": body_preview})

        return {
            "success": True,
            "preview": {
                "operation": "create_calendar_event",
                "subject": subject.strip(),
                "start": start.strip(),
                "end": end.strip(),
                "location": location.strip() if isinstance(location, str) else None,
                "all_day": all_day,
                "body": body.strip() if isinstance(body, str) else None,
                "event_info": event_info,
                "will_create": True
            }
        }


# 创建工具实例
create_calendar_event_tool = CreateCalendarEventTool()
create_calendar_preview_tool = CreateCalendarEventPreviewTool()

"""
通过 IMAP 读取邮箱（配置来自 config/email_config.toml）。
"""
from __future__ import annotations

import imaplib
import os
import re
from email import message_from_bytes
from email.header import decode_header, make_header
from email.message import Message
from pathlib import Path
from typing import Any, Type

import tomllib
from pydantic import BaseModel, Field

from .base import BaseTool


def _config_path() -> Path:
    override = os.environ.get("EMAIL_CONFIG_PATH", "").strip()
    if override:
        return Path(override)
    backend_root = Path(__file__).resolve().parents[2]
    return backend_root / "config" / "email_config.toml"


def load_email_config() -> dict[str, Any] | None:
    path = _config_path()
    if not path.is_file():
        return None
    with open(path, "rb") as f:
        return tomllib.load(f)


def _imap_ok(typ: str | bytes | None) -> bool:
    if typ is None:
        return False
    if isinstance(typ, bytes):
        return typ.upper() == b"OK"
    return str(typ).upper() == "OK"


def _imap_data_text(data: Any, max_len: int = 800) -> str:
    """将 IMAP 命令返回的 data 转成可读字符串，便于排错。"""
    if not data:
        return ""
    parts: list[str] = []
    if isinstance(data, (list, tuple)):
        for item in data:
            parts.append(_imap_data_text(item, max_len=max_len))
    elif isinstance(data, bytes):
        parts.append(data.decode(errors="replace").strip())
    else:
        parts.append(str(data).strip())
    out = " | ".join(p for p in parts if p)
    return out[:max_len]


def _select_mailbox(imap: imaplib.IMAP4, mb: str) -> tuple[bool, str, Any]:
    """
    选择邮件夹。先尝试只读 EXAMINE，失败再尝试 SELECT。
    返回 (是否成功, 实际使用的邮件夹名, 最后一次 typ/data 供排错)。
    """
    candidates = [mb]
    # 部分服务器对大小写或引号敏感；INBOX 再试两种变体
    if mb.upper() == "INBOX":
        for alt in ("inbox", "Inbox"):
            if alt not in candidates:
                candidates.append(alt)

    last_typ: Any = None
    last_data: Any = None
    for name in candidates:
        for readonly in (True, False):
            typ, data = imap.select(name, readonly=readonly)
            last_typ, last_data = typ, data
            if _imap_ok(typ):
                return True, name, (typ, data)
    return False, mb, (last_typ, last_data)


def _list_mailboxes_preview(imap: imaplib.IMAP4, max_lines: int = 25) -> str:
    try:
        typ, data = imap.list()
        if not _imap_ok(typ) or not data:
            return ""
        lines: list[str] = []
        for row in data[:max_lines]:
            if isinstance(row, bytes):
                lines.append(row.decode(errors="replace").strip())
            else:
                lines.append(str(row).strip())
        extra = ""
        if len(data) > max_lines:
            extra = f" …（共 {len(data)} 条，仅显示前 {max_lines} 条）"
        return "；LIST 返回：" + " | ".join(lines) + extra
    except Exception:
        return ""


# 网易系 IMAP 要求在 LOGIN 之后、SELECT 之前发送 ID（RFC 2971），否则返回「Unsafe Login」。
_NETEASE_IMAP_HOST_MARKERS = ("163.com", "126.com", "188.com", "yeah.net")


def _should_send_imap_id(host: str, cfg: dict[str, Any]) -> bool:
    """是否发送 IMAP ID：配置 send_imap_id 优先；未配置时按 imap_host 自动识别网易。"""
    v = cfg.get("send_imap_id")
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        s = v.strip().lower()
        if s in ("true", "1", "yes", "on"):
            return True
        if s in ("false", "0", "no", "off"):
            return False
    h = (host or "").lower()
    return any(m in h for m in _NETEASE_IMAP_HOST_MARKERS)


def _imap_id_quoted_value(s: str, default: str) -> str:
    t = (s or "").strip() or default
    # IMAP quoted 字符串内避免反斜杠与双引号破坏语法
    t = t.replace("\\", "/").replace('"', "'")
    return t[:200] if len(t) > 200 else t


def _send_imap_id_command(imap: imaplib.IMAP4, cfg: dict[str, Any]) -> tuple[Any, Any]:
    """发送 IMAP ID；需在 AUTH 状态下、select 之前调用。"""
    imaplib.Commands["ID"] = ("AUTH", "SELECTED")
    name = _imap_id_quoted_value(str(cfg.get("imap_client_name", "")), "LocalSuperAgent")
    version = _imap_id_quoted_value(str(cfg.get("imap_client_version", "")), "1.0")
    vendor = _imap_id_quoted_value(str(cfg.get("imap_client_vendor", "")), "agent-backend")
    client_id = ("name", name, "version", version, "vendor", vendor)
    arg = '("' + '" "'.join(client_id) + '")'
    return imap._simple_command("ID", arg)


def _maybe_send_imap_id(imap: imaplib.IMAP4, host: str, cfg: dict[str, Any]) -> str | None:
    """
    网易系邮箱发送 ID。若已判定需要发送但 ID 失败，返回错误文案（应中止后续 SELECT）。
    不需要发送时返回 None。
    """
    if not _should_send_imap_id(host, cfg):
        return None
    typ, dat = _send_imap_id_command(imap, cfg)
    if not _imap_ok(typ):
        return (
            "IMAP ID 声明失败（网易 163/126/188/yeah 等通常必须在 SELECT 前成功发送 ID）："
            f"typ={typ!r}，{_imap_data_text(dat)}"
        )
    return None


def _decode_mime_header(value: str | None) -> str:
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return value


def _get_body_preview(msg: Message, max_chars: int = 2000) -> str:
    if msg.is_multipart():
        for part in msg.walk():
            ctype = part.get_content_type()
            if ctype == "text/plain" and "attachment" not in (part.get("Content-Disposition") or ""):
                payload = part.get_payload(decode=True)
                if isinstance(payload, bytes):
                    charset = part.get_content_charset() or "utf-8"
                    try:
                        text = payload.decode(charset, errors="replace")
                    except Exception:
                        text = payload.decode("utf-8", errors="replace")
                    return text[:max_chars] + ("…" if len(text) > max_chars else "")
    else:
        if msg.get_content_type() == "text/plain":
            payload = msg.get_payload(decode=True)
            if isinstance(payload, bytes):
                charset = msg.get_content_charset() or "utf-8"
                try:
                    text = payload.decode(charset, errors="replace")
                except Exception:
                    text = payload.decode("utf-8", errors="replace")
                return text[:max_chars] + ("…" if len(text) > max_chars else "")
    return ""


class ReadEmailInput(BaseModel):
    """读取邮件（IMAP）工具参数"""

    limit: int = Field(
        default=20,
        ge=1,
        le=100,
        description="最多返回多少封邮件（按时间从新到旧）",
    )
    unread_only: bool = Field(
        default=False,
        description="为 true 时只搜索未读邮件",
    )
    subject_contains: str = Field(
        default="",
        description=(
            "若非空，仅返回主题中包含该子串的邮件（不区分大小写）。"
            "用户未明确提到「按主题筛选」时不要填写；拼写需与主题一致（如 DCloud 不要写成 DClound）。"
            "筛选时从较新的邮件向旧扫描，最多检视约数千封以内。"
        ),
    )
    from_contains: str = Field(
        default="",
        description=(
            "若非空，仅返回发件人中包含该子串的邮件（不区分大小写）。"
            "可用于按发件人姓名、邮箱或域名筛选，如 '王一凡'、'@example.com' 或 'gmail'。"
            "当用户提到「某人的邮件」「某人发的邮件」时填写此字段。"
        ),
    )
    mailbox: str = Field(
        default="",
        description="邮件夹名称；留空则使用配置文件中的 mailbox（通常为 INBOX）",
    )
    date_since: str = Field(
        default="",
        description=(
            "只返回指定日期之后的邮件。格式：DD-MMM-YYYY（如 01-Jan-2024）。"
            "当用户提到「最近一周」「最近一个月」「今天」「昨天」等时，填写此参数。"
            "今天是自动计算的相对日期，需要转换为具体日期。"
        ),
    )


class ReadEmailOutput(BaseModel):
    success: bool
    error: str = ""
    messages: list[dict[str, Any]] = Field(default_factory=list)
    mailbox: str = ""
    total_fetched: int = 0
    # 便于区分「收件箱为空」与「有邮件但筛选/窗口内无匹配」
    hint: str = ""
    subject_filter: str = ""
    from_filter: str = ""
    scanned_in_folder: int = 0


class ReadEmailTool(BaseTool):
    """通过 IMAP 列出并读取邮件摘要与正文预览。"""

    @property
    def name(self) -> str:
        return "read_email"

    @property
    def description(self) -> str:
        return """通过 IMAP 读取邮箱中的邮件（主题、发件人、日期、正文预览）。

适用场景：
- 用户想查看收件箱或未读邮件
- 用户想按主题关键词筛选邮件
- 用户想按发件人筛选邮件

前提：已在项目 backend/config/email_config.toml 中配置 IMAP（可由 email_config.example.toml 复制后填写）。

参数：
- limit: 最多返回几封（默认 20，最大 100）
- unread_only: 是否仅未读
- subject_contains: 仅当用户明确要按主题筛选时填写；未提及时留空，否则易因拼写不一致得到 0 封
- from_contains: 当用户提到「某人的邮件」「谁发的」时填写；可按姓名、邮箱或域名筛选
- mailbox: 邮件夹，默认用配置文件中的值（一般为 INBOX）"""

    @property
    def input_schema(self) -> Type[BaseModel]:
        return ReadEmailInput

    def execute(
        self,
        limit: int = 20,
        unread_only: bool = False,
        subject_contains: str = "",
        from_contains: str = "",
        mailbox: str = "",
        date_since: str = "",
    ) -> dict:
        cfg = load_email_config()
        if not cfg:
            return ReadEmailOutput(
                success=False,
                error=(
                    f"未找到邮件配置文件：{_config_path()}。"
                    "请将 config/email_config.example.toml 复制为 email_config.toml 并填写 IMAP 参数。"
                ),
            ).model_dump()

        host = (cfg.get("imap_host") or "").strip()
        username = (cfg.get("username") or "").strip()
        password = (cfg.get("password") or "").strip()
        if not host or not username or not password:
            return ReadEmailOutput(
                success=False,
                error="email_config.toml 中 imap_host、username、password 不能为空。",
            ).model_dump()

        port = int(cfg.get("imap_port", 993))
        use_ssl = bool(cfg.get("use_ssl", True))
        use_starttls = bool(cfg.get("use_starttls", False))
        timeout = int(cfg.get("timeout_seconds", 30))
        default_limit = int(cfg.get("default_fetch_limit", 20))
        mb = (mailbox or "").strip() or (cfg.get("mailbox") or "INBOX").strip() or "INBOX"

        if limit <= 0:
            limit = default_limit
        limit = min(limit, 100)

        imap: imaplib.IMAP4
        try:
            if use_ssl:
                imap = imaplib.IMAP4_SSL(host, port, timeout=timeout)
            else:
                imap = imaplib.IMAP4(host, port, timeout=timeout)
                if use_starttls:
                    imap.starttls()

            imap.login(username, password)

            id_err = _maybe_send_imap_id(imap, host, cfg)
            if id_err:
                try:
                    imap.logout()
                except Exception:
                    pass
                return ReadEmailOutput(
                    success=False,
                    error=id_err,
                    mailbox=mb,
                ).model_dump()

            ok_select, mb_used, (sel_typ, sel_data) = _select_mailbox(imap, mb)
            if not ok_select:
                detail = _imap_data_text(sel_data)
                reason = f"服务器响应 typ={sel_typ!r}"
                if detail:
                    reason += f"，详情：{detail}"
                list_hint = _list_mailboxes_preview(imap)
                try:
                    imap.logout()
                except Exception:
                    pass
                return ReadEmailOutput(
                    success=False,
                    error=(
                        f"无法选择邮件夹：{mb}（{reason}）。"
                        "请核对 config 中的 mailbox 是否与服务商一致；"
                        "常见为 INBOX，部分账号需在网页端开启 IMAP 后使用授权码登录。"
                        "若提示 Unsafe Login 且为网易（163/126/188/yeah），请确认 imap_host 含对应域名以便自动发送 IMAP ID，或设置 send_imap_id = true。"
                        f"{list_hint}"
                    ),
                    mailbox=mb,
                ).model_dump()
            mb = mb_used

            criterion = "UNSEEN" if unread_only else "ALL"

            # 处理日期筛选
            search_criteria = []
            if unread_only:
                search_criteria.append("UNSEEN")
            if date_since:
                search_criteria.append(f"SINCE {date_since}")

            # 构建搜索 criterion
            if search_criteria:
                criterion = " ".join(search_criteria)
            else:
                criterion = "ALL"

            typ, data = imap.search(None, criterion)
            if typ != "OK" or not data or not data[0]:
                imap.logout()
                return ReadEmailOutput(
                    success=True,
                    messages=[],
                    mailbox=mb,
                    total_fetched=0,
                    hint="服务器 SEARCH 未返回任何邮件 UID（该邮件夹可能为空）。",
                    subject_filter=(subject_contains or "").strip(),
                    scanned_in_folder=0,
                ).model_dump()

            id_list = data[0].split()
            n_total = len(id_list)
            subject_key = (subject_contains or "").strip().lower()
            subject_applied = (subject_contains or "").strip()
            from_key = (from_contains or "").strip().lower()
            from_applied = (from_contains or "").strip()

            # 无筛选：只多取少量即可。有筛选：从较新邮件向旧多扫一些，避免只扫 5 封导致误判「无邮件」
            if subject_key or from_key:
                desired = max(500, min(3000, limit * 80))
                tail_n = min(n_total, desired)
            else:
                tail_n = min(n_total, max(limit * 5, limit))

            id_list = id_list[-tail_n:]

            out: list[dict[str, Any]] = []
            for msg_id in reversed(id_list):
                if len(out) >= limit:
                    break
                typ, msg_data = imap.fetch(msg_id, "(RFC822)")
                if typ != "OK" or not msg_data or not isinstance(msg_data[0], tuple):
                    continue
                raw = msg_data[0][1]
                if not isinstance(raw, (bytes, bytearray)):
                    continue
                try:
                    msg = message_from_bytes(bytes(raw))
                except Exception:
                    continue

                subj = _decode_mime_header(msg.get("Subject"))
                if subject_key and subject_key not in subj.lower():
                    continue

                from_ = _decode_mime_header(msg.get("From"))
                if from_key and from_key not in from_.lower():
                    continue

                date_ = (msg.get("Date") or "").strip()
                preview = _get_body_preview(msg)
                flags_typ, flags_data = imap.fetch(msg_id, "(FLAGS)")
                flags_str = ""
                if flags_typ == "OK" and flags_data:
                    first = flags_data[0]
                    flag_line = first[0] if isinstance(first, tuple) else first
                    if isinstance(flag_line, bytes):
                        m = re.search(rb"FLAGS \(([^)]*)\)", flag_line)
                        if m:
                            flags_str = m.group(1).decode(errors="replace")

                out.append(
                    {
                        "id": msg_id.decode() if isinstance(msg_id, bytes) else str(msg_id),
                        "subject": subj,
                        "from": from_,
                        "date": date_,
                        "flags": flags_str,
                        "body_preview": preview,
                    }
                )

            imap.logout()

            hint = ""
            if len(out) == 0:
                if subject_key or from_key:
                    parts = []
                    if subject_key:
                        parts.append(f"主题包含 {subject_applied!r}")
                    if from_key:
                        parts.append(f"发件人包含 {from_applied!r}")
                    filter_desc = " 和 ".join(parts)
                    hint = (
                        f"在最近检视的约 {tail_n} 封邮件（从较新到较旧）中，没有符合条件（{filter_desc}）的邮件。"
                        "请核对筛选关键字是否正确。"
                    )
                elif n_total > 0:
                    hint = "有邮件 UID，但本次未能解析出任何条目（偶发）；可重试或稍后再试。"

            return ReadEmailOutput(
                success=True,
                messages=out,
                mailbox=mb,
                total_fetched=len(out),
                hint=hint,
                subject_filter=subject_applied,
                from_filter=from_applied,
                scanned_in_folder=tail_n,
            ).model_dump()

        except imaplib.IMAP4.error as e:
            return ReadEmailOutput(
                success=False,
                error=f"IMAP 错误：{e}",
                mailbox=mb,
            ).model_dump()
        except Exception as e:
            return ReadEmailOutput(
                success=False,
                error=f"读取邮件失败：{e}",
                mailbox=mb,
            ).model_dump()


read_email_tool = ReadEmailTool()


# ============================================================================
# 写邮件草稿工具
# ============================================================================

class WriteEmailDraftInput(BaseModel):
    """写邮件草稿工具的输入参数"""
    to: str = Field(
        description="""收件人邮箱地址
        - 可以是单个邮箱，如 "example@email.com"
        - 可以是多个邮箱，用逗号分隔，如 "a@email.com, b@email.com"
        - 如果用户提供的是人名或组织名，尝试补全为完整邮箱""",
        examples=["recipient@example.com", "user1@company.com, user2@company.com"]
    )
    subject: str = Field(
        description="""邮件主题
        - 简洁明了概括邮件内容
        - 不要包含 "RE:" 或 "FW:" 等邮件客户端会自动添加的前缀""",
        examples=["项目进度汇报", "会议邀请：Q2规划讨论"]
    )
    body: str = Field(
        description="""邮件正文内容
        - 使用纯文本格式
        - 如果有多段内容，用空行分隔
        - 保持格式简洁，适合邮件阅读""",
        examples=["张总您好，\n\n附件是本月的项目进度报告，请查收。\n\n祝好"]
    )
    cc: str = Field(
        default="",
        description="""抄送邮箱地址（可选）
        - 格式同收件人
        - 如果用户没有提到抄送，留空""",
        examples=["manager@company.com"]
    )
    bcc: str = Field(
        default="",
        description="""密送邮箱地址（可选）
        - 格式同收件人
        - 如果用户没有提到密送，留空"""
    )


class WriteEmailDraftOutput(BaseModel):
    """写邮件草稿工具的输出"""
    success: bool = Field(description="操作是否成功")
    entry_id: str = Field(default="", description="草稿的唯一标识（EntryID）")
    error: str = Field(default="", description="错误信息（失败时）")


class WriteEmailDraftTool(BaseTool):
    """
    写邮件草稿工具

    功能：
    - 在本机 Outlook 草稿箱中创建邮件草稿
    - 支持收件人、主题、正文、抄送、密送
    - 草稿可直接在 Outlook 中编辑和发送

    注意：
    - 不实际发送邮件，只是创建草稿
    - 需要 Windows 系统并安装 Outlook
    """

    @property
    def name(self) -> str:
        return "write_email_draft"

    @property
    def description(self) -> str:
        return """写邮件草稿（仅创建草稿，不发送）。

适用场景：
- 用户想写新邮件但还没准备好发送
- 用户想先保存邮件草稿，稍后在 Outlook 中编辑和发送

功能：
- 在本机 Outlook 草稿箱中创建邮件草稿
- 草稿可直接在 Outlook 中打开、编辑后发送

输入参数：
- to: 收件人邮箱地址（必填）
- subject: 邮件主题（必填）
- body: 邮件正文内容（必填）
- cc: 抄送地址（可选）
- bcc: 密送地址（可选）

前提条件：
- Windows 系统
- 已安装 Microsoft Outlook 并配置好邮箱

注意事项：
- 此工具只创建草稿，不实际发送邮件
- 草稿保存后可在 Outlook 草稿箱中找到并编辑发送"""

    @property
    def input_schema(self) -> Type[BaseModel]:
        return WriteEmailDraftInput

    def execute(
        self,
        to: str,
        subject: str,
        body: str,
        cc: str = "",
        bcc: str = ""
    ) -> dict:
        """执行写邮件草稿（保存到 Outlook 草稿箱）"""
        try:
            # 验证必填参数
            if not to or not to.strip():
                return WriteEmailDraftOutput(
                    success=False,
                    error="收件人邮箱不能为空"
                ).model_dump()

            if not subject or not subject.strip():
                return WriteEmailDraftOutput(
                    success=False,
                    error="邮件主题不能为空"
                ).model_dump()

            if not body or not body.strip():
                return WriteEmailDraftOutput(
                    success=False,
                    error="邮件正文不能为空"
                ).model_dump()

            # 导入 Outlook 创建函数
            from ..outlook_local import create_outlook_email_draft

            # 调用 Outlook API 创建草稿
            success, error, entry_id = create_outlook_email_draft(
                to=to.strip(),
                subject=subject.strip(),
                body=body.strip(),
                cc=cc.strip() if cc else None,
                bcc=bcc.strip() if bcc else None,
            )

            if success:
                return WriteEmailDraftOutput(
                    success=True,
                    entry_id=entry_id or ""
                ).model_dump()
            else:
                return WriteEmailDraftOutput(
                    success=False,
                    error=error or "未知错误"
                ).model_dump()

        except ImportError as e:
            return WriteEmailDraftOutput(
                success=False,
                error=f"无法加载 Outlook 集成模块：{str(e)}"
            ).model_dump()
        except Exception as e:
            return WriteEmailDraftOutput(
                success=False,
                error=f"创建邮件草稿失败：{str(e)}"
            ).model_dump()


class WriteEmailDraftPreviewTool(BaseTool):
    """
    写邮件草稿预览工具（模拟执行，返回待确认内容）

    功能：
    - 验证参数合法性
    - 返回将要创建的邮件草稿详情
    - 不实际创建草稿，等待用户确认后再执行
    """

    @property
    def name(self) -> str:
        return "write_email_draft"  # 与实际工具同名，会覆盖 TOOL_MAP 中的实际工具

    @property
    def description(self) -> str:
        return """预览写邮件草稿操作（待用户确认）。

此工具会检查参数是否合法，并返回将要创建的邮件草稿预览。
实际创建操作需要用户确认后才能执行。

适用场景：
- 用户想写邮件草稿
- 需要用户在执行前确认邮件内容

输入参数：
- to: 收件人邮箱地址
- subject: 邮件主题
- body: 邮件正文内容
- cc: 抄送地址（可选）
- bcc: 密送地址（可选）

返回：
- 操作预览信息，包括收件人、主题、正文预览等
- 用户需确认后才真正创建草稿"""

    @property
    def input_schema(self) -> Type[BaseModel]:
        return WriteEmailDraftInput

    def execute(
        self,
        to: str,
        subject: str,
        body: str,
        cc: str = "",
        bcc: str = ""
    ) -> dict:
        """模拟执行，返回预览信息"""
        try:
            # 验证必填参数
            if not to or not to.strip():
                return {
                    "success": False,
                    "error": "收件人邮箱不能为空",
                    "preview": None
                }

            if not subject or not subject.strip():
                return {
                    "success": False,
                    "error": "邮件主题不能为空",
                    "preview": None
                }

            if not body or not body.strip():
                return {
                    "success": False,
                    "error": "邮件正文不能为空",
                    "preview": None
                }

            # 清理输入
            to = to.strip()
            subject = subject.strip()
            body = body.strip()
            cc = cc.strip() if cc else ""
            bcc = bcc.strip() if bcc else ""

            # 计算统计信息
            body_lines = body.split("\n")
            body_preview = "\n".join(body_lines[:5])
            if len(body_lines) > 5:
                body_preview += f"\n... (还有 {len(body_lines) - 5} 行)"

            return {
                "success": True,
                "preview": {
                    "operation": "write_email_draft",
                    "to": to,
                    "subject": subject,
                    "body_preview": body_preview,
                    "body_lines": len(body_lines),
                    "body_characters": len(body),
                    "cc": cc if cc else "（无）",
                    "bcc": bcc if bcc else "（无）",
                    "save_location": "Outlook 草稿箱"
                }
            }

        except Exception as e:
            return {
                "success": False,
                "error": f"预览失败：{str(e)}",
                "preview": None
            }


# 创建工具实例
write_email_draft_tool = WriteEmailDraftTool()
write_email_draft_preview_tool = WriteEmailDraftPreviewTool()

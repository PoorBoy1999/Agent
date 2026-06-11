"""
文件读取工具
支持 Function Calling，让 LLM 能够调用此工具

知识点：
1. Pydantic v2 BaseModel 作为工具参数
   - description 用于 LLM 理解参数用途
   - Field() 定制参数的元信息

2. with_structured_output()
   - LangChain 提供的方法，让 LLM 输出结构化结果
   - 可以指定输出为 Pydantic 模型

3. 路径安全检查
   - 防止目录遍历攻击
   - 硬编码允许访问的根目录
"""
import os
from pathlib import Path
from typing import Type

from pydantic import BaseModel, Field

from .base import BaseTool


def _prepare_file_path(file_path: str) -> str:
    """保留路径中的空格（Windows 合法路径），仅去掉首尾空白。"""
    return file_path.strip()


class ReadFileInput(BaseModel):
    """读取文件工具的输入参数
    
    知识点：description 会告诉 LLM 这个参数的作用
    """
    file_path: str = Field(
        description="""要读取的文件路径

        【路径格式说明 - 重要！】
        当用户提供的是相对路径（如"桌面上的 职场\\sql.txt"）时：
        - 不要直接使用 "桌面上的 职场\\sql.txt" 作为路径
        - 应该转换为完整路径：C:\\Users\\Ron\\Desktop\\职场\\sql.txt
        - 只需将允许的根目录 C:\\Users\\Ron\\Desktop 加上用户描述的相对路径即可

        当用户提供完整路径时（如 "C:\\Users\\Ron\\Desktop\\职场\\sql.txt"）：
        - 直接使用该路径，不要改写

        重要规则：
        - 文件路径必须在允许范围内：C:\\Users\\Ron\\Desktop\\
        - 路径须与用户描述一致，文件夹名中的空格和特殊字符必须保留
        - 文件扩展名要完整
        - 如果路径中有空格或中文，不要害怕，直接传递给工具""",
        examples=["C:\\Users\\Ron\\Desktop\\AI学习笔记\\RAG坑总结.txt", "职场\\sql.txt", "桌面/职场/sql.txt"]
    )
    max_lines: int = Field(
        default=1000,
        description="最多读取的行数，防止读取过大文件"
    )


class ReadFileOutput(BaseModel):
    """读取文件工具的输出"""
    success: bool = Field(description="操作是否成功")
    content: str = Field(default="", description="文件内容（成功时）")
    error: str = Field(default="", description="错误信息（失败时）")
    file_path: str = Field(description="实际读取的文件路径")


class ReadFileTool(BaseTool):
    """
    读取本地文件工具
    
    功能：
    - 读取指定路径的文本文件内容
    - 支持限制读取行数
    - 安全限制：只能读取允许目录下的文件
    """
    
    # 允许访问的根目录
    ALLOWED_ROOT = r"C:\Users\Ron\Desktop"
    
    @property
    def name(self) -> str:
        return "read_file"
    
    @property
    def description(self) -> str:
        return """读取本地文本文件的内容。

适用场景：
- 用户想查看某个文件的内容
- 用户提到"读一下"、"看看"、"打开"某个文件
- 用户想了解文件的具体内容

输入参数：
- file_path: 文件的完整路径或相对路径
- max_lines: 最大读取行数（默认1000行）

安全限制：
- 只能读取 C:\\Users\\Ron\\Desktop\\ 目录下的文件"""
    
    @property
    def input_schema(self) -> Type[BaseModel]:
        return ReadFileInput
    
    def execute(self, file_path: str, max_lines: int = 1000) -> dict:
        """
        执行文件读取
        
        知识点：
        - 返回字典，方便序列化为 JSON
        - 详细的错误信息有助于调试
        """
        try:
            file_path = _prepare_file_path(file_path)
            # 安全检查：构建完整路径
            if not os.path.isabs(file_path):
                full_path = os.path.join(self.ALLOWED_ROOT, file_path)
            else:
                full_path = file_path
            
            # 规范化路径（处理 .. 等）
            full_path = os.path.normpath(full_path)
            
            # 安全检查：确保路径在允许目录内
            if not full_path.startswith(self.ALLOWED_ROOT):
                return ReadFileOutput(
                    success=False,
                    error=f"访问被拒绝：路径不在允许范围内 ({self.ALLOWED_ROOT})",
                    file_path=file_path
                ).model_dump()
            
            # 检查文件是否存在
            if not os.path.exists(full_path):
                return ReadFileOutput(
                    success=False,
                    error=f"文件不存在：{file_path}",
                    file_path=file_path
                ).model_dump()
            
            # 检查是否是文件
            if not os.path.isfile(full_path):
                return ReadFileOutput(
                    success=False,
                    error=f"路径不是文件：{file_path}",
                    file_path=file_path
                ).model_dump()
            
            # 读取文件内容
            lines = []
            with open(full_path, 'r', encoding='utf-8') as f:
                for i, line in enumerate(f):
                    if i >= max_lines:
                        break
                    lines.append(line.rstrip('\n'))
            
            content = '\n'.join(lines)
            
            return ReadFileOutput(
                success=True,
                content=content,
                file_path=full_path
            ).model_dump()
            
        except UnicodeDecodeError:
            return ReadFileOutput(
                success=False,
                error=f"文件编码不支持（仅支持 UTF-8 编码的文本文件）：{file_path}",
                file_path=file_path
            ).model_dump()
        except Exception as e:
            return ReadFileOutput(
                success=False,
                error=f"读取文件失败：{str(e)}",
                file_path=file_path
            ).model_dump()


# 创建工具实例
read_file_tool = ReadFileTool()


# ============================================================================
# 文件写入工具
# ============================================================================

class WriteFileInput(BaseModel):
    """写入文件工具的输入参数"""
    file_path: str = Field(
        description="""要写入的文件路径（完整路径或相对于允许目录的路径）
        
        重要格式要求：
        - 必须是完整路径，例如：C:\\Users\\Ron\\Desktop\\AI学习笔记\\新建文件.txt
        - 路径须与用户描述一致，文件夹名中的空格必须保留
        - 如果文件不存在，会自动创建
        - 如果文件已存在，会覆盖原文件""",
        examples=["C:\\Users\\Ron\\Desktop\\AI学习笔记\\新建文件.txt"]
    )
    content: str = Field(
        description="""要写入文件的内容
        - 纯文本内容
        - 可以包含多行文本（使用 \\n 换行）""",
        examples=["第一行内容\n第二行内容"]
    )
    encoding: str = Field(
        default="utf-8",
        description="文件编码，默认 utf-8"
    )


class WriteFileOutput(BaseModel):
    """写入文件工具的输出"""
    success: bool = Field(description="操作是否成功")
    file_path: str = Field(default="", description="实际写入的文件路径")
    bytes_written: int = Field(default=0, description="写入的字节数")
    error: str = Field(default="", description="错误信息（失败时）")


class WriteFileTool(BaseTool):
    """
    写入本地文件工具
    
    功能：
    - 将内容写入指定路径的文本文件
    - 支持自动创建新文件
    - 支持覆盖已有文件
    - 安全限制：只能写入允许目录下的文件
    
    注意：此工具会直接执行写入操作，建议配合确认机制使用
    """
    
    # 允许访问的根目录
    ALLOWED_ROOT = r"C:\Users\Ron\Desktop"
    
    @property
    def name(self) -> str:
        return "write_file"
    
    @property
    def description(self) -> str:
        return """写入内容到本地文本文件。

适用场景：
- 用户想创建新文件
- 用户想修改/覆盖已有文件内容
- 用户提到"写入"、"保存"、"创建"文件

输入参数：
- file_path: 文件的完整路径或相对路径
- content: 要写入的内容（纯文本）
- encoding: 文件编码（默认 utf-8）

安全限制：
- 只能写入 C:\\Users\\Ron\\Desktop\\ 目录下的文件
- 不支持写入二进制文件"""
    
    @property
    def input_schema(self) -> Type[BaseModel]:
        return WriteFileInput
    
    def execute(self, file_path: str, content: str, encoding: str = "utf-8") -> dict:
        """
        执行文件写入
        
        直接写入文件，无确认步骤
        """
        try:
            file_path = _prepare_file_path(file_path)
            
            # 构建完整路径
            if not os.path.isabs(file_path):
                full_path = os.path.join(self.ALLOWED_ROOT, file_path)
            else:
                full_path = file_path
            
            # 规范化路径
            full_path = os.path.normpath(full_path)
            
            # 安全检查
            if not full_path.startswith(self.ALLOWED_ROOT):
                return WriteFileOutput(
                    success=False,
                    error=f"访问被拒绝：路径不在允许范围内 ({self.ALLOWED_ROOT})",
                    file_path=file_path
                ).model_dump()
            
            # 确保目录存在
            parent_dir = os.path.dirname(full_path)
            if parent_dir and not os.path.exists(parent_dir):
                os.makedirs(parent_dir, exist_ok=True)
            
            # 写入文件
            bytes_written = 0
            with open(full_path, 'w', encoding=encoding) as f:
                bytes_written = f.write(content)
            
            return WriteFileOutput(
                success=True,
                file_path=full_path,
                bytes_written=bytes_written
            ).model_dump()
            
        except Exception as e:
            return WriteFileOutput(
                success=False,
                error=f"写入文件失败：{str(e)}",
                file_path=file_path
            ).model_dump()


class WriteFilePreviewTool(BaseTool):
    """
    文件写入预览工具（模拟执行，返回待确认内容）
    
    功能：
    - 验证参数合法性
    - 返回将要执行的操作详情
    - 不实际写入文件，等待用户确认后再执行
    """
    
    ALLOWED_ROOT = r"C:\Users\Ron\Desktop"
    
    @property
    def name(self) -> str:
        return "write_file"  # 与实际工具同名，会覆盖 TOOL_MAP 中的实际工具
    
    @property
    def description(self) -> str:
        return """预览文件写入操作（待用户确认）。

此工具会检查参数是否合法，并返回将要写入的内容预览。
实际写入操作需要用户确认后才能执行。

适用场景：
- 用户想创建或修改文件
- 需要用户在执行前确认写入内容

输入参数：
- file_path: 文件的完整路径
- content: 要写入的内容
- encoding: 文件编码（默认 utf-8）

返回：
- 操作预览信息，包括目标路径、内容长度等
- 用户需确认后才真正执行写入"""
    
    @property
    def input_schema(self) -> Type[BaseModel]:
        return WriteFileInput
    
    def execute(self, file_path: str, content: str, encoding: str = "utf-8") -> dict:
        """
        模拟执行文件写入，返回预览信息
        
        不实际写入文件，只是验证参数并返回预览
        """
        try:
            file_path = _prepare_file_path(file_path)
            
            # 构建完整路径
            if not os.path.isabs(file_path):
                full_path = os.path.join(self.ALLOWED_ROOT, file_path)
            else:
                full_path = file_path
            
            # 规范化路径
            full_path = os.path.normpath(full_path)
            
            # 安全检查
            if not full_path.startswith(self.ALLOWED_ROOT):
                return {
                    "success": False,
                    "error": f"访问被拒绝：路径不在允许范围内 ({self.ALLOWED_ROOT})",
                    "preview": None
                }
            
            # 检查文件是否存在
            file_exists = os.path.exists(full_path)
            
            # 计算内容统计
            lines = content.split('\n')
            line_count = len(lines)
            char_count = len(content)
            byte_count = len(content.encode(encoding))

            # 内容预览（限制长度，避免显示过大的内容）
            # 只显示前5行，最多500字符
            preview_lines = lines[:5]
            content_preview = '\n'.join(preview_lines)
            if len(content_preview) > 500:
                content_preview = content_preview[:500] + "\n..."
            if len(lines) > 5:
                content_preview += f"\n... (还有 {len(lines) - 5} 行，共 {char_count} 字符)"
            
            # 返回预览信息
            return {
                "success": True,
                "preview": {
                    "operation": "write_file",
                    "file_path": full_path,
                    "file_exists": file_exists,
                    "content_preview": content_preview,
                    "stats": {
                        "lines": line_count,
                        "characters": char_count,
                        "bytes": byte_count
                    },
                    "encoding": encoding,
                    "will_overwrite": file_exists
                }
            }
            
        except Exception as e:
            return {
                "success": False,
                "error": f"预览失败：{str(e)}",
                "preview": None
            }


# 创建工具实例
write_file_tool = WriteFileTool()
write_file_preview_tool = WriteFilePreviewTool()

"""
Pydantic 模型定义 - 用于工具参数校验和结构化数据
"""
from pydantic import BaseModel, Field
from typing import Optional


class ReadFileInput(BaseModel):
    """读取文件工具的输入参数"""
    file_path: str = Field(
        description="要读取的文件路径，必须在允许的目录范围内"
    )
    
    max_lines: Optional[int] = Field(
        default=1000,
        description="最多读取的行数，默认1000行"
    )


class ReadFileOutput(BaseModel):
    """读取文件工具的输出"""
    success: bool
    content: str = ""
    error: Optional[str] = None
    file_path: str

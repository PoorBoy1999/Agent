"""
网络搜索工具 - 用于搜索网页和查找信息

功能：
1. 使用 DuckDuckGo 执行搜索
2. 返回相关链接和摘要信息
3. 供 LLM 选择合适的 URL 进行访问
"""
from typing import Type

from pydantic import BaseModel, Field

from .base import BaseTool


class WebSearchInput(BaseModel):
    """网络搜索工具的输入参数"""
    search_term: str = Field(
        description="""搜索关键词
        - 输入要搜索的内容或问题
        - 尽量具体，包含关键信息
        - 例如："验收 checklist 最佳实践"、"Atlassian 敏捷开发 验收标准"
        - 建议使用中英文结合或纯英文可获得更多结果""",
        examples=[
            "验收 checklist 最佳实践",
            "acceptance criteria checklist best practices",
            "Atlassian agile acceptance criteria"
        ]
    )


class WebSearchOutput(BaseModel):
    """网络搜索工具的输出"""
    success: bool = Field(description="操作是否成功")
    search_term: str = Field(default="", description="搜索关键词")
    results: list = Field(default_factory=list, description="搜索结果列表")
    error: str = Field(default="", description="错误信息（失败时）")


class WebSearchTool(BaseTool):
    """
    网络搜索工具

    用途：
    - 当用户要求"找"、"搜索"某篇文章或信息时使用
    - 当用户没有提供具体URL，需要查找时使用
    - 返回搜索结果供下一步选择访问

    返回结果：
    - 包含相关网页的标题、URL、摘要
    - LLM 可以从中选择合适的 URL 进行 browser_fetch
    """

    @property
    def name(self) -> str:
        return "web_search"

    @property
    def description(self) -> str:
        return """网络搜索工具 - 搜索网页和查找信息。

适用场景：
- 用户要求"找"、"搜索"某篇文章或信息时
- 用户没有提供具体URL，需要自己查找时
- 这是获取真实URL的最佳方式

输入参数：
- search_term: 搜索关键词

返回内容：
- results: 搜索结果列表，每项包含 title、url、snippet
- 建议从中选择权威来源（如 Atlassian、官方文档等）

使用流程：
1. 调用 web_search 获取搜索结果
2. 从结果中选择合适的 URL
3. 再调用 browser_fetch 访问该 URL"""

    @property
    def input_schema(self) -> Type[BaseModel]:
        return WebSearchInput

    def execute(self, search_term: str) -> dict:
        """
        执行网络搜索（同步接口，供 Agent 调用）
        使用 DashScope API 的内置搜索功能
        """
        import json
        import urllib.parse
        import urllib.request

        # 方法1: 使用 DashScope 的搜索 API
        try:
            import os
            api_key = os.getenv("DASHSCOPE_API_KEY")
            if not api_key:
                raise RuntimeError("Missing DASHSCOPE_API_KEY environment variable")
            url = "https://api.deepseek.com"
            headers = {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json"
            }
            
            data = {
                "model": "deepseek-v4-flash",
                "messages": [
                    {"role": "user", "content": f"请搜索以下关键词，返回5个最相关的网页链接（包含标题和URL）：{search_term}"}
                ],
                "max_tokens": 2000,
                "temperature": 0
            }
            
            req = urllib.request.Request(
                url,
                data=json.dumps(data).encode('utf-8'),
                headers=headers,
                method='POST'
            )
            
            with urllib.request.urlopen(req, timeout=30) as response:
                result = json.loads(response.read().decode('utf-8'))
            
            if 'choices' in result and len(result['choices']) > 0:
                content = result['choices'][0]['message']['content']
                
                # 解析返回的链接
                import re
                # 匹配各种 URL 格式
                url_pattern = r'https?://[^\s\)"，\'<>「」『』\[\]{}|\\]+'
                urls = re.findall(url_pattern, content)
                
                results = []
                seen = set()
                for url in urls[:10]:
                    url = url.rstrip('.,;:')
                    if url not in seen and len(url) > 20:
                        seen.add(url)
                        results.append({
                            "title": f"搜索结果",
                            "url": url,
                            "snippet": ""
                        })
                
                return {
                    "success": True,
                    "search_term": search_term,
                    "results": results,
                    "error": "" if results else "未找到相关结果"
                }
                
        except Exception as e:
            pass
        
        # 方法2: 备用 - 使用 DuckDuckGo HTML
        try:
            encoded_term = urllib.parse.quote(search_term)
            urls_to_try = [
                f"https://duckduckgo.com/html/?q={encoded_term}",
            ]
            
            results = []
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            }
            
            for url in urls_to_try:
                try:
                    req = urllib.request.Request(url, headers=headers)
                    with urllib.request.urlopen(req, timeout=8) as response:
                        html = response.read().decode('utf-8', errors='ignore')
                    
                    import re
                    result_pattern = r'<a\s+class="result__a"\s+href="([^"]+)"[^>]*>([^<]+)</a>'
                    matches = re.findall(result_pattern, html)
                    
                    for url, title in matches[:10]:
                        title = re.sub(r'<[^>]+>', '', title).strip()
                        if title and url and not url.startswith('//') and 'duckduckgo' not in url:
                            full_url = url if url.startswith('http') else f"https://{url.lstrip('/')}"
                            results.append({
                                "title": title,
                                "url": full_url,
                                "snippet": ""
                            })
                    
                    if results:
                        break
                        
                except:
                    continue
            
            if results:
                return {
                    "success": True,
                    "search_term": search_term,
                    "results": results,
                    "error": ""
                }
                    
        except:
            pass
        
        return {
            "success": False,
            "search_term": search_term,
            "results": [],
            "error": "搜索服务暂时不可用，请检查网络连接"
        }


# 创建工具实例
web_search_tool = WebSearchTool()

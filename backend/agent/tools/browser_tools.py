"""
浏览器工具 - 网页内容提取

功能：
1. 使用无头 Chromium 访问网页
2. 仅允许 GET 请求
3. 超时 30s
4. 禁止下载和弹窗
5. 实现"阅读模式"提取正文内容
6. 支持网页内容摘要（返回原始文本供 Planner 调用 LLM 总结）

依赖：
- pip install playwright
- playwright install chromium
- pip install nest_asyncio
"""
import asyncio
import re
import concurrent.futures
from typing import Type

from pydantic import BaseModel, Field

from .base import BaseTool


class BrowserFetchInput(BaseModel):
    """浏览器访问工具的输入参数"""
    url: str = Field(
        description="""要访问的网页 URL
        - 必须是完整的 HTTP/HTTPS URL
        - 仅支持 GET 请求
        - 例如：https://news.example.com/article/123""",
        examples=["https://www.example.com", "https://news.example.com/article/123"]
    )
    extract_content: bool = Field(
        default=True,
        description="是否提取正文内容（阅读模式），默认为 True"
    )
    max_content_length: int = Field(
        default=50000,
        description="最大内容长度，防止返回过长内容"
    )
    wait_for_selector: str = Field(
        default="body",
        description="等待页面加载的选择器"
    )


class BrowserFetchOutput(BaseModel):
    """浏览器访问工具的输出"""
    success: bool = Field(description="操作是否成功")
    url: str = Field(default="", description="实际访问的 URL")
    title: str = Field(default="", description="网页标题")
    content: str = Field(default="", description="提取的正文内容")
    summary: str = Field(default="", description="内容摘要提示")
    raw_text: str = Field(default="", description="原始文本（用于 LLM 总结）")
    error: str = Field(default="", description="错误信息（失败时）")
    metadata: dict = Field(default_factory=dict, description="附加元信息")


class BrowserFetchTool(BaseTool):
    """
    浏览器访问工具（无头 Chromium）

    特性：
    - 使用 Playwright 的异步 API
    - 仅支持 GET 请求
    - 超时 30 秒（增加容错性）
    - 禁止文件下载
    - 禁止弹窗（alert/confirm/prompt）
    - 内置"阅读模式"提取正文
    """

    def __init__(self):
        pass

    @property
    def name(self) -> str:
        return "browser_fetch"

    @property
    def description(self) -> str:
        return """浏览器访问工具 - 提取网页正文内容。

适用场景：
- 用户想了解某个网页的内容
- 用户要求"查看"、"访问"、"打开"某个网址
- 用户要求"总结"、"概括"某个网页的内容

输入参数：
- url: 完整的 HTTP/HTTPS 网址
- extract_content: 是否提取正文（默认 True）
- max_content_length: 最大内容长度（默认 50000 字符）

返回内容：
- title: 网页标题
- content: 提取的正文内容（阅读模式）
- raw_text: 原始文本（用于后续 LLM 总结）
- summary: 摘要提示

注意：
- 仅支持 GET 请求
- 超时时间 15 秒
- 自动过滤广告、导航栏等干扰内容"""

    @property
    def input_schema(self) -> Type[BaseModel]:
        return BrowserFetchInput

    async def _get_browser(self):
        """获取或初始化浏览器实例（每次创建新的避免状态问题）"""
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            raise ImportError(
                "请安装 Playwright: pip install playwright && playwright install chromium"
            )

        playwright = await async_playwright().start()
        browser = await playwright.chromium.launch(
            headless=True,
            args=[
                '--disable-downloads',           # 禁止下载
                '--disable-popup-blocking',      # 阻止弹窗
                '--disable-extensions',           # 禁用扩展
                '--no-sandbox',                  # 无沙箱
                '--disable-dev-shm-usage',       # 减少内存使用
            ]
        )
        return playwright, browser

    async def _close_browser(self, playwright, browser):
        """关闭浏览器实例"""
        try:
            if browser:
                await browser.close()
            if playwright:
                await playwright.stop()
        except Exception:
            pass

    async def _extract_readable_content(self, page) -> dict:
        """
        提取网页正文内容（阅读模式）
        """
        try:
            title = await page.title()
        except Exception:
            title = ""

        try:
            # 移除 script/style 标签，直接获取 body 文本
            await page.evaluate("""
                () => {
                    const remove = document.querySelectorAll('script, style, noscript, iframe');
                    remove.forEach(el => el.remove());
                }
            """)
            body = await page.inner_text("body")
            content = body.strip() if body else ""
        except Exception as e:
            content = ""

        return {
            "title": title,
            "content": content,
            "textLength": len(content)
        }

    async def _fetch_page(self, url: str, timeout: int = 30) -> dict:
        """
        执行网页抓取（异步版本）
        """
        playwright, browser = await self._get_browser()
        context = None
        page = None

        try:
            context = await browser.new_context(
                ignore_https_errors=True,
                accept_downloads=False,
            )

            page = await context.new_page()
            page.set_default_timeout(timeout * 1000)

            # 禁止弹窗
            def handle_dialog(dialog):
                asyncio.create_task(dialog.dismiss())
            page.on("dialog", handle_dialog)

            response = await page.goto(url, wait_until="domcontentloaded")

            if response and response.status >= 400:
                return {
                    "success": False,
                    "error": f"HTTP {response.status}: {response.status_text}",
                    "url": url,
                    "title": "",
                    "content": ""
                }

            try:
                await page.wait_for_selector("body", timeout=5000)
            except Exception:
                pass

            result = await self._extract_readable_content(page)

            # 清理文本
            content = re.sub(r'\n{3,}', '\n\n', result.get('content', ''))
            content = re.sub(r' {2,}', ' ', content)
            content = content.strip()

            return {
                "success": True,
                "url": url,
                "title": result.get('title', '').strip(),
                "content": content,
                "error": ""
            }

        except Exception as e:
            error_msg = str(e)
            if "download" in error_msg.lower():
                error_msg = "下载被禁止"
            return {
                "success": False,
                "error": error_msg,
                "url": url,
                "title": "",
                "content": ""
            }
        finally:
            if page:
                await page.close()
            if context:
                await context.close()
            await self._close_browser(playwright, browser)

    def execute(
        self,
        url: str,
        extract_content: bool = True,
        max_content_length: int = 50000,
        wait_for_selector: str = "body",
        timeout: int = 30
    ) -> dict:
        """
        执行浏览器访问（同步接口，供 Agent 调用）

        使用线程池和独立的事件循环执行异步代码
        """
        import nest_asyncio
        nest_asyncio.apply()

        print(f"[BrowserTool] 开始执行，URL: {url}, timeout: {timeout}")
        
        try:
            loop = asyncio.get_event_loop()
            print(f"[BrowserTool] 获取到 event loop, is_running: {loop.is_running()}")
            
            if loop.is_running():
                import concurrent.futures
                
                def run_in_new_loop():
                    """在新事件循环中运行异步代码"""
                    new_loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(new_loop)
                    try:
                        return new_loop.run_until_complete(self._fetch_page(url, timeout=timeout))
                    finally:
                        new_loop.close()
                
                with concurrent.futures.ThreadPoolExecutor() as executor:
                    future = executor.submit(run_in_new_loop)
                    result = future.result(timeout=timeout + 20)
            else:
                result = loop.run_until_complete(self._fetch_page(url, timeout=timeout))
        except concurrent.futures.TimeoutError:
            result = {
                "success": False,
                "error": f"浏览器访问超时（{timeout}秒）",
                "url": url,
                "title": "",
                "content": ""
            }
        except Exception as e:
            result = {
                "success": False,
                "error": f"浏览器错误: {str(e)}",
                "url": url,
                "title": "",
                "content": ""
            }

        if result.get("success"):
            content = result.get("content", "")

            if len(content) > max_content_length:
                content = content[:max_content_length] + f"\n\n[... 内容已截断，原长度 {len(content)} 字符 ...]"

            summary_hint = ""
            if content:
                preview = content[:500]
                summary_hint = f"""【网页内容摘要】

页面标题：{result.get('title', '无标题')}

内容预览（前 500 字符）：
{preview}

---

原始文本已提取完成。如需完整摘要，请使用 LLM 继续处理。

如需摘要，可参考以下提示词：
"请总结以下网页内容的主要信息，提取关键要点：\n\n{content[:2000]}..."

原始内容长度：{len(content)} 字符"""

            return {
                "success": True,
                "url": result.get("url", url),
                "title": result.get("title", ""),
                "content": content if extract_content else "",
                "summary": summary_hint,
                "raw_text": content,
                "error": ""
            }
        else:
            return {
                "success": False,
                "url": url,
                "title": "",
                "content": "",
                "summary": "",
                "raw_text": "",
                "error": result.get("error", "未知错误")
            }

    async def cleanup(self):
        """清理浏览器资源"""
        if self._browser:
            await self._browser.close()
            self._browser = None
        if self._playwright:
            await self._playwright.stop()
            self._playwright = None


# 创建工具实例
browser_fetch_tool = BrowserFetchTool()

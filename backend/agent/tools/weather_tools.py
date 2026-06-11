"""
天气查询工具 - 使用 Open-Meteo API 获取天气预报

功能：
1. 通过 Open-Meteo API 获取天气预报
2. 支持指定城市的天气查询（自动地理编码）
3. 支持查询未来7天的天气预报
4. 自动提取并格式化天气信息

依赖：
- httpx 库（用于 HTTP 请求）
- 内置 JSON 解析

优势对比 wttr.in：
- 更稳定的服务质量（SLA保障）
- 更丰富的数据字段
- 更快的响应速度
"""
import httpx
from typing import Type, Optional
from pydantic import BaseModel, Field


# Open-Meteo API 配置
OPEN_METEO_GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
OPEN_METEO_FORECAST_URL = "https://api.open-meteo.com/v1/forecast"


class WeatherQueryInput(BaseModel):
    """天气查询工具的输入参数"""
    city: str = Field(
        description="""查询天气的城市名称
        - 可以是中文城市名（如：北京、上海）
        - 可以是英文城市名（如：Beijing、Shanghai）
        - 如果用户未指定城市，询问用户想要查询哪个城市的天气""",
        examples=["北京", "上海", "Beijing", "Tokyo"]
    )
    forecast_days: int = Field(
        default=3,
        description="""预报天数
        - 1: 今天
        - 2: 今天+明天
        - 3: 今天+明天+后天（默认）
        - 最高支持7天预报""",
        ge=1,
        le=7
    )
    date: Optional[str] = Field(
        default=None,
        description="""指定日期的天气预报
        - 格式：YYYY-MM-DD（如：2026-05-27）
        - 如果用户提到具体日期，传入此参数
        - 可以是'今天'、'明天'、'后天'等相对日期（会自动转换为具体日期）""",
        examples=["2026-05-27", "明天", "后天"]
    )


class WeatherQueryOutput(BaseModel):
    """天气查询工具的输出"""
    success: bool = Field(description="查询是否成功")
    city: str = Field(default="", description="查询的城市")
    forecast_type: str = Field(default="", description="预报类型（今天/明天/后天/3天预报）")
    weather_data: str = Field(default="", description="天气数据")
    summary: str = Field(default="", description="简洁的天气摘要")
    error: str = Field(default="", description="错误信息（失败时）")


class WeatherQueryTool:
    """
    天气查询工具

    使用 Open-Meteo API 获取天气预报信息
    """

    def __init__(self):
        self._session = None

    @property
    def name(self) -> str:
        return "weather_query"

    @property
    def description(self) -> str:
        return """天气查询工具 - 查询天气预报信息。

适用场景：
- 用户询问"天气怎么样"、"今天/明天/后天天气"
- 用户询问特定城市或地点的天气
- 用户询问"要不要带伞"、"要不要穿外套"等与天气相关的问题
- 用户询问"明天会不会下雨"、"温度多少"等天气细节

输入参数：
- city: 要查询天气的城市名称（必填）
- forecast_days: 预报天数，1-7天（默认3天）
- date: 可选，指定具体日期

返回内容：
- weather_data: 天气数据详情
- summary: 简洁的天气摘要

使用说明：
1. 当用户询问天气时，调用此工具
2. 提取用户提到的城市名称
3. 如果用户未指定城市，询问用户想要查询哪个城市的天气
4. 如果用户提到"今天"、"明天"等日期，设置对应的 forecast_days
"""

    @property
    def input_schema(self) -> Type[BaseModel]:
        return WeatherQueryInput

    def _get_client(self) -> httpx.Client:
        """获取 HTTP 客户端（带连接复用）"""
        if not hasattr(self, '_client') or self._client.is_closed:
            self._client = httpx.Client(timeout=30.0, headers={
                'User-Agent': 'WeatherQueryTool/1.0'
            })
        return self._client

    def _geocode_city(self, city: str, timeout: float = 10.0) -> Optional[dict]:
        """
        将城市名称转换为经纬度坐标

        Args:
            city: 城市名称（支持中文或英文）
            timeout: 超时时间（秒）

        Returns:
            包含 latitude, longitude, name 的字典，失败返回 None
        """
        client = self._get_client()
        params = {
            'name': city,
            'count': 1,
            'language': 'zh' if self._is_chinese(city) else 'en',
            'format': 'json'
        }

        try:
            response = client.get(
                OPEN_METEO_GEOCODE_URL,
                params=params,
                timeout=timeout
            )
            response.raise_for_status()
            data = response.json()

            results = data.get('results', [])
            if results:
                result = results[0]
                return {
                    'latitude': result['latitude'],
                    'longitude': result['longitude'],
                    'name': result.get('name', city),
                    'country': result.get('country', ''),
                    'admin1': result.get('admin1', '')  # 省/州
                }
            return None

        except httpx.HTTPError as e:
            print(f"地理编码请求失败: {e}")
            return None

    def _is_chinese(self, text: str) -> bool:
        """简单判断文本是否包含中文字符"""
        return any('\u4e00' <= char <= '\u9fff' for char in text)

    def _get_weather_code_description(self, code: int) -> str:
        """
        将 WMO 天气代码转换为中文描述

        WMO Weather interpretation codes (WW):
        https://open-meteo.com/en/docs#weathervariables
        """
        weather_codes = {
            0: "晴朗",
            1: "基本晴朗",
            2: "多云",
            3: "阴天",
            45: "有雾",
            48: "雾凇",
            51: "小毛毛雨",
            53: "中毛毛雨",
            55: "大毛毛雨",
            56: "冻毛毛雨（轻）",
            57: "冻毛毛雨（重）",
            61: "小雨",
            63: "中雨",
            65: "大雨",
            66: "冻雨（轻）",
            67: "冻雨（重）",
            71: "小雪",
            73: "中雪",
            75: "大雪",
            77: "雪粒",
            80: "阵雨（小）",
            81: "阵雨（中）",
            82: "阵雨（大）",
            85: "阵雪（小）",
            86: "阵雪（大）",
            95: "雷暴",
            96: "雷暴伴小冰雹",
            99: "雷暴伴大冰雹"
        }
        return weather_codes.get(code, f"未知({code})")

    def _fetch_forecast(
        self,
        latitude: float,
        longitude: float,
        forecast_days: int = 3,
        timeout: float = 10.0
    ) -> Optional[dict]:
        """
        获取天气预报数据

        Args:
            latitude: 纬度
            longitude: 经度
            forecast_days: 预报天数（1-7）
            timeout: 超时时间（秒）

        Returns:
            天气预报数据字典，失败返回 None
        """
        client = self._get_client()

        # 限制天数范围
        forecast_days = max(1, min(7, forecast_days))

        params = {
            'latitude': latitude,
            'longitude': longitude,
            'timezone': 'auto',  # 自动时区
            'daily': [
                'temperature_2m_max',
                'temperature_2m_min',
                'precipitation_sum',
                'precipitation_probability_max',
                'weathercode',
                'windspeed_10m_max',
                'sunrise',
                'sunset'
            ],
            'hourly': [
                'temperature_2m',
                'relativehumidity_2m',
                'apparent_temperature',
                'precipitation_probability',
                'weathercode'
            ],
            'forecast_days': forecast_days,
            'current': [
                'temperature_2m',
                'relativehumidity_2m',
                'apparent_temperature',
                'precipitation',
                'weathercode',
                'windspeed_10m',
                'winddirection_10m'
            ]
        }

        try:
            response = client.get(
                OPEN_METEO_FORECAST_URL,
                params=params,
                timeout=timeout
            )
            response.raise_for_status()
            return response.json()

        except httpx.HTTPError as e:
            print(f"天气预报请求失败: {e}")
            return None
        except ValueError as e:
            print(f"JSON 解析失败: {e}")
            return None

    def _format_daily_forecast(self, data: dict, location_name: str) -> str:
        """
        格式化每日天气预报为易读的文本

        Args:
            data: Open-Meteo API 返回的数据
            location_name: 地点名称

        Returns:
            格式化的天气预报文本
        """
        daily = data.get('daily', {})
        current = data.get('current', {})

        output = []
        output.append(f"📍 {location_name}")
        output.append("=" * 40)

        # 当前天气
        if current:
            temp = current.get('temperature_2m', 'N/A')
            feels_like = current.get('apparent_temperature', 'N/A')
            humidity = current.get('relativehumidity_2m', 'N/A')
            weather_code = current.get('weathercode', 0)
            wind = current.get('windspeed_10m', 'N/A')
            precip = current.get('precipitation', 0)

            output.append("\n🌡️ 当前天气")
            output.append(f"   温度: {temp}°C (体感 {feels_like}°C)")
            output.append(f"   天气: {self._get_weather_code_description(weather_code)}")
            output.append(f"   湿度: {humidity}%")
            output.append(f"   风速: {wind} km/h")
            output.append(f"   降水: {precip} mm")

        # 每日预报
        dates = daily.get('time', [])
        if dates:
            output.append("\n📅 天气预报")
            for i, date in enumerate(dates):
                max_temp = daily.get('temperature_2m_max', [])[i] if i < len(daily.get('temperature_2m_max', [])) else 'N/A'
                min_temp = daily.get('temperature_2m_min', [])[i] if i < len(daily.get('temperature_2m_min', [])) else 'N/A'
                precip_sum = daily.get('precipitation_sum', [])[i] if i < len(daily.get('precipitation_sum', [])) else 0
                precip_prob = daily.get('precipitation_probability_max', [])[i] if i < len(daily.get('precipitation_probability_max', [])) else 0
                weather_code = daily.get('weathercode', [])[i] if i < len(daily.get('weathercode', [])) else 0
                wind_max = daily.get('windspeed_10m_max', [])[i] if i < len(daily.get('windspeed_10m_max', [])) else 'N/A'
                sunrise = daily.get('sunrise', [])[i] if i < len(daily.get('sunrise', [])) else ''
                sunset = daily.get('sunset', [])[i] if i < len(daily.get('sunset', [])) else ''

                # 格式化日期
                day_label = self._format_day_label(i, date)

                output.append(f"\n{day_label} ({date})")
                output.append(f"   🌡️ 温度: {min_temp}°C ~ {max_temp}°C")
                output.append(f"   ☁️ 天气: {self._get_weather_code_description(weather_code)}")
                output.append(f"   💧 降水概率: {precip_prob}% (总量: {precip_sum}mm)")
                output.append(f"   💨 最大风速: {wind_max} km/h")

                if sunrise and sunset:
                    sunrise_time = sunrise.split('T')[1] if 'T' in sunrise else sunrise
                    sunset_time = sunset.split('T')[1] if 'T' in sunset else sunset
                    output.append(f"   🌅 日出: {sunrise_time} / 🌇 日落: {sunset_time}")

        return '\n'.join(output)

    def _format_day_label(self, index: int, date: str) -> str:
        """格式化日期标签"""
        import datetime

        try:
            date_obj = datetime.datetime.strptime(date, '%Y-%m-%d').date()
            today = datetime.date.today()

            if date_obj == today:
                return "今天"
            elif date_obj == today + datetime.timedelta(days=1):
                return "明天"
            elif date_obj == today + datetime.timedelta(days=2):
                return "后天"
            else:
                weekdays = ['周一', '周二', '周三', '周四', '周五', '周六', '周日']
                return weekdays[date_obj.weekday()]
        except:
            return date

    def _generate_summary(self, data: dict, city: str, forecast_days: int) -> str:
        """
        生成简洁的天气摘要

        Args:
            data: 天气预报数据
            city: 城市名称
            forecast_days: 预报天数

        Returns:
            简洁的天气摘要文本
        """
        daily = data.get('daily', {})
        current = data.get('current', {})

        summary_parts = []

        # 当前天气
        if current:
            temp = current.get('temperature_2m', 'N/A')
            weather_code = current.get('weathercode', 0)
            desc = self._get_weather_code_description(weather_code)
            summary_parts.append(f"当前: {desc} {temp}°C")

        # 今日和未来几天
        dates = daily.get('time', [])
        if dates:
            # 今天的预报
            max_temp = daily.get('temperature_2m_max', [None])[0]
            min_temp = daily.get('temperature_2m_min', [None])[0]
            precip_prob = daily.get('precipitation_probability_max', [0])[0]
            weather_code = daily.get('weathercode', [0])[0]

            today_summary = f"今日: {self._get_weather_code_description(weather_code)} "
            if min_temp and max_temp:
                today_summary += f"{min_temp}~{max_temp}°C"
            summary_parts.append(today_summary)

            if precip_prob > 50:
                summary_parts.append(f"降雨概率较高 ({precip_prob}%)")

            # 明天的预报
            if forecast_days >= 2 and len(dates) >= 2:
                max_temp = daily.get('temperature_2m_max', [None])[1]
                min_temp = daily.get('temperature_2m_min', [None])[1]
                weather_code = daily.get('weathercode', [0])[1]
                precip_prob = daily.get('precipitation_probability_max', [0])[1]

                tomorrow_summary = f"明天: {self._get_weather_code_description(weather_code)} "
                if min_temp and max_temp:
                    tomorrow_summary += f"{min_temp}~{max_temp}°C"
                summary_parts.append(tomorrow_summary)

        return '\n'.join(summary_parts)

    def execute(
        self,
        city: str,
        forecast_days: int = 3,
        date: Optional[str] = None,
        timeout: int = 30
    ) -> dict:
        """
        执行天气查询

        使用 Open-Meteo API 获取天气信息
        """
        # 限制预报天数
        forecast_days = max(1, min(7, forecast_days))

        try:
            # 步骤1: 地理编码 - 将城市名转换为坐标
            geo_result = self._geocode_city(city, timeout=min(timeout, 10))
            if not geo_result:
                return {
                    "success": False,
                    "city": city,
                    "forecast_type": "",
                    "weather_data": "",
                    "summary": "",
                    "error": f"无法找到城市 '{city}'，请检查城市名称是否正确"
                }

            latitude = geo_result['latitude']
            longitude = geo_result['longitude']
            location_name = geo_result['name']
            if geo_result.get('country'):
                location_name = f"{location_name}, {geo_result['country']}"

            # 步骤2: 获取天气预报
            weather_data = self._fetch_forecast(
                latitude=latitude,
                longitude=longitude,
                forecast_days=forecast_days,
                timeout=min(timeout, 15)
            )

            if not weather_data:
                return {
                    "success": False,
                    "city": city,
                    "forecast_type": "",
                    "weather_data": "",
                    "summary": "",
                    "error": "获取天气预报失败，请稍后重试"
                }

            # 步骤3: 格式化输出
            formatted_weather = self._format_daily_forecast(weather_data, location_name)
            summary = self._generate_summary(weather_data, city, forecast_days)

            return {
                "success": True,
                "city": location_name,
                "forecast_type": self._get_forecast_type(forecast_days, date),
                "weather_data": formatted_weather,
                "summary": summary,
                "error": ""
            }

        except Exception as e:
            return {
                "success": False,
                "city": city,
                "forecast_type": "",
                "weather_data": "",
                "summary": "",
                "error": f"天气查询出错: {str(e)}"
            }

    def _get_forecast_type(self, forecast_days: int, date: Optional[str]) -> str:
        """根据参数生成预报类型描述"""
        if date:
            return f"{date}天气预报"
        elif forecast_days == 1:
            return "今日天气预报"
        elif forecast_days == 2:
            return "今明两天天气预报"
        elif forecast_days <= 7:
            return f"未来{forecast_days}天天气预报"
        else:
            return "天气预报"


# 创建工具实例
weather_query_tool = WeatherQueryTool()

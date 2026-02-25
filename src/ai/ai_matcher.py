#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AI智能匹配模块 - 用于自动选择最佳搜索结果"""

import json
import logging
import httpx
from typing import List, Dict, Any, Optional
from datetime import datetime
from src.db import models
from .ai_metrics import AIMetricsCollector, AICallMetrics
from .ai_cache import AIResponseCache
from .ai_providers import get_provider_config, is_provider_supported

# 从 models 导入需要的类
ProviderSearchInfo = models.ProviderSearchInfo

logger = logging.getLogger(__name__)
ai_responses_logger = logging.getLogger("ai_responses")

# 从 ai_prompts 导入提示词
from .ai_prompts import (
    DEFAULT_AI_MATCH_PROMPT,
    DEFAULT_AI_SEASON_MAPPING_PROMPT,
    DEFAULT_AI_RECOGNITION_PROMPT,
    DEFAULT_AI_ALIAS_EXPANSION_PROMPT,
    DEFAULT_AI_ALIAS_VALIDATION_PROMPT,
    DEFAULT_AI_EPISODE_GROUP_SELECT_PROMPT,
)


def _extract_openai_content(response) -> Optional[str]:
    """从 OpenAI 响应中安全提取内容

    某些 API（如部分兼容接口）可能返回字符串而非标准对象
    """
    # 如果响应本身就是字符串，直接返回
    if isinstance(response, str):
        logger.warning(f"OpenAI API 返回了字符串而非对象")
        return response

    # 检查是否有 choices 属性
    if not hasattr(response, 'choices') or not response.choices:
        logger.error(f"OpenAI API 响应格式异常: {type(response).__name__}")
        return None

    # 标准格式
    return response.choices[0].message.content


def _safe_json_loads(text: str, log_raw_response: bool = False) -> Optional[Dict]:
    """安全的JSON解析函数,能处理AI返回的常见错误

    Args:
        text: AI返回的文本
        log_raw_response: 是否记录原始响应到专用日志文件
    """
    if not text:
        return None

    # 可选: 记录原始响应到专用日志文件
    if log_raw_response:
        ai_responses_logger.info(f"原始响应内容:\n{text}\n{'='*80}")

    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        if log_raw_response:
            ai_responses_logger.warning(f"JSON直接解析失败: {e}。尝试智能修复...")

        # 尝试从markdown代码块中提取JSON
        import re
        match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', text, re.DOTALL)
        if match:
            json_str = match.group(1)
            if log_raw_response:
                ai_responses_logger.info(f"从markdown代码块提取的JSON:\n{json_str}\n{'='*80}")
            try:
                return json.loads(json_str)
            except json.JSONDecodeError:
                pass

        # 尝试直接提取第一个完整的JSON对象
        match = re.search(r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}', text, re.DOTALL)
        if match:
            json_str = match.group(0)
            if log_raw_response:
                ai_responses_logger.info(f"提取的JSON对象:\n{json_str}\n{'='*80}")
            try:
                return json.loads(json_str)
            except json.JSONDecodeError:
                pass

        if log_raw_response:
            ai_responses_logger.error(f"JSON修复失败,原始文本前500字符:\n{text[:500]}\n{'='*80}")
        return None


# --- 动态导入AI SDK ---
try:
    from openai import OpenAI
    OPENAI_AVAILABLE = True
except ImportError:
    OPENAI_AVAILABLE = False
    logger.debug("OpenAI SDK 未安装")

try:
    from google import genai
    GEMINI_AVAILABLE = True
except ImportError:
    GEMINI_AVAILABLE = False
    logger.debug("Google GenAI SDK 未安装")


class AIMatcher:
    """AI智能匹配器"""

    def __init__(self, config: Dict[str, Any]):
        """
        初始化AI匹配器

        Args:
            config: 配置字典,包含:
                - ai_match_provider: AI提供商 (deepseek/siliconflow/openai/gemini)
                - ai_match_api_key: API密钥
                - ai_match_base_url: Base URL (可选,Gemini不需要)
                - ai_match_model: 模型名称
                - ai_match_prompt: 自定义匹配提示词 (可选)
                - ai_recognition_prompt: 自定义识别提示词 (可选)
                - ai_alias_validation_prompt: 自定义别名验证提示词 (可选)
                - ai_log_raw_response: 是否记录原始AI响应 (可选,默认False)
                - ai_cache_enabled: 是否启用缓存 (可选,默认True)
                - ai_cache_ttl: 缓存过期时间(秒) (可选,默认3600)
        """
        # 保存完整配置以供后续使用
        self.config = config

        self.provider = config.get("ai_match_provider", "deepseek").lower()
        self.api_key = config.get("ai_match_api_key")
        self.base_url = config.get("ai_match_base_url")
        self.model = config.get("ai_match_model")
        self.log_raw_response = config.get("ai_log_raw_response", False)

        # 提示词配置: 直接使用传入的配置,不做任何兜底处理
        # 注意: 硬编码的DEFAULT_*_PROMPT只用于初始化数据库,不用于运行时兜底
        # 调用方应该确保在调用AIMatcher之前已经通过initialize_configs创建了配置项
        self.match_prompt = config.get("ai_match_prompt", "")
        self.recognition_prompt = config.get("ai_recognition_prompt", "")
        self.alias_validation_prompt = config.get("ai_alias_validation_prompt", "")

        if not self.api_key:
            raise ValueError("AI Matcher: API Key 未配置")

        if not self.model:
            raise ValueError("AI Matcher: 模型名称未配置")

        # 初始化监控和缓存
        self.metrics = AIMetricsCollector()

        cache_enabled = config.get("ai_cache_enabled", True)
        cache_ttl = config.get("ai_cache_ttl", 3600)
        self.cache = AIResponseCache(ttl_seconds=cache_ttl) if cache_enabled else None

        self.client = None
        self._initialize_client()

    def update_prompts(self, prompt_config: Dict[str, str]):
        """
        更新提示词配置(热更新)

        Args:
            prompt_config: 提示词配置字典
        """
        if "match_prompt" in prompt_config:
            self.match_prompt = prompt_config["match_prompt"]
        if "recognition_prompt" in prompt_config:
            self.recognition_prompt = prompt_config["recognition_prompt"]
        if "alias_validation_prompt" in prompt_config:
            self.alias_validation_prompt = prompt_config["alias_validation_prompt"]

        logger.info("AI匹配器提示词已更新")

    async def get_balance(self) -> Optional[Dict[str, Any]]:
        """
        获取账户余额 (根据提供商动态查询)

        Returns:
            余额信息字典,包含:
            - currency: 货币类型 (CNY/USD)
            - total_balance: 总余额
            - granted_balance: 赠金余额 (如果提供商支持)
            - topped_up_balance: 充值余额 (如果提供商支持)

        Raises:
            ValueError: 如果提供商不支持余额查询
            Exception: 如果API调用失败
        """
        # 获取提供商配置
        provider_config = get_provider_config(self.provider)
        if not provider_config:
            raise ValueError(f"无法获取提供商配置: {self.provider}")

        # 检查是否支持余额查询
        if not provider_config.get("supportBalance"):
            raise ValueError(f"提供商 {self.provider} 不支持余额查询")

        if not OPENAI_AVAILABLE:
            raise ImportError("OpenAI SDK 未安装")

        try:
            # 获取余额API路径
            balance_api_path = provider_config.get("balanceApiPath", "/user/balance")
            url = f"{self.base_url}{balance_api_path}"

            headers = {
                "Accept": "application/json",
                "Authorization": f"Bearer {self.api_key}"
            }

            async with httpx.AsyncClient() as client:
                response = await client.get(url, headers=headers, timeout=10.0)
                response.raise_for_status()

                data = response.json()

                # 根据提供商类型解析响应
                parser_type = provider_config.get("balanceResponseParser", "deepseek")
                return self._parse_balance_response(data, parser_type)

        except httpx.HTTPStatusError as e:
            logger.error(f"{self.provider} 余额查询失败: HTTP {e.response.status_code}")
            raise Exception(f"API调用失败: {e.response.status_code}")
        except httpx.RequestError as e:
            logger.error(f"{self.provider} 余额查询网络错误: {e}")
            raise Exception(f"网络错误: {str(e)}")
        except Exception as e:
            logger.error(f"{self.provider} 余额查询异常: {e}")
            raise

    def _parse_balance_response(self, data: Dict[str, Any], parser_type: str) -> Dict[str, Any]:
        """
        解析余额响应数据

        Args:
            data: API响应数据
            parser_type: 解析器类型 (deepseek/siliconflow)

        Returns:
            标准化的余额信息字典
        """
        if parser_type == "deepseek":
            # DeepSeek 响应格式
            if not data.get("is_available"):
                raise Exception("账户余额不足或不可用")

            balance_infos = data.get("balance_infos", [])
            if not balance_infos:
                raise Exception("未返回余额信息")

            balance_info = balance_infos[0]
            return {
                "currency": balance_info.get("currency", "CNY"),
                "total_balance": balance_info.get("total_balance", "0.00"),
                "granted_balance": balance_info.get("granted_balance", "0.00"),
                "topped_up_balance": balance_info.get("topped_up_balance", "0.00")
            }

        elif parser_type == "siliconflow":
            # SiliconFlow 响应格式
            # 根据官方文档: GET /user/info 返回格式为:
            # {
            #   "code": 20000,
            #   "data": {
            #     "balance": "0.88",           // 赠金余额
            #     "chargeBalance": "88.00",    // 充值余额
            #     "totalBalance": "88.88"      // 总余额
            #   }
            # }
            user_data = data.get("data", {})
            return {
                "currency": "CNY",  # SiliconFlow 默认使用人民币
                "total_balance": user_data.get("totalBalance", "0.00"),
                "granted_balance": user_data.get("balance", "0.00"),  # balance 字段是赠金
                "topped_up_balance": user_data.get("chargeBalance", "0.00")  # chargeBalance 是充值余额
            }

        else:
            # 默认解析器 - 尝试通用格式
            return {
                "currency": data.get("currency", "CNY"),
                "total_balance": str(data.get("total_balance", "0.00")),
                "granted_balance": str(data.get("granted_balance", "0.00")),
                "topped_up_balance": str(data.get("topped_up_balance", "0.00"))
            }

    def _initialize_client(self):
        """根据提供商初始化客户端"""
        try:
            # 检查提供商是否支持
            if not is_provider_supported(self.provider):
                raise ValueError(f"不支持的AI提供商: {self.provider}")

            # 获取提供商配置
            provider_config = get_provider_config(self.provider)
            if not provider_config:
                raise ValueError(f"无法获取提供商配置: {self.provider}")

            # Gemini 使用官方 SDK
            if self.provider == "gemini":
                if not GEMINI_AVAILABLE:
                    raise ImportError("Google GenAI SDK 未安装,请运行: pip install google-genai")

                self.client = genai.Client(api_key=self.api_key)
                logger.info(f"AI匹配器初始化成功: Gemini ({self.model}) - 使用官方 SDK")

            # 其他提供商使用 OpenAI 兼容接口
            else:
                if not OPENAI_AVAILABLE:
                    raise ImportError("OpenAI SDK 未安装,请运行: pip install openai")

                # 如果用户未配置Base URL,使用提供商的默认Base URL
                if not self.base_url:
                    self.base_url = provider_config.get("defaultBaseUrl")
                    logger.debug(f"使用提供商默认Base URL: {self.base_url}")

                # 初始化 OpenAI 兼容客户端 (deepseek, siliconflow, openai)
                self.client = OpenAI(
                    api_key=self.api_key,
                    base_url=self.base_url if self.base_url else None
                )
                logger.info(f"AI匹配器初始化成功: {self.provider} ({self.model}) - {self.base_url}")

        except Exception as e:
            logger.error(f"AI匹配器初始化失败: {e}")
            raise

    async def select_best_match(
        self,
        query: Dict[str, Any],
        results: List[ProviderSearchInfo],
        favorited_info: Optional[Dict[str, bool]] = None
    ) -> Optional[int]:
        """
        使用AI从搜索结果中选择最佳匹配

        Args:
            query: 查询信息,包含 title, season, episode, year 等
            results: 搜索结果列表
            favorited_info: 精确标记信息 {provider:mediaId -> isFavorited}

        Returns:
            最佳匹配结果的索引,如果没有合适的匹配则返回None
        """
        if not results:
            return None

        start_time = datetime.now()
        cache_hit = False

        try:
            # 构建输入数据
            results_data = []
            for idx, result in enumerate(results):
                # 检查是否被精确标记
                is_favorited = False
                if favorited_info:
                    key = f"{result.provider}:{result.mediaId}"
                    is_favorited = favorited_info.get(key, False)

                results_data.append({
                    "index": idx,
                    "provider": result.provider,
                    "title": result.title,
                    "type": result.type,
                    "season": result.season,
                    "year": result.year,
                    "episodeCount": result.episodeCount,
                    "isFavorited": is_favorited
                })

            # 尝试从缓存获取
            if self.cache:
                cached_result = self.cache.get(
                    "select_best_match",
                    query=query,
                    results=results_data,
                    favorited=favorited_info
                )
                if cached_result is not None:
                    cache_hit = True
                    duration = (datetime.now() - start_time).total_seconds() * 1000

                    # 记录缓存命中
                    self.metrics.record(AICallMetrics(
                        timestamp=datetime.now(),
                        method="select_best_match",
                        success=True,
                        duration_ms=int(duration),
                        tokens_used=0,
                        model=self.model,
                        cache_hit=True
                    ))

                    return cached_result.get("index", -1) if isinstance(cached_result, dict) else cached_result

            input_data = {
                "query": query,
                "results": results_data
            }

            # 如果有剧集组等价上下文，附加额外提示
            egc = query.get("episode_group_context")
            if egc:
                cs = egc.get("custom_season")
                ce = egc.get("custom_episode")
                ts = egc.get("tmdb_season")
                te = egc.get("tmdb_episode")
                total = egc.get("season_total_episodes", 0)
                input_data["episode_group_hint"] = (
                    f"根据TMDB剧集组映射，S{cs}E{ce}(剧集组分季)等价于S{ts}E{te}(TMDB标准分季)，"
                    f"该季度共{total}集。请优先选择集数接近{total}集或标题包含第{cs}季相关信息的弹幕源。"
                )

            logger.info(f"AI匹配: 开始分析 {len(results)} 个搜索结果")
            logger.debug(f"查询信息: {query}")

            # 根据提供商选择不同的调用方法
            if self.provider == "gemini":
                response_data = await self._match_gemini(input_data)
            else:
                # OpenAI 兼容接口 (deepseek, siliconflow, openai)
                response_data = await self._match_openai(input_data)

            if not response_data:
                logger.warning("AI匹配: 未能获取有效响应")
                return None

            # 检查返回类型
            if not isinstance(response_data, dict):
                logger.error(f"AI匹配: 返回数据类型错误,期望dict,实际为{type(response_data).__name__}: {response_data}")
                return None

            # 解析结果
            index = response_data.get("index", -1)
            confidence = response_data.get("confidence", 0)
            reason = response_data.get("reason", "")

            if index < 0 or index >= len(results):
                logger.info(f"AI匹配: 未找到合适的匹配 (reason: {reason})")
                # 缓存负结果
                if self.cache:
                    self.cache.set(
                        response_data,
                        "select_best_match",
                        query=query,
                        results=results_data,
                        favorited=favorited_info
                    )
                return None

            selected = results[index]
            logger.info(f"AI匹配: 选择结果 #{index} - {selected.provider}:{selected.title} (置信度: {confidence}%, 理由: {reason})")

            # 缓存结果
            if self.cache:
                self.cache.set(
                    response_data,
                    "select_best_match",
                    query=query,
                    results=results_data,
                    favorited=favorited_info
                )

            return index

        except Exception as e:
            duration = (datetime.now() - start_time).total_seconds() * 1000

            # 记录失败
            self.metrics.record(AICallMetrics(
                timestamp=datetime.now(),
                method="select_best_match",
                success=False,
                duration_ms=int(duration),
                tokens_used=0,
                model=self.model,
                error=str(e),
                cache_hit=cache_hit
            ))

            logger.error(f"AI匹配过程中发生错误: {e}", exc_info=True)
            return None

    async def _match_openai(self, input_data: Dict[str, Any]) -> Optional[Dict]:
        """使用OpenAI兼容接口进行匹配"""
        if not self.client:
            return None

        start_time = datetime.now()

        try:
            user_prompt = json.dumps(input_data, ensure_ascii=False, indent=2)

            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": self.match_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                temperature=0.0,
                response_format={"type": "json_object"},
                timeout=30
            )

            content = _extract_openai_content(response)
            if content is None:
                return None
            logger.debug(f"AI原始响应: {content}")

            parsed_data = _safe_json_loads(content, log_raw_response=self.log_raw_response)
            if parsed_data:
                logger.debug(f"解析后的数据类型: {type(parsed_data).__name__}, 内容: {parsed_data}")

            # 记录成功调用
            duration = (datetime.now() - start_time).total_seconds() * 1000
            self.metrics.record(AICallMetrics(
                timestamp=datetime.now(),
                method="select_best_match",
                success=True,
                duration_ms=int(duration),
                tokens_used=response.usage.total_tokens if hasattr(response, 'usage') else 0,
                model=self.model,
                cache_hit=False
            ))

            return parsed_data

        except Exception as e:
            # 记录失败调用
            duration = (datetime.now() - start_time).total_seconds() * 1000
            self.metrics.record(AICallMetrics(
                timestamp=datetime.now(),
                method="select_best_match",
                success=False,
                duration_ms=int(duration),
                tokens_used=0,
                model=self.model,
                error=str(e),
                cache_hit=False
            ))

            logger.error(f"OpenAI匹配调用失败: {e}")
            return None

    async def _match_gemini(self, input_data: Dict[str, Any]) -> Optional[Dict]:
        """使用Gemini官方SDK进行匹配"""
        if not self.client:
            return None

        start_time = datetime.now()

        try:
            user_prompt = json.dumps(input_data, ensure_ascii=False, indent=2)
            full_prompt = f"{self.match_prompt}\n\n{user_prompt}"

            response = self.client.models.generate_content(
                model=self.model,
                contents=full_prompt,
                config={
                    "temperature": 0.0,
                    "response_mime_type": "application/json"
                }
            )

            content = response.text
            logger.debug(f"Gemini原始响应: {content}")

            parsed_data = _safe_json_loads(content, log_raw_response=self.log_raw_response)
            if parsed_data:
                logger.debug(f"解析后的数据类型: {type(parsed_data).__name__}, 内容: {parsed_data}")

            # 记录成功调用
            duration = (datetime.now() - start_time).total_seconds() * 1000
            self.metrics.record(AICallMetrics(
                timestamp=datetime.now(),
                method="select_best_match",
                success=True,
                duration_ms=int(duration),
                tokens_used=0,  # Gemini SDK 不直接提供 token 统计
                model=self.model,
                cache_hit=False
            ))

            return parsed_data

        except Exception as e:
            # 记录失败调用
            duration = (datetime.now() - start_time).total_seconds() * 1000
            self.metrics.record(AICallMetrics(
                timestamp=datetime.now(),
                method="select_best_match",
                success=False,
                duration_ms=int(duration),
                tokens_used=0,
                model=self.model,
                error=str(e),
                cache_hit=False
            ))

            logger.error(f"Gemini匹配调用失败: {e}")
            return None

    async def recognize_title(self, title: str, year: Optional[int] = None, anime_type: str = "tv_series") -> Optional[Dict[str, Any]]:
        """
        使用AI将标题信息标准化,生成适合TMDB搜索的查询关键词

        Args:
            title: 标题字符串
            year: 年份 (可选)
            anime_type: 类型 ("tv_series" 或 "movie")

        Returns:
            标准化后的信息,包含 search_title, season, type, year 等字段
            如果识别失败则返回None
        """
        if not title:
            return None

        start_time = datetime.now()
        cache_hit = False

        try:
            # 尝试从缓存获取
            if self.cache:
                cached_result = self.cache.get(
                    "recognize_title",
                    title=title,
                    year=year,
                    anime_type=anime_type
                )
                if cached_result is not None:
                    cache_hit = True
                    duration = (datetime.now() - start_time).total_seconds() * 1000

                    # 记录缓存命中
                    self.metrics.record(AICallMetrics(
                        timestamp=datetime.now(),
                        method="recognize_title",
                        success=True,
                        duration_ms=int(duration),
                        tokens_used=0,
                        model=self.model,
                        cache_hit=True
                    ))

                    return cached_result

            input_data = {
                "title": title,
                "year": year,
                "type": anime_type
            }
            logger.info(f"AI识别: 开始标准化标题 - {input_data}")

            # 根据提供商选择不同的调用方法
            if self.provider == "gemini":
                response_data = await self._recognize_gemini(input_data)
            else:
                # OpenAI 兼容接口 (deepseek, siliconflow, openai)
                response_data = await self._recognize_openai(input_data)

            if not response_data:
                logger.warning("AI识别: 未能获取有效响应")
                return None

            # 检查返回类型
            if not isinstance(response_data, dict):
                logger.error(f"AI识别: 返回数据类型错误,期望dict,实际为{type(response_data).__name__}: {response_data}")
                return None

            # 验证必需字段
            if "search_title" not in response_data:
                logger.error(f"AI识别: 返回数据缺少search_title字段: {response_data}")
                return None

            logger.info(f"AI识别: 标准化成功 - {response_data}")

            # 缓存结果
            if self.cache:
                self.cache.set(
                    response_data,
                    "recognize_title",
                    title=title,
                    year=year,
                    anime_type=anime_type
                )

            return response_data

        except Exception as e:
            duration = (datetime.now() - start_time).total_seconds() * 1000

            # 记录失败
            self.metrics.record(AICallMetrics(
                timestamp=datetime.now(),
                method="recognize_title",
                success=False,
                duration_ms=int(duration),
                tokens_used=0,
                model=self.model,
                error=str(e),
                cache_hit=cache_hit
            ))

            logger.error(f"AI识别过程中发生错误: {e}", exc_info=True)
            return None

    async def _recognize_openai(self, input_data: Dict[str, Any]) -> Optional[Dict]:
        """使用OpenAI兼容接口进行识别"""
        if not self.client:
            return None

        start_time = datetime.now()

        try:
            import json
            user_prompt = json.dumps(input_data, ensure_ascii=False, indent=2)

            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": self.recognition_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                temperature=0.0,
                response_format={"type": "json_object"},
                timeout=30
            )

            content = _extract_openai_content(response)
            if content is None:
                return None
            logger.debug(f"AI识别原始响应: {content}")

            parsed_data = _safe_json_loads(content, log_raw_response=self.log_raw_response)
            if parsed_data:
                logger.debug(f"解析后的数据类型: {type(parsed_data).__name__}, 内容: {parsed_data}")

            # 记录成功调用
            duration = (datetime.now() - start_time).total_seconds() * 1000
            self.metrics.record(AICallMetrics(
                timestamp=datetime.now(),
                method="recognize_title",
                success=True,
                duration_ms=int(duration),
                tokens_used=response.usage.total_tokens if hasattr(response, 'usage') else 0,
                model=self.model,
                cache_hit=False
            ))

            return parsed_data

        except Exception as e:
            # 记录失败调用
            duration = (datetime.now() - start_time).total_seconds() * 1000
            self.metrics.record(AICallMetrics(
                timestamp=datetime.now(),
                method="recognize_title",
                success=False,
                duration_ms=int(duration),
                tokens_used=0,
                model=self.model,
                error=str(e),
                cache_hit=False
            ))

            logger.error(f"OpenAI识别调用失败: {e}")
            return None

    async def _recognize_gemini(self, input_data: Dict[str, Any]) -> Optional[Dict]:
        """使用Gemini官方SDK进行识别"""
        if not self.client:
            return None

        start_time = datetime.now()

        try:
            import json
            user_prompt = json.dumps(input_data, ensure_ascii=False, indent=2)
            full_prompt = f"{self.recognition_prompt}\n\n{user_prompt}"

            response = self.client.models.generate_content(
                model=self.model,
                contents=full_prompt,
                config={
                    "temperature": 0.0,
                    "response_mime_type": "application/json"
                }
            )

            content = response.text
            logger.debug(f"Gemini识别原始响应: {content}")

            parsed_data = _safe_json_loads(content, log_raw_response=self.log_raw_response)
            if parsed_data:
                logger.debug(f"解析后的数据类型: {type(parsed_data).__name__}, 内容: {parsed_data}")

            # 记录成功调用
            duration = (datetime.now() - start_time).total_seconds() * 1000
            self.metrics.record(AICallMetrics(
                timestamp=datetime.now(),
                method="recognize_title",
                success=True,
                duration_ms=int(duration),
                tokens_used=0,  # Gemini SDK 不直接提供 token 统计
                model=self.model,
                cache_hit=False
            ))

            return parsed_data

        except Exception as e:
            # 记录失败调用
            duration = (datetime.now() - start_time).total_seconds() * 1000
            self.metrics.record(AICallMetrics(
                timestamp=datetime.now(),
                method="recognize_title",
                success=False,
                duration_ms=int(duration),
                tokens_used=0,
                model=self.model,
                error=str(e),
                cache_hit=False
            ))

            logger.error(f"Gemini识别调用失败: {e}")
            return None

    async def expand_aliases(
        self,
        title: str,
        year: Optional[int],
        media_type: str,
        existing_aliases: List[str]
    ) -> Optional[Dict[str, Any]]:
        """
        AI别名扩展 - 生成可能的别名用于搜索

        Args:
            title: 作品标题（可能是非中文）
            year: 年份
            media_type: 类型（tv_series/movie）
            existing_aliases: 已有别名列表

        Returns:
            {
                "aliases": [{"name": "别名", "confidence": "high/medium/low"}],
                "recommendedSource": "bangumi/douban"
            }
        """
        if not self.client:
            logger.warning("AI别名扩展: AI客户端未初始化")
            return None

        try:
            # 构建输入数据
            input_data = {
                "title": title,
                "year": year,
                "type": media_type,
                "existing_aliases": existing_aliases
            }

            logger.info(f"AI别名扩展: 开始生成别名 - {input_data}")

            # 根据提供商选择不同的调用方法
            if self.provider == "gemini":
                response_data = await self._expand_aliases_gemini(input_data)
            else:
                # OpenAI 兼容接口 (deepseek, siliconflow, openai)
                response_data = await self._expand_aliases_openai(input_data)

            if not response_data:
                logger.warning("AI别名扩展: 未获取到有效响应")
                return None

            # 检查返回类型
            if not isinstance(response_data, dict):
                logger.error(f"AI别名扩展: 返回数据类型错误,期望dict,实际为{type(response_data).__name__}: {response_data}")
                return None

            # 验证必需字段
            if "aliases" not in response_data or not isinstance(response_data["aliases"], list):
                logger.error(f"AI别名扩展: 返回数据缺少aliases字段或类型错误: {response_data}")
                return None

            logger.info(f"AI别名扩展: 生成成功 - {response_data}")
            return response_data

        except Exception as e:
            logger.error(f"AI别名扩展过程中发生错误: {e}", exc_info=True)
            return None

    async def _expand_aliases_openai(self, input_data: Dict[str, Any]) -> Optional[Dict]:
        """使用OpenAI兼容接口进行别名扩展"""
        if not self.client:
            return None

        try:
            import json

            # 获取别名扩展提示词
            alias_expansion_prompt = self.config.get("ai_alias_expansion_prompt", "")
            if not alias_expansion_prompt:
                alias_expansion_prompt = DEFAULT_AI_ALIAS_EXPANSION_PROMPT

            user_prompt = json.dumps(input_data, ensure_ascii=False, indent=2)

            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": alias_expansion_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                temperature=0.0,
                response_format={"type": "json_object"},
                timeout=30
            )

            content = _extract_openai_content(response)
            if content is None:
                return None
            logger.debug(f"AI别名扩展原始响应: {content}")

            parsed_data = _safe_json_loads(content, log_raw_response=self.log_raw_response)
            if parsed_data:
                logger.debug(f"解析后的数据类型: {type(parsed_data).__name__}, 内容: {parsed_data}")

            return parsed_data

        except Exception as e:
            logger.error(f"OpenAI别名扩展调用失败: {e}")
            return None

    async def _expand_aliases_gemini(self, input_data: Dict[str, Any]) -> Optional[Dict]:
        """使用Gemini官方SDK进行别名扩展"""
        if not self.client:
            return None

        try:
            import json

            # 获取别名扩展提示词
            alias_expansion_prompt = self.config.get("ai_alias_expansion_prompt", "")
            if not alias_expansion_prompt:
                alias_expansion_prompt = DEFAULT_AI_ALIAS_EXPANSION_PROMPT

            user_prompt = json.dumps(input_data, ensure_ascii=False, indent=2)
            full_prompt = f"{alias_expansion_prompt}\n\n{user_prompt}"

            response = self.client.models.generate_content(
                model=self.model,
                contents=full_prompt,
                config={
                    "temperature": 0.0,
                    "response_mime_type": "application/json"
                }
            )

            content = response.text
            logger.debug(f"Gemini别名扩展原始响应: {content}")

            parsed_data = _safe_json_loads(content, log_raw_response=self.log_raw_response)
            if parsed_data:
                logger.debug(f"解析后的数据类型: {type(parsed_data).__name__}, 内容: {parsed_data}")

            return parsed_data

        except Exception as e:
            logger.error(f"Gemini别名扩展调用失败: {e}")
            return None

    def validate_aliases(
        self,
        title: str,
        year: Optional[int],
        anime_type: str,
        aliases: List[str],
        custom_prompt: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """
        使用AI验证并分类别名

        Args:
            title: 作品标题
            year: 年份
            anime_type: 类型 (tv_series/movie)
            aliases: 待验证的别名列表
            custom_prompt: 自定义提示词 (可选,优先级高于实例配置)

        Returns:
            {
                "nameEn": "英文名",
                "nameJp": "日文名",
                "nameRomaji": "罗马音",
                "aliasesCn": ["中文别名1", "中文别名2", "中文别名3"]
            }
            如果失败返回None
        """
        if not self.client:
            logger.error("AI客户端未初始化")
            return None

        if not aliases:
            logger.warning("别名列表为空,跳过验证")
            return None

        try:
            # 使用自定义提示词 > 实例配置 > 默认提示词
            validation_prompt = custom_prompt or self.alias_validation_prompt

            # 构建输入数据
            input_data = {
                "title": title,
                "year": year,
                "type": anime_type,
                "aliases": aliases
            }

            logger.info(f"正在使用AI验证别名: title='{title}', aliases={len(aliases)}个")
            logger.debug(f"AI别名验证输入: {input_data}")

            user_prompt = json.dumps(input_data, ensure_ascii=False, indent=2)

            # 根据提供商选择不同的调用方法
            if self.provider == "gemini":
                full_prompt = f"{validation_prompt}\n\n{user_prompt}"
                response = self.client.models.generate_content(
                    model=self.model,
                    contents=full_prompt,
                    config={
                        "temperature": 0.0,
                        "response_mime_type": "application/json"
                    }
                )
                content = response.text
            else:
                # OpenAI 兼容接口 (deepseek, siliconflow, openai)
                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": validation_prompt},
                        {"role": "user", "content": user_prompt}
                    ],
                    temperature=0.0,
                    response_format={"type": "json_object"},
                    timeout=30
                )
                content = _extract_openai_content(response)
                if content is None:
                    return None

            logger.debug(f"AI别名验证原始响应: {content}")

            parsed_data = _safe_json_loads(content, log_raw_response=self.log_raw_response)
            if parsed_data:
                logger.info(f"AI别名验证成功: nameEn={parsed_data.get('nameEn')}, nameJp={parsed_data.get('nameJp')}, nameRomaji={parsed_data.get('nameRomaji')}, aliasesCn={len(parsed_data.get('aliasesCn', []))}个")
                logger.debug(f"解析后的数据: {parsed_data}")

            return parsed_data

        except Exception as e:
            logger.error(f"AI别名验证失败: {e}")
            return None

    async def batch_recognize_titles(
        self,
        items: List[Dict[str, Any]],
        max_concurrent: int = 5
    ) -> List[Optional[Dict[str, Any]]]:
        """
        批量识别标题(并发调用)

        Args:
            items: 待识别的项目列表,每个包含 {"title": "...", "year": 2023, "type": "tv_series"}
            max_concurrent: 最大并发数

        Returns:
            识别结果列表,与输入顺序对应
        """
        import asyncio

        semaphore = asyncio.Semaphore(max_concurrent)

        async def recognize_with_limit(item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
            async with semaphore:
                return await self.recognize_title(
                    item.get("title", ""),
                    item.get("year"),
                    item.get("type", "tv_series")
                )

        self.logger.info(f"批量识别: 开始处理 {len(items)} 个标题 (最大并发: {max_concurrent})")

        tasks = [recognize_with_limit(item) for item in items]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # 处理异常
        processed_results = []
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                self.logger.error(f"批量识别: 第 {i} 个项目失败: {result}")
                processed_results.append(None)
            else:
                processed_results.append(result)

        success_count = sum(1 for r in processed_results if r is not None)
        self.logger.info(f"批量识别: 完成 {success_count}/{len(items)} 个标题")

        return processed_results

    async def select_best_season_for_title(
        self,
        title: str,
        season_options: List[Dict[str, Any]]
    ) -> Optional[int]:
        """
        使用AI从季度选项中选择最匹配的季度（V2.1.6混合策略）

        Args:
            title: 标题
            season_options: 季度选项列表，每个包含 season_number 和 name

        Returns:
            最佳季度号，如果无法确定则返回None
        """
        if not season_options or not self.client:
            return None

        try:
            # V2.1.6: 先使用算法相似度匹配
            from src.utils.season_mapper import title_contains_season_name

            best_confidence = 0.0
            best_season = None
            best_season_name = ""

            # 遍历所有季度选项，使用算法计算相似度
            for option in season_options:
                season_num = option.get("season_number", 0)
                season_name = option.get("name", f"第{season_num}季")
                season_aliases = option.get("aliases", [])

                # 使用算法相似度计算
                confidence = title_contains_season_name(title, season_num, season_name, season_aliases)

                if confidence > best_confidence:
                    best_confidence = confidence
                    best_season = season_num
                    best_season_name = season_name

            # 如果算法相似度达到阈值(60%)，直接返回结果
            if best_confidence >= 60.0:
                self.logger.info(f"算法季度匹配: '{title}' → S{best_season} ({best_season_name}) (置信度: {best_confidence:.1f}%)")
                return best_season

            # 算法相似度不足，使用AI兜底
            self.logger.debug(f"算法相似度不足: '{title}' (最高相似度: {best_confidence:.1f}% < 60.0%), 使用AI兜底")

            # 构建季度选项描述
            options_text = ""
            for i, option in enumerate(season_options):
                season_num = option.get("season_number", 0)
                season_name = option.get("name", f"第{season_num}季")
                season_aliases = option.get("aliases", [])
                alias_text = f" (别名: {', '.join(season_aliases)})" if season_aliases else ""
                options_text += f"{i+1}. 第{season_num}季: {season_name}{alias_text}\n"

            # 使用公共AI季度匹配提示词
            from .ai_prompts import DEFAULT_AI_SEASON_MATCH_PROMPT
            prompt = DEFAULT_AI_SEASON_MATCH_PROMPT.format(
                title=title,
                options_text=options_text
            )

            # 调用AI
            response_data = await self._season_match_universal({"title": title, "options": season_options})

            if not response_data:
                return None

            # 解析AI响应
            ai_response = response_data.get("response", "").strip()

            # 尝试提取数字
            import re
            match = re.search(r'\d+', ai_response)
            if match:
                selected_season = int(match.group(0))
                # 验证选择的季度是否在选项中
                for option in season_options:
                    if option.get("season_number") == selected_season:
                        self.logger.info(f"AI季度匹配: '{title}' → S{selected_season}")
                        return selected_season

            self.logger.debug(f"AI季度匹配: '{title}' → 无法解析响应: {ai_response}")
            return None

        except Exception as e:
            self.logger.error(f"AI季度匹配失败: {e}")
            return None

    async def select_metadata_result(
        self,
        title: str,
        year: Optional[int],
        candidates: List[Dict[str, Any]],
        season: Optional[int] = None,
        custom_prompt: Optional[str] = None
    ) -> Optional[int]:
        """
        使用AI从元数据搜索结果中选择最佳匹配(通用方法)

        Args:
            title: 查询标题
            year: 年份(可选)
            candidates: 元数据候选结果列表,每个包含:
                - source: 元数据源 ('tmdb', 'tvdb', etc.)
                - id: 源的ID
                - title: 标题
                - original_title: 原标题
                - year: 年份
                - overview: 简介
            season: 季度号(可选)
            custom_prompt: 自定义提示词(可选,优先级高于默认提示词)

        Returns:
            最佳匹配结果的索引,如果没有合适的匹配则返回None
        """
        if not candidates:
            return None

        if not self.client:
            logger.warning("AI客户端未初始化,使用简单规则选择")
            return 0  # 返回第一个结果

        try:
            # 使用自定义提示词或默认提示词
            system_prompt = custom_prompt or DEFAULT_AI_SEASON_MAPPING_PROMPT

            # 构建用户输入
            input_data = {
                "query": {
                    "title": title,
                    "year": year
                },
                "results": []
            }

            # 如果指定了季度,添加到查询中
            if season is not None:
                input_data["query"]["season"] = season

            for idx, candidate in enumerate(candidates):
                result_data = {
                    "index": idx,
                    "source": candidate.get("source", "unknown"),
                    "id": candidate.get("id"),
                    "title": candidate.get("title"),
                    "year": candidate.get("year")
                }

                # 添加原标题(如果不同)
                original_title = candidate.get("original_title")
                if original_title and original_title != candidate.get("title"):
                    result_data["original_title"] = original_title

                # 添加简介(截取前150字符)
                overview = candidate.get("overview")
                if overview:
                    result_data["overview"] = overview[:150] + ("..." if len(overview) > 150 else "")

                input_data["results"].append(result_data)

            logger.info(f"AI元数据匹配: 开始分析 {len(candidates)} 个候选结果")

            user_prompt = json.dumps(input_data, ensure_ascii=False, indent=2)

            # 根据提供商选择不同的调用方法
            if self.provider == "gemini":
                full_prompt = f"{system_prompt}\n\n{user_prompt}"
                response = self.client.models.generate_content(
                    model=self.model,
                    contents=full_prompt,
                    config={
                        "temperature": 0.0,
                        "response_mime_type": "application/json"
                    }
                )
                content = response.text
            else:
                # OpenAI 兼容接口 (deepseek, siliconflow, openai)
                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt}
                    ],
                    temperature=0.0,
                    response_format={"type": "json_object"},
                    timeout=30
                )
                content = _extract_openai_content(response)
                if content is None:
                    return 0  # 返回第一个结果

            if self.log_raw_response:
                ai_responses_logger.info(f"[元数据匹配] 原始响应: {content}")

            parsed_data = _safe_json_loads(content, log_raw_response=self.log_raw_response)

            if not parsed_data:
                logger.warning("AI元数据匹配: 未能解析响应")
                return 0  # 返回第一个结果

            # 解析结果
            index = parsed_data.get("index", -1)
            confidence = parsed_data.get("confidence", 0)
            reason = parsed_data.get("reason", "")

            if index < 0 or index >= len(candidates):
                logger.info(f"AI元数据匹配: 未找到合适的匹配 (reason: {reason})")
                return 0  # 返回第一个结果

            selected = candidates[index]
            logger.info(f"AI元数据匹配: 选择结果 #{index} - {selected.get('source')}:{selected.get('title')} (ID: {selected.get('id')}, 置信度: {confidence}%, 理由: {reason})")

            return index

        except Exception as e:
            logger.error(f"AI元数据匹配过程中发生错误: {e}", exc_info=True)
            return 0  # 返回第一个结果

    async def _season_match_universal(self, input_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """通用季度匹配函数，支持所有AI提供商"""
        try:
            title = input_data.get("title", "")
            options = input_data.get("options", [])

            # 构建季度选项描述，包含别名
            options_text = ""
            for i, option in enumerate(options):
                season_num = option.get("season_number", 0)
                season_name = option.get("name", f"第{season_num}季")
                season_aliases = option.get("aliases", [])

                # 构建包含别名的描述
                alias_text = f" (别名: {', '.join(season_aliases)})" if season_aliases else ""
                options_text += f"{i+1}. 第{season_num}季: {season_name}{alias_text}\n"

            # 使用公共AI季度匹配提示词
            from .ai_prompts import DEFAULT_AI_SEASON_MATCH_PROMPT
            prompt = DEFAULT_AI_SEASON_MATCH_PROMPT.format(
                title=title,
                options_text=options_text
            )

            # 根据提供商调用不同的API
            if self.provider == "gemini":
                response = await self.client.generate_content(
                    model=self.model,
                    contents=prompt
                )
                ai_response = response.text.strip()
            else:
                # OpenAI 兼容接口 (deepseek, siliconflow, openai)
                response = await self.client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": "你是一个专业的季度识别助手，擅长分析动漫标题中的季度信息。"},
                        {"role": "user", "content": prompt}
                    ],
                    temperature=0.1,
                    max_tokens=50
                )
                content = _extract_openai_content(response)
                if content is None:
                    return None
                ai_response = content.strip()

            return {"response": ai_response}

        except Exception as e:
            self.logger.error(f"季度匹配失败 ({self.provider}): {e}")
            return None



    async def select_best_episode_group(
        self,
        title: str,
        season: Optional[int],
        episode: Optional[int],
        episode_groups: List[Dict[str, Any]]
    ) -> Optional[int]:
        """
        使用混合策略（算法优先+AI兜底）从TMDB剧集组列表中选择最佳剧集组。

        Args:
            title: 作品标题
            season: 季度号(可选)
            episode: 集数号(可选)
            episode_groups: TMDB剧集组列表，每个包含 id, name, type, groupCount, episodeCount 等

        Returns:
            选中的group在列表中的索引，如果无法确定则返回None
        """
        if not episode_groups:
            return None

        # 如果只有一个剧集组，直接选择
        if len(episode_groups) == 1:
            self.logger.info(f"剧集组选择: '{title}' 仅有1个剧集组 '{episode_groups[0].get('name')}', 直接选择")
            return 0

        # === 算法优先策略 ===

        # 策略1: 优先查找名为 "Seasons" 的剧集组
        for i, g in enumerate(episode_groups):
            group_name = g.get("name", "").lower()
            if "seasons" in group_name:
                self.logger.info(f"剧集组选择(算法): '{title}' → 找到'Seasons'剧集组: {g.get('name')} (index={i})")
                return i

        # 策略2: 仅有1个 type=1 (播出顺序) 的剧集组时直接选择
        type1_groups = [(i, g) for i, g in enumerate(episode_groups) if g.get("type") == 1]
        if len(type1_groups) == 1:
            idx, grp = type1_groups[0]
            self.logger.info(f"剧集组选择(算法): '{title}' → 唯一type=1剧集组: {grp.get('name')} (index={idx})")
            return idx

        # 策略3: 如果有明确的季度信息，尝试名称匹配
        if season is not None and season > 1:
            season_keywords = [f"season {season}", f"第{season}季", f"s{season}", f"season{season}"]
            for i, g in enumerate(episode_groups):
                group_name = g.get("name", "").lower()
                for kw in season_keywords:
                    if kw in group_name:
                        self.logger.info(f"剧集组选择(算法): '{title}' → 名称匹配季度{season}: {g.get('name')} (index={i})")
                        return i

        # === AI 兜底 ===
        if not self.client:
            # 没有AI客户端，回退到选择 type=1 中 episodeCount 最多的
            if type1_groups:
                best_idx, best_grp = max(type1_groups, key=lambda x: x[1].get("episodeCount", 0))
                self.logger.info(f"剧集组选择(回退): '{title}' → type=1中集数最多: {best_grp.get('name')} (index={best_idx})")
                return best_idx
            self.logger.warning(f"剧集组选择: '{title}' → 无AI客户端且无type=1组，无法选择")
            return None

        try:
            self.logger.info(f"剧集组选择(AI): '{title}' → 算法无法确定，使用AI从{len(episode_groups)}个组中选择")

            # 构建AI输入
            groups_for_ai = []
            for i, g in enumerate(episode_groups):
                groups_for_ai.append({
                    "index": i,
                    "id": g.get("id", ""),
                    "name": g.get("name", ""),
                    "type": g.get("type", 0),
                    "groupCount": g.get("groupCount", 0),
                    "episodeCount": g.get("episodeCount", 0),
                    "description": g.get("description", "")[:100],
                })

            input_data = {
                "title": title,
                "season": season,
                "episode": episode,
                "episode_groups": groups_for_ai,
            }

            system_prompt = DEFAULT_AI_EPISODE_GROUP_SELECT_PROMPT
            user_prompt = json.dumps(input_data, ensure_ascii=False, indent=2)

            # 根据提供商调用不同的API
            if self.provider == "gemini":
                full_prompt = f"{system_prompt}\n\n{user_prompt}"
                response = self.client.models.generate_content(
                    model=self.model,
                    contents=full_prompt,
                    config={
                        "temperature": 0.0,
                        "response_mime_type": "application/json"
                    }
                )
                content = response.text
            else:
                # OpenAI 兼容接口
                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt}
                    ],
                    temperature=0.0,
                    response_format={"type": "json_object"},
                    timeout=30
                )
                content = _extract_openai_content(response)
                if content is None:
                    self.logger.warning(f"剧集组选择(AI): '{title}' → AI返回空内容")
                    return None

            if self.log_raw_response:
                ai_responses_logger.info(f"[剧集组选择] 原始响应: {content}")

            parsed_data = _safe_json_loads(content, log_raw_response=self.log_raw_response)

            if not parsed_data:
                self.logger.warning(f"剧集组选择(AI): '{title}' → 无法解析AI响应")
                return None

            index = parsed_data.get("index", -1)
            confidence = parsed_data.get("confidence", 0)
            reason = parsed_data.get("reason", "")

            if index < 0 or index >= len(episode_groups):
                self.logger.info(f"剧集组选择(AI): '{title}' → AI未找到合适的组 (reason: {reason})")
                return None

            selected = episode_groups[index]
            self.logger.info(f"剧集组选择(AI): '{title}' → 选择 #{index} '{selected.get('name')}' (置信度: {confidence}%, 理由: {reason})")
            return index

        except Exception as e:
            self.logger.error(f"剧集组选择(AI)失败: {e}", exc_info=True)
            return None

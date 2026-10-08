"""AI 能力统一入口：管理全局实例、配置热更新和底层匹配器缓存。"""

import asyncio
import logging
from typing import Any, Dict, List, Optional

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.ai.ai_matcher import AIMatcher
from src.ai.ai_metrics import AICallMetrics
from src.services.config_service import ConfigService
from src.services.service_container import get_database_service

logger = logging.getLogger(__name__)


class AIService:
    """提供 AI 能力；候选上下文收集与传统兜底策略由编排层负责。"""

    def __init__(self, config_service: ConfigService, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self.config_service = config_service
        # 保留启动接口参数以兼容现有生命周期调用；数据库访问统一从全局 DatabaseService 获取。
        _ = session_factory
        self._matcher: Optional[AIMatcher] = None
        self._config: Dict[str, Any] = {}
        # 并发预热和搜索共享构建锁，避免同一配置创建多个客户端。
        self._lock = asyncio.Lock()
        self._missing_key_warned = False

    async def persist_metric(self, metric: AICallMetrics) -> None:
        """通过统一数据库服务持久化一条 AI 调用指标。"""
        await self._persist_ai_metric(metric)

    async def _persist_ai_metric(self, metric: AICallMetrics) -> None:
        """执行 AI 指标持久化，事务边界由 AIService 统一管理。"""
        db = get_database_service()
        async with db.transaction():
            await db.ai_metrics.create(
                timestamp=metric.timestamp,
                method=metric.method,
                success=metric.success,
                duration_ms=metric.duration_ms,
                tokens_used=metric.tokens_used,
                model=metric.model,
                error=metric.error,
                cache_hit=metric.cache_hit,
            )

    async def is_enabled(self) -> bool:
        """读取 AI 功能开关，不代表密钥已配置。"""
        return (await self.config_service.get("aiMatchEnabled", "false")).lower() == "true"

    async def is_available(self) -> bool:
        """仅检查 AI 凭据是否可用；具体功能开关由调用编排层分别控制。"""
        try:
            api_key = await self.config_service.get("aiApiKey", "")
            if not api_key or not api_key.strip():
                if not self._missing_key_warned:
                    logger.warning("AI API密钥未配置，跳过 AI 能力")
                    self._missing_key_warned = True
                return False
            self._missing_key_warned = False
            return True
        except Exception:
            logger.exception("检查 AI 可用性失败")
            return False

    def get_initialized_matcher(self) -> Optional[AIMatcher]:
        """仅获取已初始化实例供统计与清理使用，不触发配置加载或客户端创建。"""
        return self._matcher

    async def _get_config(self) -> Dict[str, Any]:
        fields = {
            "ai_match_provider": ("aiProvider", "deepseek"),
            "ai_match_api_key": ("aiApiKey", ""),
            "ai_match_base_url": ("aiBaseUrl", ""),
            "ai_match_model": ("aiModel", ""),
            "ai_match_prompt": ("aiPrompt", ""),
            "ai_recognition_prompt": ("aiRecognitionPrompt", ""),
            "ai_alias_validation_prompt": ("aiAliasValidationPrompt", ""),
            "ai_alias_expansion_prompt": ("aiAliasExpansionPrompt", ""),
            "ai_episode_group_prompt": ("aiEpisodeGroupPrompt", ""),
            "ai_log_raw_response": ("aiLogRawResponse", "false"),
            "ai_thinking_enabled": ("aiThinkingEnabled", "false"),
            "ai_cache_enabled": ("aiCacheEnabled", "true"),
            "ai_cache_ttl": ("aiCacheTtl", "3600"),
            "ai_call_timeout": ("aiCallTimeout", "60"),
        }
        config = {name: await self.config_service.get(key, default) for name, (key, default) in fields.items()}
        for name in ("ai_log_raw_response", "ai_thinking_enabled", "ai_cache_enabled"):
            config[name] = config[name].lower() == "true"
        for name in ("ai_cache_ttl", "ai_call_timeout"):
            config[name] = int(config[name])
        return config

    async def get_matcher(self) -> Optional[AIMatcher]:
        """获取共享匹配器；提示词热更新，其他运行配置变化时重建。"""
        async with self._lock:
            try:
                # 所有 AI 能力共用此门禁，缺密钥时不读取完整配置或创建客户端。
                if not await self.is_available():
                    return None
                config = await self._get_config()
                # 配置可热更新，构建前再次检查本次快照，避免检查后密钥被清空。
                if not config["ai_match_api_key"] or not config["ai_match_api_key"].strip():
                    return None
                # 提示词变化只更新配置，避免重建客户端并丢弃内存统计。
                prompt_keys = {
                    "ai_match_prompt", "ai_recognition_prompt", "ai_alias_validation_prompt",
                    "ai_alias_expansion_prompt", "ai_episode_group_prompt",
                }
                runtime_changed = any(config[key] != self._config.get(key) for key in config if key not in prompt_keys)
                if self._matcher is None or runtime_changed:
                    matcher = AIMatcher(config, on_metric_record=self.persist_metric)
                    self._matcher = matcher
                elif config != self._config:
                    self._matcher.update_prompts({
                        "match_prompt": config["ai_match_prompt"],
                        "recognition_prompt": config["ai_recognition_prompt"],
                        "alias_validation_prompt": config["ai_alias_validation_prompt"],
                    })
                    self._matcher.config.update(config)
                    # 缓存键不含提示词，热更新后清空旧响应，保留客户端与调用指标。
                    if self._matcher.cache is not None:
                        self._matcher.cache.clear()
                self._config = config
                return self._matcher
            except Exception:
                logger.exception("初始化AI匹配器失败")
                return None

    async def validate_aliases(
        self, title: str, year: Optional[int], anime_type: str, aliases: List[str],
    ) -> Optional[Dict[str, Any]]:
        """通过共享匹配器验证别名；关闭、未就绪或失败时返回空结果。"""
        try:
            # 每次从服务获取匹配器，不依赖标题识别分支是否曾初始化。
            matcher = await self.get_matcher()
            if matcher is not None:
                return await matcher.validate_aliases(title, year, anime_type, aliases)
        except Exception:
            logger.exception("AI别名验证失败")
        return None


    async def select_best_match(
        self, query_info: Dict[str, Any], sorted_results: List[Any],
        favorited_info: Dict[str, bool], existing_info: Optional[Dict[str, bool]] = None,
        recognition_info: Optional[Dict[str, bool]] = None,
    ) -> Optional[int]:
        """选择候选索引；失败返回空结果，由调用编排决定是否兜底。"""
        try:
            matcher = await self.get_matcher()
            if matcher is not None:
                return await matcher.select_best_match(query_info, sorted_results, favorited_info, existing_info, recognition_info)
        except Exception:
            logger.exception("AI匹配失败")
        return None


    async def recognize_title(
        self, title: str, year: Optional[int] = None, anime_type: str = "tv_series",
    ) -> Optional[Dict[str, Any]]:
        """通过共享匹配器标准化标题；调用前统一执行可用性门禁。"""
        try:
            matcher = await self.get_matcher()
            if matcher is not None:
                return await matcher.recognize_title(title, year, anime_type)
        except Exception:
            logger.exception("AI标题识别失败")
        return None

    async def select_best_season_for_title(
        self, title: str, season_options: List[Dict[str, Any]],
    ) -> Optional[int]:
        """通过共享匹配器选择季度；调用前统一执行可用性门禁。"""
        try:
            matcher = await self.get_matcher()
            if matcher is not None:
                return await matcher.select_best_season_for_title(title, season_options)
        except Exception:
            logger.exception("AI季度选择失败")
        return None

    async def select_best_episode_group(
        self, title: str, season: Optional[int], episode: Optional[int],
        episode_groups: List[Dict[str, Any]],
    ) -> Optional[int]:
        """从剧集组中选择最佳索引，失败返回空结果。"""
        try:
            matcher = await self.get_matcher()
            if matcher is not None:
                return await matcher.select_best_episode_group(title, season, episode, episode_groups)
        except Exception:
            logger.exception("AI剧集组选择失败")
        return None

    async def generate_regex(self, description: str, existing_regex: str = "", context: str = "") -> Optional[str]:
        """根据描述生成正则表达式，失败返回空结果。"""
        try:
            matcher = await self.get_matcher()
            if matcher is not None:
                return await matcher.generate_regex(description, existing_regex, context)
        except Exception:
            logger.exception("AI正则生成失败")
        return None

    async def convert_title(self, title: str) -> Optional[str]:
        """使用名称转换专用提示词获取中文标题，失败返回空结果。"""
        try:
            matcher = await self.get_matcher()
            if matcher is not None:
                # 每次读取专用提示词，使配置修改即时生效且无需重建客户端。
                prompt = await self.config_service.get("aiNameConversionPrompt", "")
                return await matcher.convert_title(title, custom_prompt=prompt)
        except Exception:
            logger.exception("AI名称转换失败")
        return None



_global_ai_service: Optional[AIService] = None


def get_ai_service() -> AIService:
    """获取全局 AI 服务；未初始化属于启动错误，不等同于功能关闭。"""
    if _global_ai_service is None:
        raise RuntimeError("AIService 未初始化，请先调用 init_ai_service()")
    return _global_ai_service


def init_ai_service(config_service: ConfigService, session_factory: async_sessionmaker[AsyncSession]) -> AIService:
    """启动时初始化唯一 AI 服务，禁止重复创建导致调用方持有不同实例。"""
    global _global_ai_service
    if _global_ai_service is None:
        _global_ai_service = AIService(config_service, session_factory)
        logger.info("AI服务已初始化")
    return _global_ai_service

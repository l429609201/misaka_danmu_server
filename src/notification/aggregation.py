"""
NotificationAggregator — 统一聚合管理

按消息类型、聚合 key 分桶，支持：
- 时间窗口聚合（TIME_WINDOW）：窗口内消息合并为汇总
- 数量阈值聚合（COUNT_THRESHOLD）：达到数量后合并
- 去重：相同 aggregation_key 在窗口内不重复记录
- 不直接调用渠道，只返回待发送消息给 NotificationManager
"""
import datetime
import logging
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from src.notification.messages.base import AggregationPolicy, NotificationMessage
from src.notification.messages.task import AggregatedSummaryMessage

logger = logging.getLogger(__name__)


@dataclass
class AggregationBucket:
    """聚合桶 — 收集同类消息"""
    key: str
    subscription_key: str = ""
    messages: List[NotificationMessage] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    # 去重 key 集合
    seen_keys: set = field(default_factory=set)


class NotificationAggregator:
    """通知聚合器

    - collect(message): 收集消息，返回需要立即发送的消息列表
    - flush(key): 刷新指定聚合桶
    - flush_all(): 刷新全部聚合桶
    """

    def __init__(self, time_window: float = 30.0, max_count: int = 10,
                 surge_enabled: bool = True, surge_window: float = 30.0,
                 surge_threshold: int = 5):
        """
        Args:
            time_window: 时间窗口（秒），窗口到期后自动 flush
            max_count: 数量阈值，达到后自动 flush
            surge_enabled: 是否启用智能洪峰检测（大量同类消息自动转汇总）
            surge_window: 洪峰检测滑动窗口（秒）
            surge_threshold: 洪峰阈值：窗口内同类消息达到此数量后，后续同类消息转入汇总
        """
        self._time_window = time_window
        self._max_count = max_count
        self._buckets: Dict[str, AggregationBucket] = {}
        # 智能洪峰检测配置
        self._surge_enabled = surge_enabled
        self._surge_window = surge_window
        self._surge_threshold = surge_threshold
        # 每个 message_type 最近的"立即发送"时间戳列表（滑动窗口），用于洪峰判定
        self._recent_sends: Dict[str, List[float]] = {}

    def configure_surge(self, enabled: bool, window: float, threshold: int):
        """运行时更新洪峰检测配置（供 NotificationManager 读取用户配置后调用）"""
        self._surge_enabled = enabled
        self._surge_window = window
        self._surge_threshold = threshold

    # 参与智能洪峰聚合的消息类型（仅成功类；失败类保持逐条，避免错误被淹没）
    _SURGE_ELIGIBLE_TYPES = {
        "import_success",
        "webhook_import_success",
        "auto_import_success",
        "refresh_success",
        "incremental_refresh_success",
    }

    def _record_send(self, message_type: str) -> None:
        """记录一次立即发送的时间戳，并清理滑动窗口外的旧记录。"""
        now = time.time()
        sends = self._recent_sends.setdefault(message_type, [])
        sends.append(now)
        cutoff = now - self._surge_window
        self._recent_sends[message_type] = [t for t in sends if t >= cutoff]

    def _is_surging(self, message_type: str) -> bool:
        """判断某类型是否处于洪峰：滑动窗口内立即发送数已达阈值。"""
        now = time.time()
        cutoff = now - self._surge_window
        sends = [t for t in self._recent_sends.get(message_type, []) if t >= cutoff]
        self._recent_sends[message_type] = sends
        return len(sends) >= self._surge_threshold

    def collect(self, message: NotificationMessage) -> List[NotificationMessage]:
        """收集消息，返回需要立即发送的消息列表

        - 消息自带 TIME_WINDOW / COUNT_THRESHOLD 策略：放入桶，达到条件时返回汇总消息
        - NONE 策略 + 智能洪峰检测：平峰逐条发；同类消息在窗口内超阈值后转入汇总
        """
        # 1. 消息自带聚合策略（如追更失败）：走原有分桶逻辑
        if message.aggregation_policy != AggregationPolicy.NONE:
            return self._collect_into_bucket(message)

        # 2. NONE 策略：智能洪峰检测
        mtype = message.message_type
        if self._surge_enabled and mtype in self._SURGE_ELIGIBLE_TYPES:
            if self._is_surging(mtype):
                # 已进入洪峰：转入按类型的汇总桶（时间窗口结束或数量达标时 flush）
                return self._collect_into_bucket(message, force_key=f"surge:{mtype}")
            # 未洪峰：立即发送并记录时间戳
            self._record_send(mtype)
            return [message]

        # 3. 其它 NONE 消息：立即发送
        return [message]

    def _collect_into_bucket(
        self, message: NotificationMessage, force_key: Optional[str] = None
    ) -> List[NotificationMessage]:
        """将消息放入聚合桶，达到时间/数量条件时返回汇总消息。"""
        agg_key = force_key or message.aggregation_key or message.message_type
        bucket = self._buckets.get(agg_key)

        if bucket is None:
            bucket = AggregationBucket(
                key=agg_key,
                subscription_key=message.subscription_key,
            )
            self._buckets[agg_key] = bucket

        # 去重检查（使用 payload 的某些字段作为去重 key）
        dedup_key = self._make_dedup_key(message)
        if dedup_key and dedup_key in bucket.seen_keys:
            return []  # 重复消息，忽略
        if dedup_key:
            bucket.seen_keys.add(dedup_key)

        bucket.messages.append(message)

        # 检查是否应该 flush：数量达标立即 flush；否则等 flush_expired 按时间窗口刷新
        if len(bucket.messages) >= self._max_count:
            return self._flush_bucket(agg_key)
        return []

    def flush(self, key: str) -> List[NotificationMessage]:
        """刷新指定聚合桶"""
        return self._flush_bucket(key)

    def flush_all(self) -> List[NotificationMessage]:
        """刷新全部聚合桶"""
        result = []
        for key in list(self._buckets.keys()):
            result.extend(self._flush_bucket(key))
        return result

    def flush_expired(self) -> List[NotificationMessage]:
        """刷新所有已过期的聚合桶（定时调用）"""
        now = time.time()
        result = []
        for key, bucket in list(self._buckets.items()):
            if now - bucket.created_at >= self._time_window:
                result.extend(self._flush_bucket(key))
        return result

    def cleanup_expired(self):
        """清理空桶"""
        empty_keys = [k for k, b in self._buckets.items() if not b.messages]
        for k in empty_keys:
            del self._buckets[k]

    def _flush_bucket(self, key: str) -> List[NotificationMessage]:
        """刷新桶，生成汇总消息"""
        bucket = self._buckets.pop(key, None)
        if not bucket or not bucket.messages:
            return []

        # 只有1条消息时直接返回原消息
        if len(bucket.messages) == 1:
            return bucket.messages

        # 多条消息生成汇总
        summary = self._build_summary(bucket)
        return [summary]

    def _build_summary(self, bucket: AggregationBucket) -> NotificationMessage:
        """从明细消息生成汇总消息"""
        items = []
        for msg in bucket.messages:
            items.append(msg.payload)

        now_str = datetime.datetime.now().strftime("%H:%M:%S")
        start_str = datetime.datetime.fromtimestamp(bucket.created_at).strftime("%H:%M:%S")

        return AggregatedSummaryMessage(
            message_type="aggregated_summary",
            payload={
                "count": len(bucket.messages),
                "items": items,
                "time_range": f"{start_str} ~ {now_str}",
                "original_subscription_key": bucket.subscription_key,
                "aggregation_key": bucket.key,
            },
        )

    @staticmethod
    def _make_dedup_key(message: NotificationMessage) -> str:
        """生成去重 key"""
        d = message.payload
        parts = [
            message.message_type,
            d.get("anime_title", ""),
            str(d.get("season", "")),
            str(d.get("episode", "")),
        ]
        return ":".join(parts)

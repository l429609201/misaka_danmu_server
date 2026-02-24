import re
import logging
from typing import Any, Dict, List

# parse_search_keyword 已迁移至 src.utils.filename_parser，此处保留 re-export 以兼容旧导入路径
from src.utils.filename_parser import parse_search_keyword  # noqa: F401

def to_camel(snake_str: str) -> str:
    """将 snake_case 字符串转换为 camelCase。"""
    components = snake_str.split('_')
    # 我们将除第一个之外的每个组件的首字母大写，然后连接起来。
    return components[0] + ''.join(x.title() for x in components[1:])

def convert_keys_to_camel(data: Any) -> Any:
    """
    递归地将字典的键从 snake_case 转换为 camelCase。
    """
    if isinstance(data, dict):
        return {to_camel(k): convert_keys_to_camel(v) for k, v in data.items()}
    if isinstance(data, list):
        return [convert_keys_to_camel(i) for i in data]
    return data

def clean_xml_string(xml_string: str) -> str:
    """
    移除XML字符串中的无效字符以防止解析错误。
    此函数针对XML 1.0规范中非法的控制字符。
    """
    # XML 1.0 规范允许的字符范围: #x9 | #xA | #xD | [#x20-#xD7FF] | [#xE000-#xFFFD] | [#x10000-#x10FFFF]
    # 此正则表达式匹配所有不在上述范围内的字符。
    invalid_xml_char_re = re.compile(
        r'[^\x09\x0A\x0D\x20-\uD7FF\uE000-\uFFFD\U00010000-\U0010FFFF]'
    )
    return invalid_xml_char_re.sub('', xml_string)

def sample_comments_evenly(comments: List[Dict[str, Any]], target_count: int) -> List[Dict[str, Any]]:
    """
    按固定时间段（3分钟）随机均匀采样弹幕

    采样策略:
    1. 将视频按3分钟分段
    2. 按每段弹幕密度比例分配采样配额
    3. 在每段内等间隔采样,确保时间均匀分布
    4. 如有缺口,从有剩余的段中按比例补充

    Args:
        comments: 原始弹幕列表,每个弹幕包含 'p' 字段（时间,类型,字号,颜色,时间戳,弹幕池,用户ID,弹幕ID）
        target_count: 目标弹幕数量

    Returns:
        采样后的弹幕列表
    """
    import random
    import math
    logger = logging.getLogger(__name__)

    if len(comments) <= target_count:
        return comments

    if target_count <= 0:
        return []

    # 固定时间段长度：3分钟 = 180秒
    SEGMENT_DURATION = 180.0

    # 解析弹幕时间并排序
    timed_comments = []
    for comment in comments:
        try:
            p_attr = comment.get('p', '')
            if p_attr:
                # p属性格式：时间,类型,字号,颜色,时间戳,弹幕池,用户ID,弹幕ID
                time_str = p_attr.split(',')[0]
                time_seconds = float(time_str)
                timed_comments.append((time_seconds, comment))
        except (ValueError, IndexError):
            # 如果解析失败，跳过这条弹幕
            continue

    if not timed_comments:
        return comments[:target_count]  # 如果没有有效时间，直接截取

    # 按时间排序
    timed_comments.sort(key=lambda x: x[0])

    # 获取时间范围
    min_time = timed_comments[0][0]
    max_time = timed_comments[-1][0]

    if max_time <= min_time:
        # 如果所有弹幕时间相同，随机采样
        return [comment for _, comment in random.sample(timed_comments, min(target_count, len(timed_comments)))]

    # 计算总时长和段数
    time_duration = max_time - min_time
    total_segments = math.ceil(time_duration / SEGMENT_DURATION)

    logger.debug(f"弹幕采样详情: 时间范围 {min_time:.1f}s - {max_time:.1f}s (总时长 {time_duration:.1f}s), 分成 {total_segments} 段 (每段 {SEGMENT_DURATION}s)")

    # 为每个时间段分配弹幕
    segments = [[] for _ in range(total_segments)]

    for time_seconds, comment in timed_comments:
        # 计算当前弹幕属于哪个时间段
        segment_index = int((time_seconds - min_time) / SEGMENT_DURATION)

        # 确保不超出范围
        if segment_index >= total_segments:
            segment_index = total_segments - 1

        segments[segment_index].append(comment)

    # === 按密度比例分配配额 ===
    # 计算每段的弹幕密度权重
    segment_weights = []
    for i, segment in enumerate(segments):
        weight = len(segment) if len(segment) > 0 else 0
        segment_weights.append(weight)

    total_weight = sum(segment_weights)

    if total_weight == 0:
        # 所有段都没有弹幕
        logger.warning("所有时间段都没有弹幕")
        return []

    # 按权重分配配额
    segment_quotas = []
    allocated_total = 0

    for i, weight in enumerate(segment_weights):
        if weight == 0:
            segment_quotas.append(0)
        else:
            # 按比例分配
            quota = int(target_count * weight / total_weight)
            segment_quotas.append(quota)
            allocated_total += quota

    # 处理余数: 将剩余配额分配给弹幕最多的段
    remainder = target_count - allocated_total
    if remainder > 0:
        # 找出弹幕最多的段
        sorted_indices = sorted(range(len(segment_weights)), key=lambda i: segment_weights[i], reverse=True)
        for i in range(remainder):
            if i < len(sorted_indices):
                idx = sorted_indices[i]
                segment_quotas[idx] += 1

    logger.debug(f"按密度分配配额: {segment_quotas}")

    # === 从每段中等间隔采样 ===
    sampled_comments = []
    segment_stats = []

    for i, segment in enumerate(segments):
        quota = segment_quotas[i]

        if quota == 0 or len(segment) == 0:
            segment_stats.append({
                'index': i,
                'total': len(segment),
                'sampled': 0,
                'remaining': len(segment),
                'deficit': quota
            })
            continue

        if len(segment) >= quota:
            # 弹幕充足,等间隔采样
            if quota == len(segment):
                sampled = segment
            else:
                # 等间隔采样
                step = len(segment) / quota
                sampled = [segment[int(j * step)] for j in range(quota)]

            sampled_comments.extend(sampled)
            segment_stats.append({
                'index': i,
                'total': len(segment),
                'sampled': quota,
                'remaining': len(segment) - quota
            })
            logger.debug(f"时间段 {i} ({i*SEGMENT_DURATION:.0f}s-{(i+1)*SEGMENT_DURATION:.0f}s): 从 {len(segment)} 条中等间隔采样 {quota} 条")
        else:
            # 弹幕不足,全部采样
            sampled_comments.extend(segment)
            segment_stats.append({
                'index': i,
                'total': len(segment),
                'sampled': len(segment),
                'remaining': 0,
                'deficit': quota - len(segment)
            })
            logger.debug(f"时间段 {i} ({i*SEGMENT_DURATION:.0f}s-{(i+1)*SEGMENT_DURATION:.0f}s): 弹幕不足,全部采样 {len(segment)} 条 (缺口 {quota - len(segment)} 条)")

    # === 补充缺口 ===
    total_deficit = sum(stat.get('deficit', 0) for stat in segment_stats)

    if total_deficit > 0:
        logger.debug(f"总缺口: {total_deficit} 条,开始从有剩余的段中按比例补充")

        # 找出有剩余弹幕的段
        segments_with_remaining = [
            (stat['index'], segments[stat['index']], stat['remaining'])
            for stat in segment_stats if stat['remaining'] > 0
        ]

        if segments_with_remaining:
            # 计算每段的补充配额(按剩余数量比例)
            total_remaining = sum(r for _, _, r in segments_with_remaining)

            for seg_idx, segment, remaining in segments_with_remaining:
                # 按比例分配补充配额
                补充配额 = int(total_deficit * remaining / total_remaining)

                if 补充配额 > 0:
                    # 找出该段中未被采样的弹幕
                    already_sampled_set = set(id(c) for c in sampled_comments if c in segment)
                    available = [c for c in segment if id(c) not in already_sampled_set]

                    if available:
                        actual_补充 = min(补充配额, len(available))
                        # 等间隔补充
                        if actual_补充 == len(available):
                            补充_comments = available
                        else:
                            step = len(available) / actual_补充
                            补充_comments = [available[int(j * step)] for j in range(actual_补充)]

                        sampled_comments.extend(补充_comments)
                        logger.debug(f"从时间段 {seg_idx} 等间隔补充 {actual_补充} 条弹幕")

    logger.info(f"弹幕均匀采样完成: 原始{len(comments)}条 -> 采样{len(sampled_comments)}条 (目标{target_count}条, 分{total_segments}段, 每段{SEGMENT_DURATION}s)")

    # 确保返回的数量不超过目标数量
    return sampled_comments[:target_count]

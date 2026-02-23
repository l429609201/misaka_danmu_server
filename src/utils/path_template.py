"""
弹幕文件路径模板解析器

支持的变量：
- ${title}: 作品标题
- ${titleBase}: 标准化标题（去除季度信息，如"第X季"、"第X期"等）
- ${season}: 季度号
- ${episode}: 分集号
- ${year}: 年份
- ${provider}: 数据源提供商
- ${animeId}: 作品ID
- ${episodeId}: 分集ID
- ${sourceId}: 数据源ID

支持的格式化选项：
- ${season:02d}: 季度号补零到2位
- ${episode:02d}: 分集号补零到2位
- ${episode:03d}: 分集号补零到3位
"""

import re
import os
import logging
from pathlib import Path
from typing import Dict, Any, Optional
from string import Template

logger = logging.getLogger(__name__)

# normalize_title 已迁移至统一模块
from src.utils.filename_parser import normalize_title  # noqa: E402


class DanmakuPathTemplate:
    """弹幕文件路径模板解析器"""
    
    def __init__(self, template: str):
        """
        初始化路径模板
        
        Args:
            template: 路径模板字符串，如 "/downloads/${title}/Season ${season}/${title} - S${season:02d}E${episode:02d}"
        """
        self.template = template
        self._validate_template()
    
    def _validate_template(self):
        """验证模板格式是否正确"""
        # 检查是否包含不安全的路径字符
        unsafe_chars = ['..', '\\', '<', '>', '|', '"', '?', '*']
        for char in unsafe_chars:
            if char in self.template:
                raise ValueError(f"路径模板包含不安全字符 '{char}': {self.template}")
    
    def generate_path(self, context: Dict[str, Any]) -> Path:
        """
        根据上下文生成弹幕文件路径
        
        Args:
            context: 包含变量值的字典
            
        Returns:
            生成的文件路径
        """
        try:
            # 清理和准备变量
            clean_context = self._prepare_context(context)
            
            # 处理格式化变量（如 ${season:02d}）
            formatted_template = self._process_formatted_variables(self.template, clean_context)
            
            # 使用 string.Template 进行变量替换
            template = Template(formatted_template)
            resolved_path = template.safe_substitute(clean_context)
            
            # 检查是否有未替换的变量
            if '$' in resolved_path:
                logger.warning(f"路径模板中存在未替换的变量: {resolved_path}")

            # 自动添加.xml后缀（如果没有的话）
            if not resolved_path.endswith('.xml'):
                resolved_path += '.xml'

            # 处理路径分隔符，确保跨平台兼容性
            # 将Unix风格的路径分隔符转换为当前系统的分隔符
            if '/' in resolved_path and not resolved_path.startswith('/'):
                # 相对路径，转换分隔符
                resolved_path = resolved_path.replace('/', os.sep)

            return Path(resolved_path)
            
        except Exception as e:
            logger.error(f"生成弹幕路径失败: {e}, 模板: {self.template}, 上下文: {context}")
            # 回退到默认路径 - 使用相对路径确保源码运行时也能正常工作
            return Path(f"config/danmaku/{context.get('animeId', 'unknown')}/{context.get('episodeId', 'unknown')}.xml")
    
    def _prepare_context(self, context: Dict[str, Any]) -> Dict[str, str]:
        """准备和清理上下文变量"""
        clean_context = {}
        
        for key, value in context.items():
            if value is None:
                clean_context[key] = 'unknown'
            elif isinstance(value, (int, float)):
                clean_context[key] = str(value)
            else:
                # 清理文件名中的非法字符
                clean_value = self._sanitize_filename(str(value))
                clean_context[key] = clean_value
        
        return clean_context
    
    def _sanitize_filename(self, filename: str) -> str:
        """清理文件名中的非法字符"""
        # Windows和Linux都不允许的字符
        illegal_chars = ['<', '>', ':', '"', '|', '?', '*', '\\', '/']
        
        clean_name = filename
        for char in illegal_chars:
            clean_name = clean_name.replace(char, '_')
        
        # 移除前后空格和点
        clean_name = clean_name.strip(' .')
        
        # 如果清理后为空，使用默认值
        if not clean_name:
            clean_name = 'unknown'
        
        return clean_name
    
    def _process_formatted_variables(self, template: str, context: Dict[str, str]) -> str:
        """处理带格式化的变量，如 ${season:02d}"""
        # 匹配格式化变量的正则表达式
        pattern = r'\$\{(\w+):(\w+)\}'
        
        def replace_formatted(match):
            var_name = match.group(1)
            format_spec = match.group(2)
            
            if var_name not in context:
                return match.group(0)  # 保持原样
            
            try:
                value = context[var_name]
                # 尝试转换为数字并格式化
                if format_spec.endswith('d'):  # 整数格式
                    num_value = int(value)
                    return f"{num_value:{format_spec}}"
                else:
                    return value
            except (ValueError, TypeError):
                logger.warning(f"无法格式化变量 {var_name} 的值 '{value}' 使用格式 '{format_spec}'")
                return value
        
        return re.sub(pattern, replace_formatted, template)
    



def create_danmaku_context(anime_title: str, season: int, episode_index: int,
                          year: Optional[int] = None, provider: Optional[str] = None,
                          anime_id: Optional[int] = None, episode_id: Optional[int] = None,
                          source_id: Optional[int] = None) -> Dict[str, Any]:
    """
    创建弹幕路径模板的上下文变量

    Args:
        anime_title: 作品标题
        season: 季度号
        episode_index: 分集号
        year: 年份
        provider: 数据源提供商
        anime_id: 作品ID
        episode_id: 分集ID
        source_id: 数据源ID

    Returns:
        上下文字典
    """
    return {
        'title': anime_title,
        'titleBase': normalize_title(anime_title),  # 标准化标题（去除季度信息）
        'season': season,
        'episode': episode_index,
        'year': year,
        'provider': provider,
        'animeId': anime_id,
        'episodeId': episode_id,
        'sourceId': source_id
    }


# 预定义的路径模板示例（.xml后缀会自动添加）
TEMPLATE_EXAMPLES = {
    'default': '/app/config/danmaku/${animeId}/${episodeId}',
    'organized_by_title': 'downloads/弹幕/${title}/${title} - S${season:02d}E${episode:02d}',
    'qbittorrent_style': 'downloads/QB下载/动漫/${title}/Season ${season}/${title} - S${season:02d}E${episode:02d}',
    'plex_style': 'media/动漫/${title} (${year})/Season ${season:02d}/${title} - S${season:02d}E${episode:02d}',
    'emby_style': 'media/动漫/${title}/${title} S${season:02d}/${title} S${season:02d}E${episode:02d}',
    'simple': '弹幕/${title}/${episode:03d}',
    # Windows绝对路径示例
    'windows_absolute': 'D:/弹幕/${title}/${title} - S${season:02d}E${episode:02d}',
    'windows_downloads': 'C:/Users/${username}/Downloads/弹幕/${title}/${episode:03d}'
}


# ==================== 路径生成函数 ====================

async def generate_danmaku_path(episode, config_manager=None) -> tuple[str, Path]:
    """
    生成弹幕文件的完整路径

    Args:
        episode: Episode 对象
        config_manager: ConfigManager 实例

    Returns:
        tuple: (web_path, absolute_path)
    """
    anime_id = episode.source.anime.id
    episode_id = episode.id
    anime_type = episode.source.anime.type  # 获取类型: tv_series, movie, ova, other

    # 检查是否启用自定义路径
    custom_path_enabled = False

    if config_manager:
        try:
            custom_path_enabled_str = await config_manager.get('customDanmakuPathEnabled', 'false')
            custom_path_enabled = custom_path_enabled_str.lower() == 'true'
        except Exception as e:
            logger.warning(f"获取自定义路径配置失败: {e}")

    if custom_path_enabled and config_manager:
        try:
            # 根据类型选择不同的配置
            # movie 类型使用电影配置,其他类型使用电视配置
            if anime_type == 'movie':
                root_directory = await config_manager.get('movieDanmakuDirectoryPath', '/app/config/danmaku/movies')
                filename_template = await config_manager.get('movieDanmakuFilenameTemplate', '${title}/${episodeId}')
            else:
                root_directory = await config_manager.get('tvDanmakuDirectoryPath', '/app/config/danmaku/tv')
                filename_template = await config_manager.get('tvDanmakuFilenameTemplate', '${animeId}/${episodeId}')

            # 创建路径模板上下文
            # 注意：season 可能为 0（特别篇/SP），不能使用 or 1 来设置默认值
            season_value = episode.source.anime.season if episode.source.anime.season is not None else 1
            context = create_danmaku_context(
                anime_title=episode.source.anime.title,
                season=season_value,
                episode_index=episode.episodeIndex,
                year=episode.source.anime.year,
                provider=episode.source.providerName,
                anime_id=anime_id,
                episode_id=episode_id,
                source_id=episode.source.id
            )

            # 生成相对路径(不包含.xml后缀)
            path_template = DanmakuPathTemplate(filename_template)
            relative_path_obj = path_template.generate_path(context)
            relative_path = str(relative_path_obj)

            # 移除自动添加的.xml后缀(因为我们要手动控制)
            if relative_path.endswith('.xml'):
                relative_path = relative_path[:-4]

            # 手动拼接.xml后缀
            relative_path += '.xml'

            # 拼接完整路径: 根目录 + 相对路径
            # 确保路径分隔符正确
            root_directory = root_directory.rstrip('/').rstrip('\\')
            relative_path = relative_path.lstrip('/').lstrip('\\')

            full_path = f"{root_directory}/{relative_path}"

            # 规范化路径
            full_path = str(Path(full_path))

            web_path = full_path
            absolute_path = Path(full_path)

            logger.info(f"使用自定义路径模板生成弹幕路径: {absolute_path}")
            return web_path, absolute_path

        except Exception as e:
            logger.error(f"使用自定义路径模板失败: {e}，回退到默认路径")

    # 默认路径逻辑 - 根据运行环境自动调整
    def _is_docker_environment():
        """检测是否在Docker容器中运行"""
        import os
        # 方法1: 检查 /.dockerenv 文件（Docker标准做法）
        if Path("/.dockerenv").exists():
            return True
        # 方法2: 检查环境变量
        if os.getenv("DOCKER_CONTAINER") == "true" or os.getenv("IN_DOCKER") == "true":
            return True
        # 方法3: 检查当前工作目录是否为 /app
        if Path.cwd() == Path("/app"):
            return True
        return False

    if _is_docker_environment():
        # Docker容器环境
        web_path = f"/app/config/danmaku/{anime_id}/{episode_id}.xml"
        absolute_path = Path(f"/app/config/danmaku/{anime_id}/{episode_id}.xml")
    else:
        # 源码运行环境 - 使用相对路径
        web_path = f"/app/config/danmaku/{anime_id}/{episode_id}.xml"
        absolute_path = Path(f"config/danmaku/{anime_id}/{episode_id}.xml")

    return web_path, absolute_path

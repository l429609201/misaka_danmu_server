"""
统一文件名解析模块

整合项目中散落的文件名识别、标题清理、季集提取等辅助函数，
提供统一的接口和更全面的正则模式。

参考: https://github.com/pipi20xx/anime-matcher 的正则模式设计
"""

import re
import logging
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ============================================================================
# 数据结构
# ============================================================================

@dataclass
class ParseResult:
    """文件名解析结果"""
    title: str = ""
    season: Optional[int] = None
    episode: Optional[int] = None
    is_movie: bool = False
    year: Optional[str] = None
    resolution: Optional[str] = None
    video_codec: Optional[str] = None
    audio_codec: Optional[str] = None
    source: Optional[str] = None
    team: Optional[str] = None
    dynamic_range: Optional[str] = None
    platform: Optional[str] = None
    effect: Optional[str] = None
    original_title: Optional[str] = None
    en_name: Optional[str] = None
    raw_input: Optional[str] = None  
    forced: Dict[str, str] = field(default_factory=dict)


# ============================================================================
# 常量 — 数字映射
# ============================================================================

CHINESE_NUM_MAP = {
    '一': 1, '二': 2, '三': 3, '四': 4, '五': 5,
    '六': 6, '七': 7, '八': 8, '九': 9, '十': 10,
}

ROMAN_NUM_MAP = {
    'i': 1, 'ii': 2, 'iii': 3, 'iv': 4, 'v': 5,
    'vi': 6, 'vii': 7, 'viii': 8, 'ix': 9, 'x': 10,
    # 全角罗马数字
    'ⅰ': 1, 'ⅱ': 2, 'ⅲ': 3, 'ⅳ': 4, 'ⅴ': 5,
    'ⅵ': 6, 'ⅶ': 7, 'ⅷ': 8, 'ⅸ': 9, 'ⅹ': 10,
}

FULLWIDTH_ROMAN_MAP = {
    'Ⅰ': 1, 'Ⅱ': 2, 'Ⅲ': 3, 'Ⅳ': 4, 'Ⅴ': 5,
    'Ⅵ': 6, 'Ⅶ': 7, 'Ⅷ': 8, 'Ⅸ': 9, 'Ⅹ': 10,
    'Ⅺ': 11, 'Ⅻ': 12,
}

# 电影关键词 (用于 is_movie_by_title)
MOVIE_KEYWORDS = ["剧场版", "劇場版", "movie", "映画"]


# ============================================================================
# 常量 — 正则模式 (同步 anime-matcher 最新版，使用词边界断言)
# ============================================================================

VIDEO_EXTENSIONS = {
    'mkv', 'mp4', 'avi', 'wmv', 'flv', 'ts', 'm2ts', 'rmvb', 'rm',
    'mov', 'webm', 'mpg', 'mpeg', 'vob', 'iso', 'bdmv', 'ogm',
}

# 分辨率 (同步 anime-matcher PIX_RE，使用词边界)
PIX_RE = re.compile(
    r'(?<![a-zA-Z0-9])'
    r'((\d{3,4}[Pp])|([248][Kk])|(\d{3,4}[xX]\d{3,4}))'
    r'(?![a-zA-Z0-9])',
    re.IGNORECASE
)

# 视频编码 (同步 anime-matcher VIDEO_RE，新增 VC/MPEG/Xvid/DivX)
VIDEO_RE = re.compile(
    r'(?<![a-zA-Z0-9])'
    r'(H\.?26[45]|[Xx]26[45]|AVC|HEVC|VC[0-9]?|MPEG[0-9]?|Xvid|DivX|AV1|VP9|10bit|8bit)'
    r'(?![a-zA-Z0-9])',
    re.IGNORECASE
)

# 音频编码 (同步 anime-matcher AUDIO_RE，支持声道捕获组)
AUDIO_RE = re.compile(
    r'(?<![a-zA-Z0-9])'
    r'(DTS-?HD(?:\.MA|[-\s]MA)?|DTS(?:\.MA|[-\s]MA)?|Atmos|TrueHD|AC-?3'
    r'|DDP|DD\+|DD|AAC|FLAC|Vorbis|Opus|E-?AC-?3|LPCM|PCM|MP3)'
    r'(?:(?:(?:\s*|\.|_|-)(?=[0-9]))?([0-9]\.[0-9](?:\+[0-9]\.[0-9])?|[0-9]ch))?'
    r'(?![a-zA-Z0-9])',
    re.IGNORECASE
)

# 来源/介质 (同步 anime-matcher SOURCE_RE，新增 HDRip/UHDTV/Pdtv 等)
SOURCE_RE = re.compile(
    r'(?<![a-zA-Z0-9])'
    r'(WEB-DL|WEBRIP|WEB-RIP|BDRIP|DVDRIP|HDRip|BLURAY|UHDTV|HDTV|HDDVD'
    r'|REMUX|UHD|Pdtv|Dvdscr|BLU|WEB|BD|BDRemux|TVRip|DVD)'
    r'(?![a-zA-Z0-9])',
    re.IGNORECASE
)

# 动态范围 (新增，来自 anime-matcher DYNAMIC_RANGE_RE)
DYNAMIC_RANGE_RE = re.compile(
    r'(?<![a-zA-Z0-9])'
    r'(HDR10\+|HDR10|HDR|HLG|Dolby\s*Vision|DoVi|DV|SDR|IMAX)'
    r'(?![a-zA-Z0-9])',
    re.IGNORECASE
)

# 特效/版本标记 (新增，来自 anime-matcher EFFECT_RE)
EFFECT_RE = re.compile(
    r'(?<![a-zA-Z0-9])'
    r'(3D|REPACK|HQ|Remastered|Extended|Uncut|Internal|Pro|Proper)'
    r'(?![a-zA-Z0-9])',
    re.IGNORECASE
)

# 平台 (同步 anime-matcher PLATFORM_RE，新增 playWEB/ATVP/HIDIVE 等)
PLATFORM_RE = re.compile(
    r'(?:-)?(?<![a-zA-Z0-9])'
    r'(Baha|Bilibili|Netflix|NF|Amazon|AMZN|DSNP|Crunchyroll|CR|Hulu|HBO'
    r'|YouTube|YT|playWEB|B-Global|friDay|LINETV|KKTV|ATVP|IQ|IQIYI|CRAMZN'
    r'|iT|ABEMA|HIDIVE|Funimation|Sentai|VIU|MyVideo|CatchPlay|WeTV|Viki|ADN)'
    r'(?![a-zA-Z0-9])'
    r'|(?:-)?(?<![a-zA-Z0-9])(Disney\+|AppleTV\+)',
    re.IGNORECASE
)

# 字幕标签 (新增，来自 anime-matcher SUBTITLE_RE)
SUBTITLE_RE = re.compile(
    r'(?i)[\[\(\{（【][^\]\}）】]*?'
    r'(?:(?:[简繁日中英体文语語]{1,10}(?:内封|内嵌|外挂|双语|多语|样式|字幕))'
    r'|(?:CHS|CHT|GB|BIG5|JPSC|JP_SC|SRTx|ASSx))'
    r'[^\]\}）】]*?[\]\)\}）】]'
)

# 别名/检索词屏蔽 (新增，来自 anime-matcher ALIAS_RE)
ALIAS_RE = re.compile(
    r'(?i)[\[\(\{（【]\s*'
    r'(?:检索用|检索|檢索|别名|別名|又名|附带|附帶|翻译|翻译自)[:：\s]+.*?'
    r'[\]\)\}）】]'
)

# 深度噪音词 (同步 anime-matcher NOISE_WORDS，已移除内联 (?i) 标志，统一由调用方传 re.IGNORECASE)
NOISE_WORDS = [
    r"PTS|JADE|AOD|CHC|(?!LINETV)[A-Z]{1,4}TV[-0-9UVHDK]*",
    r"[0-9]{1,2}th|[0-9]{1,2}bit|IMAX|BBC|XXX|DC$",
    r"Ma10p|Hi10p|Hi10|Ma10|10bit|8bit",
    r"年龄限制版|年齡限制版|修正版|无修正|未删减|无修正版|無修正版",
    r"连载|新番|合集|招募翻译|版本|出品|台版|港版|搬运|搬運|[a-zA-Z0-9]+字幕组|[a-zA-Z0-9]+字幕社|[★☆]*[0-9]{1,2}月新番[★☆]*",
    r"UNCUT|UNRATE|WITH EXTRAS|RERIP|SUBBED|PROPER|REPACK|Complete|Extended|Version|10bit",
    r"\b(OVA|ONA|Special|SP|Specials|劇場版|剧场版|OAD|Extra)\b",
    r"\b[vV][0-9]{1,2}\b|\bver[0-9]{1,2}\b",
    r"CD[ ]*[1-9]|DVD[ ]*[1-9]|DISK[ ]*[1-9]|DISC[ ]*[1-9]|[ ]+GB",
    r"YYeTs|人人影视|弯弯字幕组",
    r"[简繁中日英双雙多]+[体文语語]+[ ]*(MP4|MKV|AVC|HEVC|AAC|ASS|SRT)*",
    r"繁体|繁體|简体|简体|简日|繁日|简中|繁中|简繁|双语|双语|内嵌|內嵌|内封|內封|外挂|外掛",
]

# 发布组排除词 (同步 anime-matcher NOT_GROUPS，已移除内联 (?i)，由调用方传 re.IGNORECASE)
NOT_GROUPS = (
    "1080P|720P|4K|2160P|H264|H265|X264|X265|AVC|HEVC|AAC|DTS|AC3|DDP|ATMOS"
    "|WEB-DL|WEBRIP|BLURAY|BD|HD|HDR|SDR|DV|TRUEHD|HIRES|10BIT|EAC3|UHD 4K"
    "|Ma10p|Hi10p|Hi10|Ma10|REMUX"
)

# 发布组语义特征词 (新增，来自 anime-matcher GROUP_KEYWORDS)
GROUP_KEYWORDS = re.compile(
    r'组|組|社|制作|製作|字幕|工作|家族|学园|學園|压制|壓制|发布|發佈'
    r'|协会|協會|联盟|聯盟|论坛|論壇|中心|屋|团|團|亭|园|園'
)

# 季集提取模式 (同步 anime-matcher，新增 DR 和序数词模式)
EPISODE_PATTERNS = [
    r"(?i)EP?([0-9]{2,4})",
    r"(?i)DR([0-9]{2,4})",
    r"第[ ]*([0-9]{1,4})[ ]*[集话話期幕]",
    r"\[([0-9]{1,4})\]",
    r"[ ]+-[ ]+([0-9]{1,4})",
]

SEASON_EXTRACT_PATTERNS = [
    r"(?i)\b([0-9]{1,2})(?:st|nd|rd|th)\b(?:\s*Season)?",
    r"(?i)(?<![a-zA-Z])S([0-9]{1,2})(?![a-zA-Z0-9])",
    r"第([一二三四五六七八九十0-9]+)季",
    r"Season[ ]*([0-9]+)",
]

# 统一元数据清理模式 (同步 anime-matcher，覆盖所有新增模式)
METADATA_PATTERN = re.compile(
    r'(?:'
    # 分辨率
    r'3840\s*[x×]\s*2160|2560\s*[x×]\s*1440|1920\s*[x×]\s*1080|1280\s*[x×]\s*720'
    r'|4320[pP]|2160[pP]|1080[pPiI]|720[pP]|576[pPiI]|480[pP]|8[kK]|4[kK]'
    # 视频编码
    r'|H\.?265|H\.?264|x\.?265|x\.?264|HEVC|AVC|AV1|VP9|VC[0-9]?|MPEG[0-9]?|Xvid|DivX|10bit|8bit'
    # 音频编码
    r'|DTS-?HD(?:\.MA)?|DTS(?:\.MA)?|Atmos|TrueHD|AC-?3|DDP|DD\+|DD'
    r'|AAC|FLAC|Vorbis|Opus|E-?AC-?3|LPCM|PCM|MP3'
    # 来源
    r'|WEB-?DL|WEBRip|WEB-RIP|BluRay|BDRip|BDRemux|Remux|HDTV|TVRip|DVDRip'
    r'|HDRip|UHDTV|HDDVD|Pdtv|Dvdscr|BLU|WEB|BD|UHD|DVD'
    # 动态范围
    r'|HDR10\+|HDR10|HDR|HLG|Dolby\s*Vision|DoVi|DV|SDR|IMAX'
    # 特效/版本
    r'|REPACK|Remastered|Extended|Uncut|Internal|Proper'
    # 字幕/语言标记
    r'|CHT|CHS|BIG5|GB|ENG|JPN|TC|SC|JP|繁体|简体|简日|繁日'
    # 平台
    r'|Baha|Bilibili|Netflix|NF|Amazon|AMZN|DSNP|Crunchyroll|CR|Hulu|HBO'
    r'|B-Global|friDay|LINETV|KKTV|ATVP|ABEMA|HIDIVE|Funimation'
    r')',
    re.IGNORECASE
)

# 季度标准化模式 (用于 normalize_title)
SEASON_SUFFIX_PATTERNS = [
    # 中文季度表达（支持简繁中文数字）
    r'\s*第[一二三四五六七八九十壹贰叁肆伍陆柒捌玖拾\d]+季.*$',
    r'\s*第[一二三四五六七八九十壹贰叁肆伍陆柒捌玖拾\d]+期.*$',
    r'\s*第[一二三四五六七八九十壹贰叁肆伍陆柒捌玖拾\d]+部.*$',
    r'\s*第[一二三四五六七八九十壹贰叁肆伍陆柒捌玖拾\d]+章.*$',
    r'\s*第[一二三四五六七八九十壹贰叁肆伍陆柒捌玖拾\d]+篇.*$',
    r'\s*第[一二三四五六七八九十壹贰叁肆伍陆柒捌玖拾\d]+幕.*$',
    # X之章 格式
    r'\s*[一二三四五六七八九十壹贰叁肆伍陆柒捌玖拾]\s*之\s*章.*$',
    # 英文季度表达
    r'\s*Season\s*\d+.*$',
    r'\s*S\d+.*$',
    r'\s*Part\s*\d+.*$',
    # 特殊篇章名
    r'\s*[：:]\s*\S+篇\s*$',
    r'\s*\S+篇\s*$',
    # Unicode罗马数字
    r'\s+[Ⅰ-Ⅻ]+\s*$',
    # ASCII罗马数字
    r'\s+[IVX]+\s*$',
]


# ============================================================================
# 辅助函数
# ============================================================================

def _roman_to_int(s: str) -> int:
    """将罗马数字字符串转换为整数"""
    roman_map = {'I': 1, 'V': 5, 'X': 10, 'L': 50, 'C': 100, 'D': 500, 'M': 1000}
    s = s.upper()
    result = 0
    i = 0
    while i < len(s):
        if i + 1 < len(s) and roman_map.get(s[i], 0) < roman_map.get(s[i + 1], 0):
            result += roman_map[s[i + 1]] - roman_map[s[i]]
            i += 2
        else:
            result += roman_map.get(s[i], 0)
            i += 1
    return result


def _chinese_num_to_int(s: str) -> Optional[int]:
    """将中文数字转换为整数，支持阿拉伯数字直通和组合中文数字

    支持范围：一~九十九（含十一~十九、二十~二十九等组合形式）
    示例：十一→11, 二十→20, 三十五→35, 九十九→99
    """
    if s.isdigit():
        return int(s)
    # 单字快速查找
    if len(s) == 1:
        return CHINESE_NUM_MAP.get(s)
    # 组合中文数字解析（十一~九十九）
    if '十' in s:
        parts = s.split('十', 1)
        tens_part = parts[0]  # 十前面的数字（空则为1）
        ones_part = parts[1]  # 十后面的数字（空则为0）
        tens = CHINESE_NUM_MAP.get(tens_part, 1) if tens_part else 1
        ones = CHINESE_NUM_MAP.get(ones_part, 0) if ones_part else 0
        if isinstance(tens, int) and isinstance(ones, int):
            return tens * 10 + ones
    return None


# 通用强制元数据块：{key=value} 或 {key=value;key2=value2}
# 键名统一小写、去下划线/连字符后再匹配语义（tmdb_id / TMDBID / tmdbid 等价）
FORCED_META_BLOCK_RE = re.compile(r'\{([^{}]*?=[^{}]*?)\}')

# 语义键归一映射：把各种写法归一到标准键名
_FORCED_KEY_ALIASES = {
    'tmdbid': 'tmdbid', 'tmdb': 'tmdbid',
    'tvdbid': 'tvdbid', 'tvdb': 'tvdbid',
    'imdbid': 'imdbid', 'imdb': 'imdbid',
    'bangumiid': 'bangumiid', 'bangumi': 'bangumiid', 'bgmid': 'bangumiid', 'bgm': 'bangumiid',
    'doubanid': 'doubanid', 'douban': 'doubanid',
    'season': 's', 's': 's',
    'episode': 'e', 'ep': 'e', 'e': 'e',
    'type': 'type', 'mediatype': 'type',
    'year': 'year',
    'title': 'title', 'name': 'title',
}


def extract_forced_metadata(name: str) -> Tuple[str, Dict[str, str]]:
    """
    从文件名中提取并剥离通用强制元数据块 {key=value}。

    借鉴 pipi/Anime-Manager 的 forced 机制，但做成**完全通用**的键值对提取：
    不限定于 TMDBID，任意 {key=value} 都会被收集。

    支持格式：
    - 单键：'某剧 {tmdbid=1422} S01E03'
    - 多键（分号或逗号分隔）：'某剧 {tmdbid=1422;s=2} '
    - 多块：'某剧 {tmdbid=1422} {type=tv}'
    - 键名容错：{TMDB_ID=1422} / {tmdbId = 1422} 均可

    :param name: 原始文件名
    :return: (剥离强制块后的文件名, 强制元数据字典)
             字典键已归一（tmdb/tmdb_id/TMDBID → tmdbid；season → s；episode → e），
             未识别的键保留其小写原名，值统一为去空白的字符串。
    """
    if not name or '{' not in name:
        return name, {}

    forced: Dict[str, str] = {}

    def _collect(match: "re.Match") -> str:
        body = match.group(1)
        # 支持一个块内多个键值：分号/逗号分隔
        for pair in re.split(r'[;,]', body):
            if '=' not in pair:
                continue
            raw_key, _, raw_val = pair.partition('=')
            # 键名归一：小写 + 去掉下划线/连字符/空格
            key = re.sub(r'[\s_\-]', '', raw_key).strip().lower()
            val = raw_val.strip()
            if not key or not val:
                continue
            forced[_FORCED_KEY_ALIASES.get(key, key)] = val
        # 剥离该块（替换为空格，避免标题粘连）
        return ' '

    stripped = FORCED_META_BLOCK_RE.sub(_collect, name)
    stripped = re.sub(r'\s+', ' ', stripped).strip()
    return stripped, forced


def _apply_forced_metadata(result: ParseResult) -> ParseResult:
    """
    将 result.forced 中的已知语义键应用为**最高优先级**覆盖。

    强制元数据代表用户的显式意图，优先级高于任何正则解析结果。
    未知键不做处理，原样保留在 result.forced 中供下游消费。
    """
    if result is None or not result.forced:
        return result

    f = result.forced

    # 季度 / 集数：显式指定则直接覆盖，并意味着这是剧集
    if 's' in f:
        try:
            result.season = int(f['s'])
            result.is_movie = False
        except (ValueError, TypeError):
            logger.debug(f"[强制元数据] 季度值无法解析为整数: {f['s']!r}")
    if 'e' in f:
        try:
            result.episode = int(f['e'])
            result.is_movie = False
        except (ValueError, TypeError):
            logger.debug(f"[强制元数据] 集数值无法解析为整数: {f['e']!r}")

    # 媒体类型：tv/series → 剧集；movie/film → 电影
    if 'type' in f:
        t = f['type'].strip().lower()
        if t in ('tv', 'tv_series', 'series', 'show', 'anime'):
            result.is_movie = False
            if result.season is None:
                result.season = 1
        elif t in ('movie', 'film', 'movies'):
            result.is_movie = True
            result.season = None
            result.episode = None

    # 年份
    if 'year' in f and re.fullmatch(r'(?:19|20)\d{2}', f['year'].strip()):
        result.year = f['year'].strip()

    # 标题：显式指定标题直接覆盖（用于自动识别不准时人工兜底）
    if 'title' in f and f['title'].strip():
        result.title = f['title'].strip()

    return result


def _preprocess_name(name: str) -> str:
    """
    【阶段A】命名预处理：把国内资源站的花式命名归一，降低后续正则复杂度。

    借鉴 MoviePilot 的 __prepare_title，处理项：
    1. NFKC 折叠全角字符 → 半角（（2025）→ (2025)、Ｓ０１ → S01），
       这样全角输入也能被后续 SxxExx 等正则命中；
    2. 中文方头括号统一为半角方括号（【】→ []）；
    3. 去除首部分类/搬运前缀（如 [新番][10月番][国漫][合集]）；
    4. 去除体积标记（1.5GB / 700MB），避免被当成集数或年份；
    5. 4K → 2160p，统一分辨率写法便于阶段B剥离。

    :param name: 已去扩展名的原始文件名
    :return: 归一化后的文件名
    """
    if not name:
        return name

    # 1. 全角 → 半角（NFKC 会把（）０１等折叠为 ()01，同时保留中日文字）
    name = unicodedata.normalize('NFKC', name)

    # 2. 【】→ []，便于统一按方括号处理
    name = name.replace('【', '[').replace('】', ']')

    # 3. 去除首部分类/搬运前缀括号块（仅当括号内含分类关键词时才剥，避免误删剧名）
    #    最多剥两次，应对 [新番][10月番] 这种连续前缀
    for _ in range(2):
        stripped = re.sub(
            r'^\s*\[[^\]]*?(?:新番|\d{1,2}月番|月番|日[漫剧]|国[漫剧]|美[剧漫]|韩剧|'
            r'搬运|搬運|合集|连载|連載|全集|完结|完結)[^\]]*?\]\s*',
            '', name
        )
        if stripped == name:
            break
        name = stripped

    # 4. 去除体积标记（1.5GB / 700 MB），前后需非字母数字避免误伤编码词
    name = re.sub(r'(?<![A-Za-z0-9])\d+(?:\.\d+)?\s*[MGT]i?B(?![A-Za-z0-9])', ' ', name, flags=re.IGNORECASE)

    # 5. 4K → 2160p（统一写法，交给阶段B的 PIX_RE 剥离）
    name = re.sub(r'(?<![A-Za-z0-9])4K(?![A-Za-z0-9])', '2160p', name, flags=re.IGNORECASE)

    return re.sub(r'\s+', ' ', name).strip()


def normalize_title_key(title: str) -> str:
    """
    【阶段E】生成标题归一化"指纹"，仅用于匹配/去重比对，不用于展示。

    借鉴 huangxd-/danmu_api 的 normalizeRuleTitle：
    NFKC 折叠全半角 → 去掉所有非字母数字字符（空格/标点/分隔符全去）→ 转小写。

    效果：'凡人修仙传'、'凡人修仙传 '、'凡人·修仙传'、'Fanren Xiu Xian Zhuan' 等
    在跨源去重与候选比对时能稳定命中同一 key，吃掉全半角、标点、空格、大小写差异。

    :param title: 原始标题
    :return: 归一化指纹（可能为空串，调用方需自行判空）
    """
    if not title:
        return ""
    s = unicodedata.normalize('NFKC', str(title))
    # 仅保留：数字、拉丁字母、CJK 统一表意文字、日文平/片假名、韩文
    s = re.sub(
        r'[^0-9A-Za-z\u4e00-\u9fff\u3400-\u4dbf\u3040-\u309f\u30a0-\u30ff\uac00-\ud7a3]',
        '', s
    )
    return s.lower()


def _is_garbage_title(title: str) -> bool:
    """
    【阶段D辅助】判断标题是否为"技术垃圾"（不可信标题）。

    典型垃圾：纯数字(03)、纯技术词(OVA/BD/1080p)、纯语言标记(CHS/BIG5)、长度过短。
    借鉴 pipi PostProcessor 的 invalid_keywords 判定。
    """
    if not title:
        return True
    t = title.strip()
    # 去掉所有非字母数字后长度不足 2，视为无效（如 "-"、"[]"、"03"）
    core = re.sub(r'[^0-9A-Za-z\u4e00-\u9fff\u3040-\u30ff]', '', t)
    if len(core) < 2:
        return True
    # 纯数字（集号残留）
    if core.isdigit():
        return True
    # 纯技术词/类型词/语言标记
    garbage_words = {
        'OVA', 'ONA', 'OAD', 'SP', 'SPECIAL', 'SPECIALS', 'TV', 'BD', 'DVD',
        'MOVIE', 'MP4', 'MKV', 'AVI', 'WEB', 'WEBDL', 'CHS', 'CHT', 'GB',
        'BIG5', 'JP', 'JPSC', 'ENG', 'JAP', 'RAW', 'FIN', 'END', 'COMPLETE',
    }
    if core.upper() in garbage_words:
        return True
    # 纯分辨率（1080p / 2160P / 720x480）
    if re.fullmatch(r'\d{3,4}[pPiI]?', core) or re.fullmatch(r'\d{3,4}[xX]\d{3,4}', core):
        return True
    # 序数词残留（2nd / 3rd）
    if re.fullmatch(r'\d+(?:st|nd|rd|th)', core, re.IGNORECASE):
        return True
    return False


def _rescue_title_from_brackets(raw: str) -> Optional[str]:
    """
    【阶段D辅助】从原始文件名的括号块中"回捞"可信标题。

    借鉴 pipi PostProcessor STEP5 的深度回捞：当主流程抠出的标题不可信时，
    遍历所有 [xxx]/【xxx】 块，排除技术词/校验码/集数块，优先返回含中文的候选。
    """
    if not raw:
        return None
    blocks = re.findall(r'[\[\【]([^\]\】]+)[\]\】]', raw)
    cjk_candidates: List[str] = []
    other_candidates: List[str] = []
    for b in blocks:
        b = b.strip()
        if not b or len(b) < 2:
            continue
        # 排除技术规格块
        if re.search(r'\d{3,4}[pPiI]\b|H\.?26[45]|x26[45]|AVC|HEVC|AAC|FLAC|'
                     r'WEB-?DL|BDRip|CHS|CHT|BIG5|MP4|MKV|新番|\d{1,2}月番',
                     b, re.IGNORECASE):
            continue
        # 排除 8 位十六进制校验码（如 FEA67121）
        if re.fullmatch(r'[0-9A-Fa-f]{8}', b):
            continue
        # 排除纯集数/集数区间块（03 / 01-12 / 第03话）
        if re.fullmatch(r'(?:第\s*)?\d{1,4}(?:\s*[-~]\s*\d{1,4})?(?:\s*[集话話回期])?', b):
            continue
        # 排除明显的技术垃圾词
        if _is_garbage_title(b):
            continue
        if _has_cjk(b):
            cjk_candidates.append(b)
        else:
            other_candidates.append(b)
    # 中文标题优先（国内场景剧名多为中文）
    if cjk_candidates:
        return cjk_candidates[0]
    if other_candidates:
        return other_candidates[0]
    return None


def _post_correct(result: ParseResult, raw: str) -> ParseResult:
    """
    【阶段D】后处理纠偏：对解析结果做一遍"体检"，修正明显错误。

    借鉴 pipi PostProcessor 的 STEP4（属性对撞）与 STEP7（类型判定），
    是杜绝"明明有 S01/集数却被判成电影"这类错误的最后防线。

    纠偏规则（按优先级）：
    1. 年份被误当作集数（episode>1900 且无季号）→ 清空集数并判为电影；
    2. 【硬规则】只要有 season 或 episode → 一律 TV，禁止 is_movie；有集无季补 season=1；
    3. 剧场版特征词优先级最高 → 强制电影并清空季集；
    4. 标题为技术垃圾 → 从原始文件名括号块回捞标题。

    :param result: 待纠偏的解析结果
    :param raw: 原始（已预处理）文件名，用于关键词判定与标题回捞
    :return: 纠偏后的解析结果（原地修改并返回）
    """
    if result is None:
        return result

    # 规则1：年份误判为集数（如 "某剧 2019" 被当成第2019集）
    if result.episode is not None and result.episode > 1900 and result.season is None:
        logger.debug(f"[纠偏] 集数 {result.episode} 疑似年份，清空集数并判为电影")
        result.episode = None
        result.is_movie = True

    # 规则2【硬规则】：有季号或集号 → 必为剧集，绝不判电影
    if result.season is not None or result.episode is not None:
        if result.is_movie:
            logger.debug(f"[纠偏] 存在季/集(S{result.season}/E{result.episode})，撤销电影判定")
        result.is_movie = False
        if result.season is None:
            # 有集无季：默认第 1 季，避免下游季度过滤取不到值
            result.season = 1

    # 规则3：剧场版/movie 特征词优先级最高（真电影不该有季集）
    if is_movie_by_title(raw):
        if not result.is_movie:
            logger.debug(f"[纠偏] 命中剧场版特征词，强制判为电影并清空季集")
        result.is_movie = True
        result.season = None
        result.episode = None

    # 规则4：标题不可信 → 从括号块回捞
    if _is_garbage_title(result.title):
        rescued = _rescue_title_from_brackets(raw)
        if rescued:
            logger.debug(f"[纠偏] 标题 '{result.title}' 不可信，回捞为 '{rescued}'")
            result.title = rescued

    # 规则5【最高优先级】：应用文件名内嵌的强制元数据 {key=value}
    # 用户显式指定的意图优先于一切正则解析与纠偏结果，故放在最后覆盖
    result = _apply_forced_metadata(result)

    return result


def _strip_video_extension(filename: str) -> str:
    """移除视频文件扩展名"""
    if '.' in filename:
        parts = filename.rsplit('.', 1)
        if len(parts) == 2 and parts[1].lower() in VIDEO_EXTENSIONS:
            return parts[0]
    return filename


def _clean_brackets_and_metadata(title: str) -> str:
    """移除方括号、圆括号内容及元数据关键词"""
    title = re.sub(r'\[.*?\]|\(.*?\)|【.*?】|\（.*?\）', '', title)
    title = METADATA_PATTERN.sub('', title)
    return title.strip()


def _clean_year_from_title(title: str) -> str:
    """移除标题中的年份"""
    title = re.sub(r'\(\s*(19|20)\d{2}\s*\)', '', title)
    title = re.sub(r'（\s*(19|20)\d{2}\s*）', '', title)
    title = re.sub(r'\b(19|20)\d{2}\b', '', title)
    return title.strip()


def _normalize_separators(title: str) -> str:
    """将点号和下划线替换为空格，并清理多余空格"""
    title = title.replace('.', ' ').replace('_', ' ')
    title = re.sub(r'\s+', ' ', title)
    return title.strip(' -')


def _has_cjk(text: str) -> bool:
    """检查文本是否包含 CJK 字符（中日韩统一表意文字、平假名、片假名）"""
    return bool(re.search(r'[\u4e00-\u9fff\u3400-\u4dbf\u3040-\u309f\u30a0-\u30ff\uf900-\ufaff]', text))


def _is_latin_word(word: str) -> bool:
    """检查一个词是否为纯 Latin 字母组成"""
    return bool(re.match(r'^[a-zA-Z][a-zA-Z\'-]*$', word))


def _split_multilang_title(title: str) -> Tuple[str, Optional[str]]:
    """
    多语种标题拆分：当标题同时包含 CJK 和 Latin 文字时，拆分为 CJK 和 Latin 两部分。
    参考 Lens 项目 STEP 5 "标题残差剥离与拆分" 的逻辑。

    返回: (cjk_title, en_name)
    - 发生拆分时: cjk_title 为 CJK 部分, en_name 为 Latin 部分
    - 未拆分时: cjk_title 为原始标题, en_name 为 None

    规则:
    - CJK 在前 + 2个以上 Latin 词在后 → 拆分
    - Latin 在前(≥2词) + CJK 在后 → 拆分
    - 单个 Latin 词 + CJK（如 "BLEACH 死神"）→ 不拆分
    - 纯 CJK 或纯 Latin → 不拆分
    """
    if not title or ' ' not in title:
        return title, None

    # 快速检查：必须同时包含 CJK 和 Latin
    if not _has_cjk(title) or not re.search(r'[a-zA-Z]{2,}', title):
        return title, None

    words = title.split()

    # 对每个词分类: True=CJK, False=Latin, None=其他(数字等)
    def classify(w):
        if _has_cjk(w):
            return True
        if _is_latin_word(w):
            return False
        return None

    tags = [classify(w) for w in words]

    # 找第一个 CJK 词和第一个 Latin 词的位置
    first_cjk = next((i for i, t in enumerate(tags) if t is True), None)
    first_latin = next((i for i, t in enumerate(tags) if t is False), None)

    if first_cjk is None or first_latin is None:
        return title, None

    # 情况1: CJK 在前，Latin 在后
    if first_cjk < first_latin:
        last_cjk_before_latin = first_cjk
        for i in range(first_cjk, len(tags)):
            if tags[i] is True:
                last_cjk_before_latin = i
            elif tags[i] is False:
                break
        latin_count = sum(1 for t in tags[last_cjk_before_latin + 1:] if t is False)
        if latin_count >= 2:
            cjk_part = ' '.join(words[:last_cjk_before_latin + 1]).strip()
            en_part = ' '.join(words[last_cjk_before_latin + 1:]).strip()
            return cjk_part, en_part or None

    # 情况2: Latin 在前，CJK 在后
    elif first_latin < first_cjk:
        latin_count = sum(1 for t in tags[:first_cjk] if t is False)
        if latin_count >= 2:
            en_part = ' '.join(words[:first_cjk]).strip()
            cjk_part = ' '.join(words[first_cjk:]).strip()
            return cjk_part, en_part or None

    return title, None


def _strip_all_metadata(name: str) -> str:
    """
    从文件名中剥离所有已知的元数据标签（分辨率、编码、来源、HDR、平台、特效等），
    参考上游 anime-matcher 的 "噪声屏蔽" 策略：先剥离元数据，再提取标题。
    """
    temp = name

    # 0. 预处理：拆分常见连写元数据 (如 WEB-DLHDR → WEB-DL.HDR)
    temp = re.sub(r'(?i)(WEB-DL)(HDR)', r'\1.\2', temp)
    temp = re.sub(r'(?i)(HEVC|AVC|H\.?265|H\.?264|x\.?265|x\.?264)(HDR)', r'\1.\2', temp)

    # 1. 剥离字幕标签括号块和别名括号块
    temp = SUBTITLE_RE.sub(' ', temp)
    temp = ALIAS_RE.sub(' ', temp)

    # 2. 剥离技术规格（按优先级顺序，长模式优先）
    for pattern in [PIX_RE, VIDEO_RE, AUDIO_RE, SOURCE_RE,
                    DYNAMIC_RANGE_RE, EFFECT_RE, PLATFORM_RE]:
        temp = pattern.sub(' ', temp)

    # 3. 剥离所有方括号/圆括号内容 (元数据值已在阶段1提取，此处可安全移除)
    temp = re.sub(r'\[.*?\]|\(.*?\)|【.*?】|（.*?）', ' ', temp)

    # 4. 剥离噪音词 (NOISE_WORDS 不含内联 (?i)，统一传 re.IGNORECASE)
    for nw in NOISE_WORDS:
        temp = re.sub(nw, ' ', temp, flags=re.IGNORECASE)

    # 5. 剥离声道信息残留 (如 5.1, 7.1)
    temp = re.sub(r'(?<![a-zA-Z0-9])([0-9]\.[0-9])(?:ch)?(?![a-zA-Z0-9])', ' ', temp)

    # 6. 剥离尾部发布组标签 (如 -PTerWEB, -ADE, @ADWeb)
    temp = re.sub(r'[-@][A-Za-z][A-Za-z0-9]{1,15}$', ' ', temp)

    # 7. 清理空壳括号和孤儿括号
    for _ in range(3):
        temp = re.sub(r'[\[\(\{（【][\s\-\._/&+\*]*[\]\)\}）】]', ' ', temp)

    # 8. 清理装饰性符号
    temp = re.sub(r'[★☆■□◆◇●○•]', ' ', temp)

    # 9. 压缩连续分隔符和空格
    temp = re.sub(r'[\s\-\._/]{3,}', ' ', temp)
    temp = re.sub(r'\s+', ' ', temp).strip(' -._')

    return temp


def _extract_tail_group(name: str) -> Optional[str]:
    """
    提取尾部发布组标签 (如 -PTerWEB, -ADE)。
    参考上游 anime-matcher TagExtractor.extract_release_group 的尾部逻辑。
    """
    base = re.sub(r'\.[a-zA-Z0-9]+$', '', name)
    m = re.search(r'-([A-Za-z][A-Za-z0-9]{1,15})$', base)
    if m:
        candidate = m.group(1)
        # 排除已知的技术词
        if re.match(rf'^({NOT_GROUPS})$', candidate, re.IGNORECASE):
            return None
        return candidate
    return None


# ============================================================================
# 核心函数 1: parse_filename — 文件名完整解析
# ============================================================================

def parse_filename(filename: str) -> Optional[ParseResult]:
    """
    从文件名中解析出标题、季集、元数据等信息。
    替代 parse_filename_for_match()。

    采用五阶段流水线（详见 docs/识别核心对比分析.md 第六章）：
    - 阶段0 强制元数据：提取并剥离通用 {key=value} 块（extract_forced_metadata）
    - 阶段A 预处理：全半角归一、【】→[]、去分类前缀/体积标记（_preprocess_name）
    - 阶段B 元数据剥离：提取并剥离分辨率/编码/来源/平台等（_strip_all_metadata）
    - 阶段C 季集提取：多梯队容错正则（SxxExx容错 → S+分隔 → 中文集数 → 裸集号 → 纯季号）
    - 阶段D 后处理纠偏：有季集必判TV、年份误判修正、标题回捞（_post_correct）
      并在纠偏后应用强制元数据（最高优先级覆盖）
    - 阶段E 归一化指纹：normalize_title_key 供下游统一过滤使用（独立函数，不在此调用）
    """
    name = _strip_video_extension(filename)

    # ── 阶段0: 提取并剥离通用强制元数据块 {key=value} ──
    # 必须最先执行：{} 内容会干扰后续所有正则（如 {tmdbid=1422} 里的数字被当成集数）
    name, forced_meta = extract_forced_metadata(name)
    if forced_meta:
        logger.debug(f"[强制元数据] 提取到: {forced_meta}")

    # ── 阶段A: 命名预处理（全半角归一、去分类前缀/体积标记、4K→2160p）──
    name = _preprocess_name(name)

    # ── 阶段1: 从原始文件名提取元数据值 ──
    # 预处理：拆分常见连写元数据 (如 WEB-DLHDR → WEB-DL.HDR) 以便正确提取
    name_for_meta = re.sub(r'(?i)(WEB-DL)(HDR)', r'\1.\2', name)
    name_for_meta = re.sub(r'(?i)(HEVC|AVC|H\.?265|H\.?264|x\.?265|x\.?264)(HDR)', r'\1.\2', name_for_meta)

    resolution = PIX_RE.search(name_for_meta)
    video_codec = VIDEO_RE.search(name_for_meta)
    audio_codec = AUDIO_RE.search(name_for_meta)
    source = SOURCE_RE.search(name_for_meta)
    dynamic_range = DYNAMIC_RANGE_RE.search(name_for_meta)
    platform = PLATFORM_RE.search(name_for_meta)
    effect = EFFECT_RE.search(name_for_meta)

    # 提取字幕组 (首部方括号)
    team_match = re.match(r'^\[([^\]]+)\]', name)
    team = team_match.group(1) if team_match else None

    # 新增：处理 ★ 分隔的文件名格式
    # ★ 充当 [] 的分隔作用：字幕组★标题★集数★分辨率★编码★语言
    if not team and '★' in name:
        star_segments = [s.strip() for s in name.split('★') if s.strip()]
        if len(star_segments) >= 3:
            # 检查首段是否为字幕组
            if GROUP_KEYWORDS.search(star_segments[0]):
                team = star_segments[0]
                star_segments = star_segments[1:]
                logger.debug(f"从 ★ 分隔符提取字幕组: '{team}'")

            # 智能分段：识别标题段、集数段，丢弃技术规格段
            title_parts = []
            episode_from_star = None
            for seg in star_segments:
                # 纯数字段 → 可能是集数（取第一个遇到的）
                if re.match(r'^\d{1,4}$', seg) and episode_from_star is None:
                    episode_from_star = int(seg)
                # 技术规格段（分辨率、编码、格式、语言等）→ 跳过
                elif PIX_RE.search(seg) or VIDEO_RE.search(seg) or AUDIO_RE.search(seg) \
                        or SOURCE_RE.search(seg) or DYNAMIC_RANGE_RE.search(seg) \
                        or PLATFORM_RE.search(seg) or EFFECT_RE.search(seg):
                    continue
                # 视频容器/格式（复用 VIDEO_EXTENSIONS 常量）→ 跳过
                elif seg.lower() in VIDEO_EXTENSIONS:
                    continue
                # 语言/字幕标记（复用 NOISE_WORDS 中的语言模式）→ 跳过
                elif re.match(r'(?i)^[简繁中日英双雙多]+[体文语語]', seg):
                    continue
                else:
                    title_parts.append(seg)

            if title_parts:
                # 用标题段重组 name（用空格连接）
                name = ' '.join(title_parts)
                # 如果有从 ★ 段提取的集数，附加到 name 末尾以便后续阶段3匹配
                if episode_from_star is not None:
                    name = f"{name} {episode_from_star}"
                logger.debug(f"★ 分段重组: name='{name}', episode_from_star={episode_from_star}")

    # 尝试提取尾部发布组 (如 -PTerWEB)
    if not team:
        team = _extract_tail_group(name)

    # 提取年份
    year_match = re.search(r'[\(\[（]?((?:19|20)\d{2})[\)\]）]?', name)
    year = year_match.group(1) if year_match else None

    # 构建元数据结果 (提前准备，避免重复代码)
    meta = dict(
        year=year,
        resolution=resolution.group(0) if resolution else None,
        video_codec=video_codec.group(0) if video_codec else None,
        audio_codec=audio_codec.group(0) if audio_codec else None,
        source=source.group(0) if source else None,
        team=team,
        dynamic_range=dynamic_range.group(1) if dynamic_range else None,
        platform=(platform.group(1) or platform.group(2)) if platform else None,
        effect=effect.group(1) if effect else None,
        # 阶段0 提取的通用强制元数据，随所有出口一并返回
        forced=forced_meta,
    )

    # ── 阶段C 梯队1: SxxExx（最可靠，容错版）──
    # 集号后允许可选的版本/脏后缀（v2 / .5 / 单个字母），并用「后面不是数字」的
    # 否定断言收尾替代原先的 \b。原因：\b 在 "S01E3e" 的 3 与 e 之间不成立，
    # 会导致整条模式失配 → 季集全丢 → 最终误判为电影（真实 bug）。
    m = re.search(
        r'(?P<title>.+?)[\s._-]*[Ss](?P<season>\d{1,2})[\s._-]*[Ee](?P<episode>\d{1,4})'
        r'(?:[vV]\d{1,2}|\.\d{1,2}|[A-Za-z])?'
        r'(?![0-9])',
        name
    )
    if m:
        title = m.group('title')
        title = _clean_brackets_and_metadata(title)
        title = _normalize_separators(title)
        title = _clean_year_from_title(title)
        title = re.sub(r'\s+', ' ', title).strip(' -')
        full_title = title
        title, en_name = _split_multilang_title(title)
        return _post_correct(
            ParseResult(title=title, season=int(m.group('season')),
                        episode=int(m.group('episode')),
                        original_title=full_title if en_name else None,
                        en_name=en_name, **meta),
            name
        )
    # ── 阶段C 梯队2: S 与集号被分隔符隔开（"标题 S01 - 03"、"标题.S1.03"）──
    m = re.search(
        r'(?P<title>.+?)[\s._-]+[Ss](?P<season>\d{1,2})[\s._-]+(?P<episode>\d{1,4})'
        r'(?![0-9])',
        name
    )
    if m:
        title = m.group('title')
        title = _clean_brackets_and_metadata(title)
        title = _normalize_separators(title)
        title = _clean_year_from_title(title)
        title = re.sub(r'\s+', ' ', title).strip(' -')
        full_title = title
        title, en_name = _split_multilang_title(title)
        return _post_correct(
            ParseResult(title=title, season=int(m.group('season')),
                        episode=int(m.group('episode')),
                        original_title=full_title if en_name else None,
                        en_name=en_name, **meta),
            name
        )
    # ── 阶段C 梯队3: 中文集数（第N集/话/話/期/回），国内最常见 ──
    # 季号可能出现在标题里（如"凡人修仙传 第二季 第3集"），交由 extract_season_from_title 提取
    m = re.search(
        r'(?P<title>.+?)[\s._-]*第\s*(?P<episode>\d{1,4})\s*[集话話期回]',
        name
    )
    if m:
        title = m.group('title')
        title = _clean_brackets_and_metadata(title)
        title = _normalize_separators(title)
        title = _clean_year_from_title(title)
        title = re.sub(r'\s+', ' ', title).strip(' -')
        # 先从标题中提取季度，再剥离季度后缀，避免"第二季"残留在标题里
        season_from_title = extract_season_from_title(title)
        if season_from_title is not None:
            for sp in SEASON_SUFFIX_PATTERNS:
                cleaned_title = re.sub(sp, '', title, flags=re.IGNORECASE).strip()
                if cleaned_title and cleaned_title != title:
                    title = cleaned_title
                    break
            title = re.sub(r'[\s\-_：:]+$', '', title).strip()
        full_title = title
        title, en_name = _split_multilang_title(title)
        return _post_correct(
            ParseResult(title=title, season=season_from_title,
                        episode=int(m.group('episode')),
                        original_title=full_title if en_name else None,
                        en_name=en_name, **meta),
            name
        )
    # ── 阶段C 梯队5: 仅季号无集号（"标题 S2"、"标题 Season 2"）──
    for pattern in [
        re.compile(r'^(?P<title>.+?)[\s._-]+[Ss](?P<season>\d{1,2})(?:\s|$)', re.IGNORECASE),
        re.compile(r'^(?P<title>.+?)[\s._-]+Season[\s._-]*(?P<season>\d{1,2})(?:\s|$)', re.IGNORECASE),
    ]:
        m = pattern.search(name)
        if m:
            title = m.group('title')
            title = _clean_brackets_and_metadata(title)
            title = _normalize_separators(title)
            title = _clean_year_from_title(title)
            title = re.sub(r'\s+', ' ', title).strip(' -')
            full_title = title
            title, en_name = _split_multilang_title(title)
            return _post_correct(
                ParseResult(title=title, season=int(m.group('season')),
                            original_title=full_title if en_name else None,
                            en_name=en_name, **meta),
                name
            )
    cleaned = _strip_all_metadata(name)
    # 移除年份括号 (如 "(2024)")
    cleaned = re.sub(r'[\(\（]\s*(19|20)\d{2}\s*[\)\）]', ' ', cleaned)
    # 移除首部字幕组括号
    cleaned = re.sub(r'^\[[^\]]+\]', '', cleaned).strip()
    # 规范化分隔符
    cleaned = cleaned.replace('.', ' ').replace('_', ' ')
    cleaned = re.sub(r'\s+', ' ', cleaned).strip(' -')

    # ── 阶段C 梯队4: 裸集号（"Title - 02"、"Title 02"）──
    for pattern in [
        re.compile(r'^(?P<title>.+?)\s*[-_]\s*(?P<episode>\d{1,4})\s*$'),
        re.compile(r'^(?P<title>.+?)\s+(?P<episode>\d{1,4})\s*$'),
    ]:
        m = pattern.search(cleaned)
        if m:
            ep = int(m.group('episode'))
            # 过滤误报: 年份不应被当作集数
            if 1900 <= ep <= 2099:
                continue
            title = m.group('title')
            title = _clean_year_from_title(title)
            title = re.sub(r'\s+', ' ', title).strip(' -')
            # 尝试从标题中提取季度信息（如 "金牌得主 第二季 Medalist 2" → season=2）
            season_from_title = extract_season_from_title(title)
            if season_from_title is not None:
                # 移除标题中的季度后缀以清理标题
                for sp in SEASON_SUFFIX_PATTERNS:
                    cleaned_title = re.sub(sp, '', title, flags=re.IGNORECASE).strip()
                    if cleaned_title and cleaned_title != title:
                        title = cleaned_title
                        break
                title = re.sub(r'[\s\-_：:]+$', '', title).strip()
            full_title = title
            title, en_name = _split_multilang_title(title)
            if title:
                return _post_correct(
                    ParseResult(title=title, season=season_from_title, episode=ep,
                                original_title=full_title if en_name else None,
                                en_name=en_name, **meta),
                    name
                )
    # ── 兜底: 无任何季集特征 → 判为电影（仍经阶段D纠偏做最后校验）──
    title = _clean_year_from_title(cleaned)
    title = re.sub(r'\s+', ' ', title).strip(' -')
    full_title = title
    title, en_name = _split_multilang_title(title)

    if title:
        return _post_correct(
            ParseResult(title=title, is_movie=True,
                        original_title=full_title if en_name else None,
                        en_name=en_name, **meta),
            name
        )

    return None


# ============================================================================
# 核心函数 2: parse_search_keyword — 搜索关键词解析
# ============================================================================

def parse_search_keyword(keyword: str) -> Dict[str, Any]:
    """
    解析搜索关键词，提取标题、季数和集数。
    替代 src/utils/common.py 中的 parse_search_keyword()。

    支持: "Title S01E01", "Title S01", "Title 第二季", "Title Ⅲ", "Title 2"
    """
    keyword = keyword.strip()
    _raw = keyword  # 保留原始完整关键词，供下游识别词等最高优先级逻辑使用

    # 1. 优先匹配 SxxExx
    m = re.match(r'^(?P<title>.+?)\s*S(?P<season>\d{1,2})E(?P<episode>\d{1,4})$', keyword, re.IGNORECASE)
    if m:
        return {
            "title": m.group('title').strip(),
            "season": int(m.group('season')),
            "episode": int(m.group('episode')),
            "original_keyword": _raw,
        }

    # 2. 匹配季度信息
    season_patterns = [
        (re.compile(r'^(.*?)\s*(?:S|Season)\s*(\d{1,2})$', re.I), lambda m: int(m.group(2))),
        (re.compile(r'^(.*?)\s*第\s*([一二三四五六七八九十\d]+)\s*[季部]$', re.I),
         lambda m: _chinese_num_to_int(m.group(2))),
        (re.compile(r'^(.*?)\s*([Ⅰ-Ⅻ])$'),
         lambda m: FULLWIDTH_ROMAN_MAP.get(m.group(2).upper())),
        (re.compile(r'^(.*?)\s+([IVXLCDM]+)$', re.I),
         lambda m: _roman_to_int(m.group(2))),
        (re.compile(r'^(.*?)\s+(\d{1,2})$'),
         lambda m: int(m.group(2))),
    ]

    for pattern, handler in season_patterns:
        m = pattern.match(keyword)
        if m:
            try:
                title = m.group(1).strip()
                season = handler(m)
                # 避免将年份误认为季度
                if season and not (len(title) > 4 and title[-4:].isdigit()):
                    return {"title": title, "season": season, "episode": None, "original_keyword": _raw}
            except (ValueError, KeyError, IndexError):
                continue

    # 3. 无匹配，返回原始标题
    return {"title": keyword, "season": None, "episode": None, "original_keyword": _raw}


# ============================================================================
# 统一日志格式化：把解析结果拼成一条多行日志（每字段单独一行）
# ============================================================================

# 字段展示顺序与中文标签（值为空的字段不输出）
_PARSE_LOG_FIELDS = [
    ("title", "标题"),
    ("year", "年份"),
    ("season", "季度"),
    ("episode", "集数"),
    ("is_movie", "是否剧场版"),
    ("resolution", "分辨率"),
    ("video_codec", "视频编码"),
    ("audio_codec", "音频编码"),
    ("source", "来源"),
    ("platform", "平台"),
    ("dynamic_range", "动态范围"),
    ("effect", "特效"),
    ("team", "发布组"),
    ("en_name", "英文名"),
    ("original_title", "原始标题"),
]


def format_parse_result_log(source: str, raw_input: str, result: Any) -> str:
    """
    把文件名解析结果拼成一条多行日志字符串，供 logger.info 一次性输出。

    每个字段单独占一行，空值字段自动跳过，避免刷屏也便于肉眼核对。
    同时兼容 ParseResult 对象与 dict（parse_search_keyword 的返回）。

    :param source: 来源标签，如「后备匹配」「Webhook」「外部自动导入」
    :param raw_input: 用户/播放器传入的原始文件名或关键词
    :param result: parse_filename 返回的 ParseResult，或 parse_search_keyword 返回的 dict
    :return: 形如「[来源] 文件名解析结果：\n  原始输入: ...\n  标题: ...」的多行字符串
    """
    lines = [f"[{source}] 文件名解析结果："]
    lines.append(f"  原始输入: {raw_input}")

    if result is None:
        lines.append("  （解析失败，未能提取任何信息）")
        return "\n".join(lines)

    # 统一取值：ParseResult 用属性访问，dict 用 key 访问
    def _get(field_name: str) -> Any:
        if isinstance(result, dict):
            return result.get(field_name)
        return getattr(result, field_name, None)

    for field_name, label in _PARSE_LOG_FIELDS:
        value = _get(field_name)
        # 跳过空值（None / 空串），但布尔 False 的「是否剧场版」需特殊处理
        if field_name == "is_movie":
            # 仅当明确是剧场版时才打印，避免每条都显示「否」
            if value:
                lines.append(f"  {label}: 是")
            continue
        if value is None or value == "":
            continue
        lines.append(f"  {label}: {value}")

    # 强制元数据单独展示（dict 类型，逐项列出便于核对用户显式指定的意图）
    forced = _get("forced")
    if forced:
        pairs = ", ".join(f"{k}={v}" for k, v in forced.items())
        lines.append(f"  强制元数据: {pairs}")

    return "\n".join(lines)


# ============================================================================

# 核心函数 3: extract_season_episode — 从文件名提取季集
# ============================================================================

def extract_season_episode(text: str) -> Tuple[Optional[int], Optional[int]]:
    """
    从文件名/文本中提取季集信息。
    替代 local_danmaku_scanner._extract_season_episode()。

    支持: S01E01, 第1季第1集, 1x01, E01/EP01
    """
    # SxxExx
    m = re.search(r'[Ss](\d+)[Ee](\d+)', text)
    if m:
        return int(m.group(1)), int(m.group(2))

    # 中文: 第1季第1集
    m = re.search(r'第(\d+)季第(\d+)集', text)
    if m:
        return int(m.group(1)), int(m.group(2))

    # 1x01
    m = re.search(r'(\d+)x(\d+)', text)
    if m:
        return int(m.group(1)), int(m.group(2))

    # E01, EP01
    m = re.search(r'[Ee][Pp]?(\d+)', text)
    if m:
        return 1, int(m.group(1))

    return None, None


# ============================================================================
# 核心函数 4: extract_season_from_title — 从标题提取季度
# ============================================================================

def extract_season_from_title(title: str) -> Optional[int]:
    """
    从标题中提取明确的季度信息。
    替代 season_mapper._extract_explicit_season_from_title()。

    识别: "第二季", "Season 2", "S2", 罗马数字 "II", 末尾数字 "暴风之铳2"
    """
    if not title:
        return None

    title_clean = title.strip()

    # 模式1: 中文 "第N季"
    m = re.search(r'第([一二三四五六七八九十]+|\d+)季', title_clean)
    if m:
        return _chinese_num_to_int(m.group(1))

    # 模式2: "Season N"
    m = re.search(r'Season\s*(\d+)', title_clean, re.IGNORECASE)
    if m:
        return int(m.group(1))

    # 模式3: "S2" (空格后或末尾)
    m = re.search(r'(?:^|\s)S(\d+)(?:\s|$)', title_clean, re.IGNORECASE)
    if m:
        return int(m.group(1))

    # 模式4: 罗马数字 (末尾)
    m = re.search(r'\s+(I{1,3}|IV|VI{0,3}|IX|X|[ⅰⅱⅲⅳⅴⅵⅶⅷⅸⅹ])\s*$', title_clean, re.IGNORECASE)
    if m:
        roman = m.group(1).lower()
        if roman in ROMAN_NUM_MAP:
            return ROMAN_NUM_MAP[roman]

    # 模式5: 末尾阿拉伯数字 (排除年份和分辨率)
    m = re.search(r'[^\d](\d{1,2})\s*$', title_clean)
    if m:
        num = int(m.group(1))
        if 1 <= num <= 20:
            return num

    return None


# ============================================================================
# 核心函数 5-7: 标题清理系列
# ============================================================================

def clean_title(title: str) -> str:
    """
    清理标题，移除元数据标识(TMDBID等)、年份、多余空格。
    替代 local_danmaku_scanner._clean_title()。
    """
    if not title:
        return title

    # 移除 TMDBID/TVDBID/IMDBID 标记
    title = re.sub(r'[（(]TMDBID=\d+[）)]', '', title, flags=re.IGNORECASE)
    title = re.sub(r'[（(]TVDBID=\d+[）)]', '', title, flags=re.IGNORECASE)
    title = re.sub(r'[（(]IMDBID=tt\d+[）)]', '', title, flags=re.IGNORECASE)

    # 移除年份
    title = re.sub(r'\s*[（(]\d{4}[）)]\s*', ' ', title)

    # 移除多余空格
    title = re.sub(r'\s+', ' ', title).strip()
    return title


def clean_movie_title(title: Optional[str]) -> Optional[str]:
    """
    清理电影标题，移除"劇場版"、"the movie"等关键词。
    替代 bangumi.py 和 tmdb.py 中重复的 _clean_movie_title()。
    """
    if not title:
        return None
    phrases_to_remove = ["劇場版", "the movie"]
    cleaned = title
    for phrase in phrases_to_remove:
        cleaned = re.sub(r'\s*' + re.escape(phrase) + r'\s*:?', '', cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r'\s{2,}', ' ', cleaned).strip().strip(':- ')
    return cleaned


def normalize_title(title: str) -> str:
    """
    标准化标题，去除季度相关信息。
    替代 path_template.normalize_title()。

    Examples:
        "Re：从零开始的异世界生活 第三季" → "Re：从零开始的异世界生活"
        "葬送的芙莉莲 第2期" → "葬送的芙莉莲"
        "无职转生 第二季 Part 2" → "无职转生"
    """
    if not title:
        return title

    result = title.strip()

    for pattern in SEASON_SUFFIX_PATTERNS:
        result = re.sub(pattern, '', result, flags=re.IGNORECASE)

    # 清理末尾标点和空格
    result = re.sub(r'[\s\-_：:]+$', '', result)

    # 处理后为空则返回原标题
    if not result.strip():
        return title.strip()

    return result.strip()


# ============================================================================
# 核心函数 8-9: 标题判断
# ============================================================================

def is_movie_by_title(title: str) -> bool:
    """
    通过标题关键词判断是否为电影。
    替代 tasks/utils.py 和 webhook.py 中重复的 is_movie_by_title()。
    """
    if not title:
        return False
    title_lower = title.lower()
    return any(kw in title_lower for kw in MOVIE_KEYWORDS)


def is_chinese_title(title: str) -> bool:
    """
    检查标题是否为中文标题（排除日文）。
    替代 tasks/utils.py 中的 is_chinese_title()。
    """
    if not title:
        return False
    # 包含日文假名则不是中文
    if re.search(r'[\u3040-\u309f\u30a0-\u30ff]', title):
        return False
    # 包含中文字符
    return bool(re.search(r'[\u4e00-\u9fff\u3400-\u4dbf\uf900-\ufaff]', title))


# ============================================================================
# 核心函数 10-11: 集数范围解析与格式化
# ============================================================================

def parse_episode_ranges(episode_str: str) -> List[int]:
    """
    解析集数范围字符串。
    替代 tasks/utils.py 和 plex.py 中重复的 parse_episode_ranges()。

    支持: "1", "1-3", "1,3,5,7,9,11-13"
    """
    episodes = []
    episode_str = episode_str.replace(" ", "")
    parts = episode_str.split(",")

    for part in parts:
        if "-" in part:
            try:
                start, end = part.split("-", 1)
                episodes.extend(range(int(start), int(end) + 1))
            except (ValueError, IndexError) as e:
                logger.warning(f"无法解析集数范围 '{part}': {e}")
                continue
        else:
            try:
                episodes.append(int(part))
            except ValueError as e:
                logger.warning(f"无法解析集数 '{part}': {e}")
                continue

    episodes = sorted(list(set(episodes)))
    logger.info(f"解析集数范围 '{episode_str}' -> {episodes}")
    return episodes


def format_episode_ranges(episodes: List[int], separator: str = ", ") -> str:
    """
    将集数列表格式化为紧凑的范围字符串。
    替代 tasks/utils.py 的 generate_episode_range_string() 和
    helpers.py 的 format_episode_ranges()。

    通过 separator 参数统一两种分隔符风格:
    - separator=", " → "1-3, 5, 8-10" (原 generate_episode_range_string)
    - separator="," → "1-3,5-7,10" (原 format_episode_ranges)
    """
    if not episodes:
        return "无" if separator == ", " else ""

    indices = sorted(list(set(episodes)))
    if not indices:
        return "无" if separator == ", " else ""

    ranges = []
    start = end = indices[0]

    for i in range(1, len(indices)):
        if indices[i] == end + 1:
            end = indices[i]
        else:
            ranges.append(str(start) if start == end else f"{start}-{end}")
            start = end = indices[i]
    ranges.append(str(start) if start == end else f"{start}-{end}")
    return separator.join(ranges)

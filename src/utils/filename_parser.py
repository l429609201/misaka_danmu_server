"""
统一文件名解析模块

整合项目中散落的文件名识别、标题清理、季集提取等辅助函数，
提供统一的接口和更全面的正则模式。

参考: https://github.com/pipi20xx/anime-matcher 的正则模式设计
"""

import re
import logging
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
    """将中文数字转换为整数，支持阿拉伯数字直通"""
    if s.isdigit():
        return int(s)
    return CHINESE_NUM_MAP.get(s)


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

    采用上游 anime-matcher 的 "先剥离元数据，再提取标题" 策略：
    1. 从原始文件名中提取元数据值（分辨率、编码等）
    2. 剥离所有元数据标签，得到干净的标题+集数字符串
    3. 在干净字符串上做标题/季集模式匹配
    """
    name = _strip_video_extension(filename)

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
    )

    # ── 阶段1.5: 在原始文件名上先尝试 SxxExx (最可靠的模式) ──
    m = re.search(
        r'(?P<title>.+?)[\s._-]*[Ss](?P<season>\d{1,2})[Ee](?P<episode>\d{1,4})\b',
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
        return ParseResult(title=title, season=int(m.group('season')),
                           episode=int(m.group('episode')),
                           original_title=full_title if en_name else None,
                           en_name=en_name, **meta)
    m = re.search(
        r'(?P<title>.+?)[\s._-]+[Ss](?P<season>\d{1,2})[\s._-]+(?P<episode>\d{1,4})\b',
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
        return ParseResult(title=title, season=int(m.group('season')),
                           episode=int(m.group('episode')),
                           original_title=full_title if en_name else None,
                           en_name=en_name, **meta)
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
            return ParseResult(title=title, season=int(m.group('season')),
                               original_title=full_title if en_name else None,
                               en_name=en_name, **meta)
    cleaned = _strip_all_metadata(name)
    # 移除年份括号 (如 "(2024)")
    cleaned = re.sub(r'[\(\（]\s*(19|20)\d{2}\s*[\)\）]', ' ', cleaned)
    # 移除首部字幕组括号
    cleaned = re.sub(r'^\[[^\]]+\]', '', cleaned).strip()
    # 规范化分隔符
    cleaned = cleaned.replace('.', ' ').replace('_', ' ')
    cleaned = re.sub(r'\s+', ' ', cleaned).strip(' -')

    # ── 阶段3: 在干净字符串上做 Episode / Movie 匹配 ──

    # 模式3: Episode only ("Title - 02", "Title 02")
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
            full_title = title
            title, en_name = _split_multilang_title(title)
            if title:
                return ParseResult(title=title, episode=ep,
                                   original_title=full_title if en_name else None,
                                   en_name=en_name, **meta)
    title = _clean_year_from_title(cleaned)
    title = re.sub(r'\s+', ' ', title).strip(' -')
    full_title = title
    title, en_name = _split_multilang_title(title)

    if title:
        return ParseResult(title=title, is_movie=True,
                           original_title=full_title if en_name else None,
                           en_name=en_name, **meta)

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

    # 1. 优先匹配 SxxExx
    m = re.match(r'^(?P<title>.+?)\s*S(?P<season>\d{1,2})E(?P<episode>\d{1,4})$', keyword, re.IGNORECASE)
    if m:
        return {
            "title": m.group('title').strip(),
            "season": int(m.group('season')),
            "episode": int(m.group('episode')),
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
                    return {"title": title, "season": season, "episode": None}
            except (ValueError, KeyError, IndexError):
                continue

    # 3. 无匹配，返回原始标题
    return {"title": keyword, "season": None, "episode": None}


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

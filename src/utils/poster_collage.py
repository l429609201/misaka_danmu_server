"""
海报聚合工具 — 将多个搜索结果的海报拼接为一张带序号的网格图。

设计要点：
- 使用 httpx 并发下载各结果的海报（单张限时，整体限并发）
- 使用 Pillow 将海报统一缩放为固定尺寸后拼成网格，左上角绘制序号徽标
- 下载失败/无海报的位置用灰色占位块 + 序号兜底
- 所有 Pillow 同步绘制放到 asyncio.to_thread 执行，避免阻塞事件循环
- 返回 PNG bytes，供 Telegram send_photo 直接发送
"""
import asyncio
import io
import logging
from typing import List, Dict, Optional

import httpx

logger = logging.getLogger(__name__)

# 单张海报在网格中的目标尺寸（宽×高，电影海报常见 2:3 比例）
CELL_W = 180
CELL_H = 270
# 单元格内边距与单元格之间的间距
GAP = 6
# 整图外边距
MARGIN = 10
# 背景色（深色，贴近 TG 暗色主题）
BG_COLOR = (24, 26, 32)
PLACEHOLDER_COLOR = (55, 58, 66)

# 通用浏览器 UA，提高图片站点下载成功率
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


def _pick_columns(count: int) -> int:
    """根据海报数量选择网格列数：≤4 单行，否则最多 5 列。"""
    if count <= 4:
        return max(1, count)
    return 5


async def _download_one(client: httpx.AsyncClient, url: str) -> Optional[bytes]:
    """下载单张海报，失败返回 None。针对特定图床补充 Referer。"""
    if not url:
        return None
    if url.startswith("//"):
        url = "https:" + url
    if "iqiyipic.com" in url:
        url = url.replace("http://", "https://", 1)
    headers = {}
    if "iqiyipic.com" in url:
        headers["Referer"] = "https://www.iqiyi.com/"
    elif "hdslb.com" in url:
        headers["Referer"] = "https://www.bilibili.com/"
    try:
        resp = await client.get(url, headers=headers or None)
        resp.raise_for_status()
        return resp.content
    except Exception as e:
        logger.debug(f"海报下载失败 (url={url}): {e}")
        return None


async def build_poster_collage(
    items: List[Dict],
    proxy: Optional[str] = None,
    ssl_verify: bool = True,
) -> Optional[bytes]:
    """将搜索结果的海报聚合为一张带序号的网格图。

    Args:
        items: 列表，每项至少含 'imageUrl' 与 'index'（1 起的展示序号）。
        proxy: 可选代理 URL。
        ssl_verify: 是否校验 SSL。

    Returns:
        PNG bytes；若无任何可用海报或 Pillow 不可用则返回 None。
    """
    if not items:
        return None

    # 并发下载所有海报
    urls = [it.get("imageUrl") or "" for it in items]
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(6.0, connect=4.0),
            follow_redirects=True,
            proxy=proxy,
            verify=ssl_verify,
            headers={"User-Agent": _UA},
        ) as client:
            sem = asyncio.Semaphore(8)

            async def _guarded(u: str):
                async with sem:
                    return await _download_one(client, u)

            raw_list = await asyncio.gather(*[_guarded(u) for u in urls])
    except Exception as e:
        logger.warning(f"海报并发下载初始化失败: {e}")
        raw_list = [None] * len(urls)

    # 至少要有一张能下载成功才值得聚合（否则纯占位图意义不大）
    if not any(raw_list):
        return None

    indices = [int(it.get("index", i + 1)) for i, it in enumerate(items)]

    # Pillow 绘制是 CPU 同步操作，放到线程池执行
    try:
        return await asyncio.to_thread(_render_grid, raw_list, indices)
    except Exception as e:
        logger.warning(f"海报聚合绘制失败: {e}")
        return None


_FONT_CACHE: Dict[int, object] = {}


def _load_font(size: int):
    """加载序号字体：优先系统中文/常见字体，失败回退 Pillow 默认位图字体。"""
    if size in _FONT_CACHE:
        return _FONT_CACHE[size]
    from PIL import ImageFont

    candidates = [
        "C:/Windows/Fonts/msyhbd.ttc",   # 微软雅黑 Bold（Windows）
        "C:/Windows/Fonts/msyh.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",  # Linux 常见
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/System/Library/Fonts/PingFang.ttc",  # macOS
        "arialbd.ttf",
        "arial.ttf",
    ]
    font = None
    for path in candidates:
        try:
            font = ImageFont.truetype(path, size)
            break
        except Exception:
            continue
    if font is None:
        try:
            font = ImageFont.load_default()
        except Exception:
            font = None
    _FONT_CACHE[size] = font
    return font


def _draw_badge(draw, x: int, y: int, number: int):
    """在 (x, y) 左上角绘制带半透明底的序号徽标。"""
    label = str(number)
    font = _load_font(26)
    # 徽标尺寸随位数自适应
    pad_x, pad_y = 8, 4
    top_offset = 0
    try:
        bbox = draw.textbbox((0, 0), label, font=font)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        top_offset = bbox[1]  # 字体顶部留白，绘制时需减去以视觉居中
    except Exception:
        tw, th = 14 * len(label), 22
    bw, bh = tw + pad_x * 2, th + pad_y * 2
    # 半透明黑底圆角矩形
    draw.rounded_rectangle(
        [x + 4, y + 4, x + 4 + bw, y + 4 + bh],
        radius=6, fill=(0, 0, 0, 180),
    )
    # 序号文字（亮黄色，醒目）
    draw.text(
        (x + 4 + pad_x, y + 4 + pad_y - top_offset),
        label, font=font, fill=(255, 209, 71),
    )


def _render_grid(raw_list: List[Optional[bytes]], indices: List[int]) -> bytes:
    """同步绘制网格图，返回 PNG bytes。"""
    from PIL import Image, ImageDraw

    count = len(raw_list)
    cols = _pick_columns(count)
    rows = (count + cols - 1) // cols

    canvas_w = MARGIN * 2 + cols * CELL_W + (cols - 1) * GAP
    canvas_h = MARGIN * 2 + rows * CELL_H + (rows - 1) * GAP
    canvas = Image.new("RGB", (canvas_w, canvas_h), BG_COLOR)

    for i, raw in enumerate(raw_list):
        r, c = divmod(i, cols)
        x = MARGIN + c * (CELL_W + GAP)
        y = MARGIN + r * (CELL_H + GAP)
        cell = _make_cell(raw)
        canvas.paste(cell, (x, y))

    # 在 RGBA 图层上绘制半透明徽标，再合并回去
    overlay = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    odraw = ImageDraw.Draw(overlay)
    for i in range(count):
        r, c = divmod(i, cols)
        x = MARGIN + c * (CELL_W + GAP)
        y = MARGIN + r * (CELL_H + GAP)
        _draw_badge(odraw, x, y, indices[i])
    canvas = Image.alpha_composite(canvas.convert("RGBA"), overlay).convert("RGB")

    buf = io.BytesIO()
    canvas.save(buf, format="PNG")
    return buf.getvalue()


def _make_cell(raw: Optional[bytes]):
    """将单张海报字节渲染为统一尺寸单元格；无图时返回灰色占位。"""
    from PIL import Image

    if raw:
        try:
            img = Image.open(io.BytesIO(raw)).convert("RGB")
            return _fit_cover(img, CELL_W, CELL_H)
        except Exception:
            pass
    return Image.new("RGB", (CELL_W, CELL_H), PLACEHOLDER_COLOR)


def _fit_cover(img, target_w: int, target_h: int):
    """等比缩放并居中裁剪填满目标尺寸（cover 效果）。"""
    from PIL import Image

    src_w, src_h = img.size
    scale = max(target_w / src_w, target_h / src_h)
    new_w, new_h = int(src_w * scale + 0.5), int(src_h * scale + 0.5)
    img = img.resize((new_w, new_h), Image.LANCZOS)
    left = (new_w - target_w) // 2
    top = (new_h - target_h) // 2
    return img.crop((left, top, left + target_w, top + target_h))

"""
看板娘素材白底转透明脚本（路 B：纯 Pillow，轻量）
------------------------------------------------------------
思路（关键点：只删"外部背景白"，不误伤角色身上的白）：
1. 从图片四条边的像素作为"背景种子"，用洪水填充（BFS）找出所有
   与边缘连通、且接近白色的像素 —— 这些才是背景。
2. 角色内部即使有白色（高光、白袜等），因为不与外边缘连通，不会被删。
3. 对生成的 alpha 蒙版做轻微收缩 + 高斯模糊，实现边缘羽化，消除白边。
4. 原图先备份到 _original/ 子目录，再把透明结果写回原文件名。

用法：python tools/remove_bg.py
"""
import os
import glob
import shutil
from collections import deque

from PIL import Image, ImageFilter

# 素材目录（相对项目根）
ASSET_DIR = os.path.join("web", "src", "assets", "assistant")
BACKUP_DIR = os.path.join(ASSET_DIR, "_original")

# 背景判定：像素与"背景基准色"的各通道差值都 <= 容差，即视为背景。
# 背景基准色由四角像素自适应取得（应对不同图背景略偏灰/偏蓝）。
COLOR_TOLERANCE = 32
# 兜底最低白阈值：低于此亮度的一律不当背景，防止把深色误删
MIN_BG_LUMA = 220
# 边缘羽化半径（像素）
FEATHER_RADIUS = 1.5


def build_bg_mask(img: Image.Image) -> Image.Image:
    """返回一张 L 模式蒙版：255=背景(要透明)，0=前景(保留)。"""
    w, h = img.size
    px = img.load()
    bg = bytearray(w * h)  # 0/1
    q = deque()

    # 自适应背景基准色：取四角像素的平均值
    corners = [px[0, 0][:3], px[w - 1, 0][:3], px[0, h - 1][:3], px[w - 1, h - 1][:3]]
    base = tuple(sum(c[i] for c in corners) // 4 for i in range(3))

    def is_bg(x, y):
        r, g, b = px[x, y][:3]
        if min(r, g, b) < MIN_BG_LUMA:
            return False
        return (abs(r - base[0]) <= COLOR_TOLERANCE
                and abs(g - base[1]) <= COLOR_TOLERANCE
                and abs(b - base[2]) <= COLOR_TOLERANCE)

    # 四条边的背景色像素作为种子入队
    for x in range(w):
        for y in (0, h - 1):
            if not bg[y * w + x] and is_bg(x, y):
                bg[y * w + x] = 1
                q.append((x, y))
    for y in range(h):
        for x in (0, w - 1):
            if not bg[y * w + x] and is_bg(x, y):
                bg[y * w + x] = 1
                q.append((x, y))

    # 4 邻域洪水填充
    while q:
        x, y = q.popleft()
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nx, ny = x + dx, y + dy
            if 0 <= nx < w and 0 <= ny < h:
                idx = ny * w + nx
                if not bg[idx] and is_bg(nx, ny):
                    bg[idx] = 1
                    q.append((nx, ny))

    mask = Image.frombytes("L", (w, h), bytes(255 if b else 0 for b in bg))
    return mask


def process_one(path: str):
    img = Image.open(path).convert("RGB")
    bg_mask = build_bg_mask(img)
    # alpha = 255 - 背景蒙版：背景处透明，前景处不透明
    alpha = bg_mask.point(lambda v: 255 - v)
    # 羽化：模糊 alpha 边缘，去掉硬白边
    alpha = alpha.filter(ImageFilter.GaussianBlur(FEATHER_RADIUS))
    out = img.convert("RGBA")
    out.putalpha(alpha)
    out.save(path)  # 覆盖回原文件名（前端引用不变）
    return out.size


def main():
    os.makedirs(BACKUP_DIR, exist_ok=True)
    files = sorted(glob.glob(os.path.join(ASSET_DIR, "*.png")))
    files = [f for f in files if os.path.dirname(f) == ASSET_DIR]  # 排除子目录
    for f in files:
        name = os.path.basename(f)
        backup = os.path.join(BACKUP_DIR, name)
        if not os.path.exists(backup):
            shutil.copy2(f, backup)  # 首次运行备份原图
        size = process_one(f)
        print(f"[OK] {name} -> 透明, {size}")
    print(f"完成，共 {len(files)} 张；原图备份在 {BACKUP_DIR}")


if __name__ == "__main__":
    main()

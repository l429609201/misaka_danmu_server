# 代码风格规范

本文档定义 Misaka Danmu Server 的 Python 代码风格和编码规范。所有 AI Agent 生成的代码必须遵循这些规则。

---

## 🐍 Python 代码规范

### 1. 导入规范

#### 导入顺序

```python
# 1. 标准库
import asyncio
import logging
from typing import List, Optional, Dict

# 2. 第三方库
from sqlalchemy import select
from fastapi import HTTPException

# 3. 项目内部模块
from src.db import crud, orm_models
from src.services import ScraperManager
```

#### 导入规则

✅ **正确的导入**：
```python
# 推荐：显式导入
from src.db.orm_models import Anime, AnimeSource

# 推荐：使用别名避免命名冲突
from src.db import orm_models
Anime = orm_models.Anime
```

❌ **禁止的导入**：
```python
# ❌ 禁止：通配符导入
from src.db.orm_models import *

# ❌ 禁止：循环导入
# 如果 A 导入 B，B 不能导入 A
```

#### 避免循环导入

**问题**：
```python
# src/services/search.py
from src.tasks.webhook import webhook_search  # ❌ A 导入 B

# src/tasks/webhook.py
from src.services.search import unified_search  # ❌ B 导入 A
```

**解决方案**：
```python
# 方案 1：延迟导入（在函数内部导入）
def my_function():
    from src.tasks.webhook import webhook_search
    return webhook_search()

# 方案 2：重构依赖（提取到第三个模块）
# src/core/search_common.py
```

---

### 2. 类型注解

#### 函数签名

✅ **必须添加类型注解**：
```python
async def get_anime_by_title(
    session: AsyncSession,
    title: str,
    season: Optional[int] = None
) -> Optional[Dict[str, Any]]:
    """通过标题查询番剧"""
    pass
```

❌ **禁止省略类型**：
```python
async def get_anime_by_title(session, title, season=None):  # ❌
    pass
```

#### 常用类型

```python
from typing import List, Dict, Optional, Union, Tuple, Any

# 列表
episodes: List[int] = [1, 2, 3]

# 字典
config: Dict[str, Any] = {"key": "value"}

# 可选值
anime_id: Optional[int] = None

# 联合类型
result: Union[str, int] = "success"

# 元组
coordinates: Tuple[int, int] = (10, 20)
```

---

### 3. 异步编程规范

#### 异步函数定义

```python
# ✅ 正确：async/await 风格
async def fetch_comments(video_id: str) -> List[Comment]:
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as response:
            return await response.json()

# ❌ 错误：混用同步和异步
async def fetch_comments(video_id: str):
    # ❌ 在 async 函数中使用同步 requests
    response = requests.get(url)
    return response.json()
```

#### Session 管理

```python
# ✅ 正确：使用 async with 自动关闭
async with session_factory() as session:
    anime = await crud.get_anime(session, anime_id)
    await session.commit()

# ❌ 错误：手动管理 session（容易忘记关闭）
session = session_factory()
anime = await crud.get_anime(session, anime_id)
await session.commit()
session.close()  # 容易遗漏
```

---

### 4. 错误处理

#### 异常捕获

```python
# ✅ 正确：具体异常类型
try:
    anime = await crud.get_anime(session, anime_id)
except SQLAlchemyError as e:
    logger.error(f"数据库错误: {e}")
    raise
except ValueError as e:
    logger.warning(f"参数错误: {e}")
    raise HTTPException(status_code=400, detail=str(e))

# ❌ 错误：捕获所有异常
try:
    anime = await crud.get_anime(session, anime_id)
except Exception:  # ❌ 太宽泛
    pass
```

#### 日志记录

```python
import logging
logger = logging.getLogger(__name__)

# ✅ 正确：不同级别的日志
logger.debug(f"开始搜索: {keyword}")
logger.info(f"找到 {len(results)} 个结果")
logger.warning(f"数据源 {provider} 响应慢")
logger.error(f"获取弹幕失败: {e}", exc_info=True)

# ❌ 错误：使用 print
print(f"搜索结果: {results}")  # ❌ 不会被记录到日志文件
```

---

### 5. 命名规范

#### 变量和函数

```python
# ✅ 正确：snake_case
anime_title = "进击的巨人"
episode_count = 24

async def get_anime_by_id(anime_id: int):
    pass

# ❌ 错误：camelCase 或混用
animeTitle = "进击的巨人"  # ❌
EpisodeCount = 24  # ❌

async def GetAnimeById(anime_id: int):  # ❌
    pass
```

#### 类名

```python
# ✅ 正确：PascalCase
class BilibiliScraper:
    pass

class TaskManager:
    pass

# ❌ 错误：snake_case 或其他
class bilibili_scraper:  # ❌
    pass
```

#### 常量

```python
# ✅ 正确：UPPER_CASE
MAX_RETRY_COUNT = 3
DEFAULT_TIMEOUT = 30
API_BASE_URL = "https://api.example.com"

# ❌ 错误：小写
max_retry_count = 3  # ❌
```

---

### 6. 注释规范

#### Docstring（必须）

```python
async def search_anime(
    keyword: str,
    season: Optional[int] = None
) -> List[SearchResult]:
    """
    搜索番剧作品。
    
    Args:
        keyword: 搜索关键词，支持中文/英文/日文
        season: 可选的季度筛选，如 1 表示第一季
    
    Returns:
        搜索结果列表，包含标题、年份、分集数等信息
    
    Raises:
        HTTPException: 网络请求失败时抛出
        ValueError: 关键词为空时抛出
    """
    pass
```

#### 行内注释（必要时）

```python
# ✅ 正确：解释"为什么"
# 修复 Issue #123：iQiyi 的 mediaId 可能会变化，需要通过 title+season 模糊匹配
if title_match and season_match:
    score += 3000

# ❌ 错误：重复代码内容
# 如果标题匹配且季度匹配，分数加 3000
if title_match and season_match:  # ❌ 废话注释
    score += 3000
```

---

### 7. 数据结构

#### 使用 Pydantic 模型

```python
from pydantic import BaseModel, Field

# ✅ 正确：使用 Pydantic 定义数据模型
class SearchRequest(BaseModel):
    keyword: str = Field(..., description="搜索关键词")
    season: Optional[int] = Field(None, description="季度")
    year: Optional[int] = Field(None, description="年份")

# ✅ 正确：使用 Pydantic 验证
request = SearchRequest(keyword="进击的巨人", season=1)
print(request.keyword)  # "进击的巨人"

# ❌ 错误：使用普通字典（无验证）
request = {"keyword": "进击的巨人", "season": 1}  # ❌
```

---

### 8. 代码组织

#### 文件结构

```python
"""模块说明（必须）"""

# 1. 导入
import logging
from typing import List

# 2. 常量定义
MAX_RETRY = 3
DEFAULT_TIMEOUT = 30

# 3. 日志初始化
logger = logging.getLogger(__name__)

# 4. 类定义
class MyClass:
    pass

# 5. 函数定义
async def my_function():
    pass
```

#### 函数长度

```python
# ✅ 推荐：函数保持简短（<50 行）
async def process_episode(episode: Episode):
    # 验证
    if not validate_episode(episode):
        return
    
    # 下载
    comments = await download_comments(episode)
    
    # 保存
    await save_comments(comments)

# ❌ 避免：超长函数（>100 行）
async def process_everything():  # ❌ 一个函数做太多事
    # ... 200 行代码 ...
    pass
```

---

## 🚨 常见错误

### 1. 忘记 await

```python
# ❌ 错误：忘记 await
result = fetch_data()  # 返回 coroutine 对象，不是结果

# ✅ 正确
result = await fetch_data()
```

### 2. 在循环中创建 session

```python
# ❌ 错误：每次循环创建 session
for episode in episodes:
    async with session_factory() as session:
        await save_episode(session, episode)

# ✅ 正确：在循环外创建 session
async with session_factory() as session:
    for episode in episodes:
        await save_episode(session, episode)
    await session.commit()
```

### 3. 硬编码魔法数字

```python
# ❌ 错误：硬编码
if retry_count > 3:  # ❌ 3 是什么意思？
    pass

# ✅ 正确：使用常量
MAX_RETRY_COUNT = 3
if retry_count > MAX_RETRY_COUNT:
    pass
```

---

## 📝 代码审查检查清单

修改代码时，确保：

- [ ] 所有导入都放在文件顶部，按标准库 → 第三方 → 项目内部排序
- [ ] 所有函数都有类型注解
- [ ] 所有公开函数都有 docstring
- [ ] 使用 `async/await` 而非同步阻塞调用
- [ ] 异常捕获具体到异常类型
- [ ] 使用 logger 而非 print
- [ ] 变量名使用 `snake_case`，类名使用 `PascalCase`
- [ ] 常量使用 `UPPER_CASE`
- [ ] 没有循环导入
- [ ] 没有超过 100 行的函数

---

*最后更新：2026-01-08*

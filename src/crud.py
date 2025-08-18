import aiomysql
import json
import logging
import re
import secrets
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Dict, Any

from . import models, security


async def get_library_anime(pool: aiomysql.Pool) -> List[Dict[str, Any]]:
    """获取媒体库中的所有番剧及其关联信息（如分集数）"""
    async with pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cursor:
            # 使用 LEFT JOIN 和 GROUP BY 来统计每个番剧的分集数
            query = """
                SELECT
                    a.id as animeId,
                    a.local_image_path,
                    a.image_url as imageUrl,
                    a.title,
            a.type,
                    a.season,
                    a.created_at as createdAt,                    
                    COALESCE(a.episode_count, (SELECT COUNT(DISTINCT e.id) FROM anime_sources s JOIN episode e ON s.id = e.source_id WHERE s.anime_id = a.id)) as episodeCount,
                    (SELECT COUNT(*) FROM anime_sources WHERE anime_id = a.id) as sourceCount
                FROM anime a
                GROUP BY a.id
                ORDER BY a.created_at DESC
            """
            await cursor.execute(query)
            return await cursor.fetchall()


async def search_anime(pool: aiomysql.Pool, keyword: str) -> List[Dict[str, Any]]:
    """在数据库中搜索番剧 (使用FULLTEXT索引)"""
    async with pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cursor:
            query = "SELECT id, title, type FROM anime WHERE MATCH(title) AGAINST(%s IN BOOLEAN MODE)"
            # 清理关键词以避免FULLTEXT语法错误
            sanitized_keyword = re.sub(r'[+\-><()~*@"]', ' ', keyword).strip()
            if not sanitized_keyword:
                return []
            await cursor.execute(query, (sanitized_keyword + '*',))
            return await cursor.fetchall()

async def search_episodes_in_library(pool: aiomysql.Pool, anime_title: str, episode_number: Optional[int], season_number: Optional[int] = None) -> List[Dict[str, Any]]:
    """
    在本地库中通过番剧标题和可选的集数搜索匹配的分集。
    返回一个扁平化的列表，包含番剧和分集信息。
    """
    clean_title = anime_title.strip()
    if not clean_title:
        return []

    # Build WHERE clauses
    episode_condition = "AND e.episode_index = %s" if episode_number is not None else ""
    params_episode = [episode_number] if episode_number is not None else []
    season_condition = "AND a.season = %s" if season_number is not None else ""
    params_season = [season_number] if season_number is not None else []

    query_template = f"""
        SELECT
            a.id AS animeId,
            a.title AS animeTitle,
            a.type,
            a.image_url AS imageUrl,
            a.created_at AS startDate,
            e.id AS episodeId,
            CASE
                WHEN a.type = 'movie' THEN CONCAT(s.provider_name, ' 源')
                ELSE e.title
            END AS episodeTitle,            
            al.name_en,
            al.name_jp,
            al.name_romaji,
            al.alias_cn_1,
            al.alias_cn_2,
            al.alias_cn_3,
            sc.display_order,
            s.is_favorited AS isFavorited,
            (SELECT COUNT(DISTINCT e_count.id) FROM anime_sources s_count JOIN episode e_count ON s_count.id = e_count.source_id WHERE s_count.anime_id = a.id) as totalEpisodeCount,
            m.bangumi_id AS bangumiId
        FROM episode e
        JOIN anime_sources s ON e.source_id = s.id
        JOIN anime a ON s.anime_id = a.id
        JOIN scrapers sc ON s.provider_name = sc.provider_name
        LEFT JOIN anime_metadata m ON a.id = m.anime_id
        LEFT JOIN anime_aliases al ON a.id = al.anime_id
        WHERE {{title_condition}} {episode_condition} {season_condition}
        ORDER BY LENGTH(a.title) ASC, sc.display_order ASC
    """

    async with pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cursor:
            # 1. Try FULLTEXT search
            sanitized_for_ft = re.sub(r'[+\-><()~*@"]', ' ', clean_title).strip()
            if not sanitized_for_ft:
                logging.info(f"Skipping FULLTEXT search for '{clean_title}' because it contains only operators/stopwords.")
                results = []
            else:
                query_ft = query_template.format(title_condition="MATCH(a.title) AGAINST(%s IN BOOLEAN MODE)")
                await cursor.execute(query_ft, tuple([sanitized_for_ft + '*'] + params_episode + params_season))
                results = await cursor.fetchall()
            if results:
                return results

            # 2. Fallback to LIKE search on main title and all aliases
            logging.info(f"FULLTEXT search for '{clean_title}' yielded no results, falling back to LIKE search including aliases.")
            
            normalized_like_title = f"%{clean_title.replace('：', ':').replace(' ', '')}%"
            
            like_conditions = [
                "REPLACE(REPLACE(a.title, '：', ':'), ' ', '') LIKE %s",
                "REPLACE(REPLACE(al.name_en, '：', ':'), ' ', '') LIKE %s",
                "REPLACE(REPLACE(al.name_jp, '：', ':'), ' ', '') LIKE %s",
                "REPLACE(REPLACE(al.name_romaji, '：', ':'), ' ', '') LIKE %s",
                "REPLACE(REPLACE(al.alias_cn_1, '：', ':'), ' ', '') LIKE %s",
                "REPLACE(REPLACE(al.alias_cn_2, '：', ':'), ' ', '') LIKE %s",
                "REPLACE(REPLACE(al.alias_cn_3, '：', ':'), ' ', '') LIKE %s",
            ]
            
            title_condition_like = f"({' OR '.join(like_conditions)})"
            query_like = query_template.format(title_condition=title_condition_like)
            
            like_params = [normalized_like_title] * len(like_conditions)
            await cursor.execute(query_like, tuple(like_params + params_episode + params_season))
            return await cursor.fetchall()

async def get_episode_indices_by_anime_title(pool: aiomysql.Pool, title: str) -> List[int]:
    """获取指定标题的作品已存在的所有分集序号。"""
    async with pool.acquire() as conn:
        async with conn.cursor() as cursor:
            # This query finds the anime by title, then joins to get all its episodes' indices.
            # Using DISTINCT to avoid duplicates if multiple sources have the same episode index.
            query = """
                SELECT DISTINCT e.episode_index
                FROM episode e
                JOIN anime_sources s ON e.source_id = s.id
                JOIN anime a ON s.anime_id = a.id
                WHERE a.title = %s
            """
            await cursor.execute(query, (title,))
            return [row[0] for row in await cursor.fetchall()]

async def find_favorited_source_for_anime(pool: aiomysql.Pool, title: str, season: int) -> Optional[Dict[str, Any]]:
    """
    通过标题和季度查找已存在于库中且被标记为“精确”的数据源。
    """
    query = """
        SELECT
            s.provider_name,
            s.media_id,
            a.id as anime_id,
            a.title as anime_title,
            a.type as media_type,
            a.image_url
        FROM anime a
        JOIN anime_sources s ON a.id = s.anime_id
        WHERE a.title = %s AND a.season = %s AND s.is_favorited = TRUE
        LIMIT 1
    """
    async with pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cursor:
            # 1. 尝试精确匹配标题和季度
            await cursor.execute(query, (title, season))
            result = await cursor.fetchone()
            if result:
                return result
            # 2. 如果精确匹配失败，可以添加更宽松的模糊匹配逻辑（如果需要）
            # ...
    return None

async def search_animes_for_dandan(pool: aiomysql.Pool, keyword: str) -> List[Dict[str, Any]]:
    """
    在本地库中通过番剧标题搜索匹配的番剧，用于 /search/anime 接口。
    """
    clean_title = keyword.strip()
    if not clean_title:
        return []

    query_template = """
        SELECT
            a.id AS animeId,
            a.title AS animeTitle,
            a.type,
            a.image_url AS imageUrl,
            a.created_at AS startDate,
            (SELECT COUNT(DISTINCT e_count.id) FROM anime_sources s_count JOIN episode e_count ON s_count.id = e_count.source_id WHERE s_count.anime_id = a.id) as episodeCount,
            m.bangumi_id AS bangumiId
        FROM anime a
        LEFT JOIN anime_aliases al ON a.id = al.anime_id
        LEFT JOIN anime_metadata m ON a.id = m.anime_id
        WHERE {title_condition}
        ORDER BY a.id
    """

    async with pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cursor:
            # 1. Try FULLTEXT search
            sanitized_for_ft = re.sub(r'[+\-><()~*@"]', ' ', clean_title).strip()
            if not sanitized_for_ft:
                logging.info(f"Skipping FULLTEXT search for '{clean_title}' because it contains only operators/stopwords.")
                results = []
            else:
                query_ft = query_template.format(title_condition="MATCH(a.title) AGAINST(%s IN BOOLEAN MODE)")
                await cursor.execute(query_ft, (sanitized_for_ft + '*',))
                results = await cursor.fetchall()
            if results:
                return results

            # 2. Fallback to LIKE search
            normalized_like_title = f"%{clean_title.replace('：', ':').replace(' ', '')}%"
            like_conditions = [
                "REPLACE(REPLACE(a.title, '：', ':'), ' ', '') LIKE %s",
                "REPLACE(REPLACE(al.name_en, '：', ':'), ' ', '') LIKE %s",
                "REPLACE(REPLACE(al.name_jp, '：', ':'), ' ', '') LIKE %s",
                "REPLACE(REPLACE(al.name_romaji, '：', ':'), ' ', '') LIKE %s",
                "REPLACE(REPLACE(al.alias_cn_1, '：', ':'), ' ', '') LIKE %s",
                "REPLACE(REPLACE(al.alias_cn_2, '：', ':'), ' ', '') LIKE %s",
                "REPLACE(REPLACE(al.alias_cn_3, '：', ':'), ' ', '') LIKE %s",
            ]
            title_condition_like = f"({' OR '.join(like_conditions)})"
            query_like = query_template.format(title_condition=title_condition_like)
            
            like_params = [normalized_like_title] * len(like_conditions)
            await cursor.execute(query_like, tuple(like_params))
            return await cursor.fetchall()

async def find_animes_for_matching(pool: aiomysql.Pool, title: str) -> List[Dict[str, Any]]:
    """
    为匹配流程查找可能的番剧，并返回其核心ID以供TMDB映射使用。
    此搜索是特意设计得比较宽泛的。
    """
    query_template = """
        SELECT DISTINCT
            a.id as anime_id,
            m.tmdb_id,
            m.tmdb_episode_group_id,
            a.title
        FROM anime a
        LEFT JOIN anime_metadata m ON a.id = m.anime_id
        LEFT JOIN anime_aliases al ON a.id = al.anime_id
        WHERE {title_condition}
        ORDER BY LENGTH(a.title) ASC
        LIMIT 5;
    """
    async with pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cursor:
            # 回退到对主标题和所有别名进行LIKE搜索
            normalized_like_title = f"%{title.replace('：', ':').replace(' ', '')}%"
            like_conditions = [
                "REPLACE(REPLACE(a.title, '：', ':'), ' ', '') LIKE %s",
                "REPLACE(REPLACE(al.name_en, '：', ':'), ' ', '') LIKE %s",
                "REPLACE(REPLACE(al.name_jp, '：', ':'), ' ', '') LIKE %s",
                "REPLACE(REPLACE(al.name_romaji, '：', ':'), ' ', '') LIKE %s",
                "REPLACE(REPLACE(al.alias_cn_1, '：', ':'), ' ', '') LIKE %s",
                "REPLACE(REPLACE(al.alias_cn_2, '：', ':'), ' ', '') LIKE %s",
                "REPLACE(REPLACE(al.alias_cn_3, '：', ':'), ' ', '') LIKE %s",
            ]
            title_condition_like = f"({' OR '.join(like_conditions)})"
            query_like = query_template.format(title_condition=title_condition_like)
            like_params = [normalized_like_title] * len(like_conditions)
            await cursor.execute(query_like, tuple(like_params))
            return await cursor.fetchall()

async def find_episode_via_tmdb_mapping(
    pool: aiomysql.Pool, tmdb_id: str, group_id: str, custom_season: Optional[int], custom_episode: int
) -> List[Dict[str, Any]]:
    """通过TMDB映射表查找本地分集。可以根据自定义季/集或绝对集数进行匹配。"""
    base_query = """
        SELECT
            a.id AS animeId, a.title AS animeTitle, a.type, a.image_url AS imageUrl, a.created_at AS startDate,
            e.id AS episodeId, e.title AS episodeTitle, sc.display_order, s.is_favorited AS isFavorited,
            (SELECT COUNT(DISTINCT e_count.id) FROM anime_sources s_count JOIN episode e_count ON s_count.id = e_count.source_id WHERE s_count.anime_id = a.id) as totalEpisodeCount,
            m.bangumi_id AS bangumiId
        FROM tmdb_episode_mapping tm
        JOIN anime_metadata am ON tm.tmdb_tv_id = am.tmdb_id AND tm.tmdb_episode_group_id = am.tmdb_episode_group_id
        JOIN anime a ON am.anime_id = a.id
        JOIN anime_sources s ON a.id = s.anime_id
        JOIN episode e ON s.id = e.source_id AND e.episode_index = tm.absolute_episode_number
        JOIN scrapers sc ON s.provider_name = sc.provider_name
        LEFT JOIN anime_metadata m ON a.id = m.anime_id
        WHERE tm.tmdb_tv_id = %s AND tm.tmdb_episode_group_id = %s
    """
    params = [tmdb_id, group_id]
    if custom_season is not None:
        base_query += " AND tm.custom_season_number = %s AND tm.custom_episode_number = %s"
        params.extend([custom_season, custom_episode])
    else:
        base_query += " AND tm.absolute_episode_number = %s"
        params.append(custom_episode)
    base_query += " ORDER BY s.is_favorited DESC, sc.display_order ASC"
    async with pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cursor:
            await cursor.execute(base_query, tuple(params))
            return await cursor.fetchall()

async def get_anime_details_for_dandan(pool: aiomysql.Pool, anime_id: int) -> Optional[Dict[str, Any]]:
    """获取番剧的详细信息及其所有分集，用于dandanplay API。"""
    async with pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cursor:
            # 1. 获取番剧基本信息
            await cursor.execute("""
                SELECT
                    a.id AS animeId,
                    a.title AS animeTitle,
                    a.type,
                    a.image_url AS imageUrl,
                    a.created_at AS startDate,
                    a.source_url AS bangumiUrl,
                    (SELECT COUNT(DISTINCT e_count.id) FROM anime_sources s_count JOIN episode e_count ON s_count.id = e_count.source_id WHERE s_count.anime_id = a.id) as episodeCount,
                    m.bangumi_id AS bangumiId
                FROM anime a
                LEFT JOIN anime_metadata m ON a.id = m.anime_id
                WHERE a.id = %s
            """, (anime_id,))
            anime_details = await cursor.fetchone()

            if not anime_details:
                return None

            episodes = []
            # 2. 根据番剧类型决定如何获取“分集”列表
            if anime_details['type'] == 'movie':
                # 对于电影，我们将每个数据源视为一个“分集”，并使用搜索源的顺序作为集数
                await cursor.execute("""
                    SELECT
                        e.id AS episodeId,
                        CONCAT(s.provider_name, ' 源') AS episodeTitle,
                        sc.display_order AS episodeNumber
                    FROM anime_sources s
                    JOIN episode e ON s.id = e.source_id
                    JOIN scrapers sc ON s.provider_name = sc.provider_name
                    WHERE s.anime_id = %s
                    ORDER BY sc.display_order ASC
                """, (anime_id,))
                episodes = await cursor.fetchall()
            else:
                # 对于电视剧，正常获取分集列表
                await cursor.execute("""
                    SELECT e.id AS episodeId, e.title AS episodeTitle, e.episode_index AS episodeNumber
                    FROM episode e JOIN anime_sources s ON e.source_id = s.id
                    WHERE s.anime_id = %s ORDER BY e.episode_index ASC
                """, (anime_id,))
                episodes = await cursor.fetchall()

            # 3. 返回整合后的数据
            return {"anime": anime_details, "episodes": episodes}

async def get_anime_id_by_bangumi_id(pool: aiomysql.Pool, bangumi_id: str) -> Optional[int]:
    """通过 bangumi_id 查找 anime_id。"""
    async with pool.acquire() as conn:
        async with conn.cursor() as cursor:
            await cursor.execute("SELECT anime_id FROM anime_metadata WHERE bangumi_id = %s", (bangumi_id,))
            result = await cursor.fetchone()
            return result[0] if result else None

async def get_user_by_id(pool: aiomysql.Pool, user_id: int) -> Optional[Dict[str, Any]]:
    """通过ID查找用户"""
    async with pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cursor:
            # 只选择必要的字段，避免暴露密码哈希等
            query = "SELECT id, username FROM users WHERE id = %s"
            await cursor.execute(query, (user_id,))
            return await cursor.fetchone()

async def find_anime_by_title(pool: aiomysql.Pool, title: str) -> Optional[Dict[str, Any]]:
    """通过标题精确查找番剧"""
    async with pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cursor:
            query = "SELECT id, title, type FROM anime WHERE title = %s"
            await cursor.execute(query, (title,))
            return await cursor.fetchone()


async def find_episode(pool: aiomysql.Pool, source_id: int, episode_index: int) -> Optional[Dict[str, Any]]:
    """查找特定源的特定分集"""
    async with pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cursor:
            query = "SELECT id, title FROM episode WHERE source_id = %s AND episode_index = %s"
            await cursor.execute(query, (source_id, episode_index))
            return await cursor.fetchone()


async def check_episode_exists(pool: aiomysql.Pool, episode_id: int) -> bool:
    """检查指定ID的分集是否存在"""
    async with pool.acquire() as conn:
        async with conn.cursor() as cursor:
            query = "SELECT 1 FROM episode WHERE id = %s LIMIT 1"
            await cursor.execute(query, (episode_id,))
            result = await cursor.fetchone()
            return result is not None


async def fetch_comments(pool: aiomysql.Pool, episode_id: int) -> List[Dict[str, Any]]:
    """获取指定分集的所有弹幕"""
    async with pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cursor:
            query = "SELECT id as cid, p, m FROM comment WHERE episode_id = %s"
            await cursor.execute(query, (episode_id,))
            return await cursor.fetchall()

async def get_existing_comment_cids(pool: aiomysql.Pool, episode_id: int) -> set:
    """获取指定分集已存在的所有弹幕 cid。"""
    async with pool.acquire() as conn:
        async with conn.cursor() as cursor:
            # cid 在数据库中是 VARCHAR，所以这里获取的是字符串集合
            await cursor.execute("SELECT cid FROM comment WHERE episode_id = %s", (episode_id,))
            return {row[0] for row in await cursor.fetchall()}

async def get_or_create_anime(pool: aiomysql.Pool, title: str, media_type: str, season: int, image_url: Optional[str], local_image_path: Optional[str]) -> int:
    """通过标题查找番剧，如果不存在则创建。如果存在但缺少海报，则更新海报。返回其ID。"""
    async with pool.acquire() as conn:
        async with conn.cursor() as cursor:
            # 1. 检查番剧是否已存在
            await cursor.execute("SELECT id, image_url, local_image_path FROM anime WHERE title = %s AND season = %s", (title, season))
            result = await cursor.fetchone()
            if result:
                anime_id = result[0]
                existing_image_url = result[1]
                existing_local_path = result[2]
                
                updates, params = [], []
                # 如果番剧已存在，但没有远程海报URL，而这次导入提供了，则更新它
                if not existing_image_url and image_url:
                    updates.append("image_url = %s")
                    params.append(image_url)
                # 如果番剧已存在，但没有本地海报路径，而这次导入提供了，则更新它
                if not existing_local_path and local_image_path:
                    updates.append("local_image_path = %s")
                    params.append(local_image_path)
                if updates:
                    params.append(anime_id)
                    await cursor.execute(f"UPDATE anime SET {', '.join(updates)} WHERE id = %s", tuple(params))
                return anime_id
            
            # 2. 番剧不存在，在事务中创建新记录
            try:
                await conn.begin()
                # 2.1 插入主表
                await cursor.execute(
                    "INSERT INTO anime (title, type, season, image_url, local_image_path, created_at) VALUES (%s, %s, %s, %s, %s, %s)",
                    (title, media_type, season, image_url, local_image_path, datetime.now())
                )
                anime_id = cursor.lastrowid
                # 2.2 插入元数据表 (使用 INSERT IGNORE 避免因孤儿数据导致重复键警告)
                await cursor.execute("INSERT IGNORE INTO anime_metadata (anime_id) VALUES (%s)", (anime_id, ))
                # 2.3 插入别名表 (同样使用 INSERT IGNORE)
                await cursor.execute("INSERT IGNORE INTO anime_aliases (anime_id) VALUES (%s)", (anime_id, ))
                await conn.commit()
                return anime_id
            except Exception as e:
                await conn.rollback()
                logging.error(f"创建番剧 '{title}' 时发生数据库错误: {e}", exc_info=True)
                raise

async def link_source_to_anime(pool: aiomysql.Pool, anime_id: int, provider: str, media_id: str) -> int:
    """将一个外部数据源链接到一个番剧，如果链接已存在则直接返回，否则创建新链接。"""
    async with pool.acquire() as conn:
        async with conn.cursor() as cursor:
            # 使用 INSERT IGNORE 来安全地插入，如果唯一键冲突则什么都不做
            await cursor.execute(
                "INSERT IGNORE INTO anime_sources (anime_id, provider_name, media_id, created_at) VALUES (%s, %s, %s, %s)",
                (anime_id, provider, media_id, datetime.now())
            )
            # 获取刚刚插入或已存在的源ID
            await cursor.execute("SELECT id FROM anime_sources WHERE anime_id = %s AND provider_name = %s AND media_id = %s", (anime_id, provider, media_id))
            source = await cursor.fetchone()
            return source[0]

async def create_episode_if_not_exists(pool: aiomysql.Pool, anime_id: int, source_id: int, episode_index: int, title: str, url: Optional[str], provider_episode_id: str) -> int:
    """如果分集不存在则创建，并返回其确定性的ID。"""
    async with pool.acquire() as conn:
        async with conn.cursor() as cursor:
            # 检查是否存在
            await cursor.execute("SELECT id FROM episode WHERE source_id = %s AND episode_index = %s", (source_id, episode_index))
            result = await cursor.fetchone()
            if result:
                return int(result[0])

            # 获取此源在当前作品下的索引 (1-based)
            await cursor.execute("SELECT id FROM anime_sources WHERE anime_id = %s ORDER BY id ASC", (anime_id,))
            source_ids = [row[0] for row in await cursor.fetchall()]
            try:
                source_order = source_ids.index(source_id) + 1
            except ValueError:
                # This should not happen in a normal flow
                raise ValueError(f"Source ID {source_id} does not belong to Anime ID {anime_id}")

            # 生成新的确定性ID
            # 格式: 25{anime_id:06d}{source_order:02d}{episode_index:04d}
            new_episode_id_str = f"25{anime_id:06d}{source_order:02d}{episode_index:04d}"
            new_episode_id = int(new_episode_id_str)

            await cursor.execute(
                "INSERT INTO episode (id, source_id, episode_index, provider_episode_id, title, source_url, fetched_at) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (new_episode_id, source_id, episode_index, provider_episode_id, title, url, datetime.now())
            )
            return new_episode_id


async def bulk_insert_comments(pool: aiomysql.Pool, episode_id: int, comments: List[Dict[str, Any]]) -> int:
    """批量插入弹幕，利用 INSERT IGNORE 忽略重复弹幕"""
    if not comments:
        return 0
    async with pool.acquire() as conn:
        async with conn.cursor() as cursor:
            query = "INSERT IGNORE INTO comment (episode_id, cid, p, m, t) VALUES (%s, %s, %s, %s, %s)"
            # 准备数据
            data_to_insert = [
                (episode_id, c['cid'], c['p'], c['m'], c['t']) for c in comments
            ]
            affected_rows = await cursor.executemany(query, data_to_insert)
            # 如果成功插入了新弹幕，则更新分集的弹幕计数
            if affected_rows > 0:
                await cursor.execute("UPDATE episode SET comment_count = comment_count + %s WHERE id = %s", (affected_rows, episode_id))
            return affected_rows


async def get_user_by_username(pool: aiomysql.Pool, username: str) -> Optional[Dict[str, Any]]:
    """通过用户名查找用户"""
    async with pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cursor:
            query = "SELECT id, username, hashed_password, token FROM users WHERE username = %s"
            await cursor.execute(query, (username,))
            return await cursor.fetchone()


async def create_user(pool: aiomysql.Pool, user: models.UserCreate) -> int:
    """创建新用户"""
    hashed_password = security.get_password_hash(user.password)
    async with pool.acquire() as conn:
        async with conn.cursor() as cursor:
            query = "INSERT INTO users (username, hashed_password, created_at) VALUES (%s, %s, %s)"
            await cursor.execute(query, (user.username, hashed_password, datetime.now()))
            return cursor.lastrowid


async def update_user_password(pool: aiomysql.Pool, username: str, new_hashed_password: str) -> None:
    """更新用户的密码"""
    async with pool.acquire() as conn:
        async with conn.cursor() as cursor:
            query = "UPDATE users SET hashed_password = %s WHERE username = %s"
            await cursor.execute(query, (new_hashed_password, username))


async def update_user_login_info(pool: aiomysql.Pool, username: str, token: str):
    """更新用户的最后登录时间和当前令牌"""
    async with pool.acquire() as conn:
        async with conn.cursor() as cursor:
            # 使用 NOW() 获取数据库服务器的当前时间
            query = "UPDATE users SET token = %s, token_update = NOW() WHERE username = %s"
            await cursor.execute(query, (token, username))

async def get_anime_source_info(pool: aiomysql.Pool, source_id: int) -> Optional[Dict[str, Any]]:
    """获取指定源ID的详细信息及其关联的作品信息。"""
    async with pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cursor:
            query = """
                SELECT 
                    s.id as source_id, 
                    s.anime_id, 
                    s.provider_name, 
                    s.media_id, 
                    a.title, 
                    a.type,
                    a.season,
                    m.tmdb_id
                FROM anime_sources s 
                JOIN anime a ON s.anime_id = a.id 
                LEFT JOIN anime_metadata m ON a.id = m.anime_id
                WHERE s.id = %s
            """
            await cursor.execute(query, (source_id,))
            return await cursor.fetchone()

async def get_anime_sources(pool: aiomysql.Pool, anime_id: int) -> List[Dict[str, Any]]:
    """获取指定作品的所有关联数据源。"""
    async with pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cursor:
            query = "SELECT id as source_id, provider_name, media_id, is_favorited, incremental_refresh_enabled, created_at FROM anime_sources WHERE anime_id = %s ORDER BY created_at ASC"
            await cursor.execute(query, (anime_id,))
            return await cursor.fetchall()

async def get_episodes_for_source(pool: aiomysql.Pool, source_id: int) -> List[Dict[str, Any]]:
    """获取指定数据源的所有分集信息。"""
    async with pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cursor:
            query = "SELECT id, title, episode_index, source_url, fetched_at, comment_count FROM episode WHERE source_id = %s ORDER BY episode_index ASC"
            await cursor.execute(query, (source_id,))
            return await cursor.fetchall()

async def get_episode_for_refresh(pool: aiomysql.Pool, episode_id: int) -> Optional[Dict[str, Any]]:
    """获取分集的基本信息，用于刷新任务。"""
    async with pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cursor:
            query = "SELECT id, title FROM episode WHERE id = %s"
            await cursor.execute(query, (episode_id,))
            return await cursor.fetchone()

async def get_episode_provider_info(pool: aiomysql.Pool, episode_id: int) -> Optional[Dict[str, Any]]:
    """获取分集的原始提供方信息 (provider_name, provider_episode_id)"""
    async with pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cursor:
            query = """
                SELECT
                    s.provider_name,
                    e.provider_episode_id
                FROM episode e
                JOIN anime_sources s ON e.source_id = s.id
                WHERE e.id = %s
            """
            await cursor.execute(query, (episode_id,))
            return await cursor.fetchone()

async def clear_source_data(pool: aiomysql.Pool, source_id: int):
    """清空指定源的所有分集和弹幕，用于刷新。"""
    async with pool.acquire() as conn:
        async with conn.cursor() as cursor:
            await cursor.execute("SELECT id FROM episode WHERE source_id = %s", (source_id,))
            episode_ids = [row[0] for row in await cursor.fetchall()]
            if episode_ids:
                format_strings = ','.join(['%s'] * len(episode_ids))
                await cursor.execute(f"DELETE FROM comment WHERE episode_id IN ({format_strings})", tuple(episode_ids))
                # 在此场景下，episode 很快会被删除，所以无需更新 comment_count
                await cursor.execute(f"DELETE FROM episode WHERE id IN ({format_strings})", tuple(episode_ids))

async def clear_episode_comments(pool: aiomysql.Pool, episode_id: int):
    """清空指定分集的所有弹幕"""
    async with pool.acquire() as conn:
        async with conn.cursor() as cursor:
            await cursor.execute("DELETE FROM comment WHERE episode_id = %s", (episode_id,))
            # 在只清空弹幕（用于刷新单个分集）的场景下，必须重置计数器
            await cursor.execute("UPDATE episode SET comment_count = 0 WHERE id = %s", (episode_id,))

async def get_anime_full_details(pool: aiomysql.Pool, anime_id: int) -> Optional[Dict[str, Any]]:
    """获取番剧的完整详细信息，包括元数据和别名。"""
    async with pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cursor:
            query = """
                SELECT
                    a.id as anime_id,
                    a.title,
                    a.type,
                    a.season,
                    a.local_image_path,
                    a.episode_count,
                    a.image_url,
                    m.tmdb_id,
                    m.tmdb_episode_group_id,
                    m.bangumi_id,
                    m.tvdb_id,
                    m.douban_id,
                    m.imdb_id,
                    al.name_en,
                    al.name_jp,
                    al.name_romaji,
                    al.alias_cn_1,
                    al.alias_cn_2,
                    al.alias_cn_3
                FROM anime a
                LEFT JOIN anime_metadata m ON a.id = m.anime_id
                LEFT JOIN anime_aliases al ON a.id = al.anime_id
                WHERE a.id = %s
            """
            await cursor.execute(query, (anime_id,))
            return await cursor.fetchone()

async def update_anime_details(pool: aiomysql.Pool, anime_id: int, update_data: models.AnimeDetailUpdate) -> bool:
    """在事务中更新番剧的核心信息、元数据和别名。"""
    async with pool.acquire() as conn:
        async with conn.cursor() as cursor:
            # 检查 anime 记录是否存在
            await cursor.execute("SELECT id FROM anime WHERE id = %s", (anime_id,))
            if not await cursor.fetchone():
                return False
            try:
                await conn.begin()
                
                # 1. 更新 anime 表
                await cursor.execute(
                    "UPDATE anime SET title = %s, type = %s, season = %s, episode_count = %s, image_url = %s WHERE id = %s",
                    (update_data.title, update_data.type, update_data.season, update_data.episode_count, update_data.image_url, anime_id)
                )
                
                # 2. 更新 anime_metadata 表 (使用新的 AS alias 语法)
                await cursor.execute("""
                    INSERT INTO anime_metadata (anime_id, tmdb_id, tmdb_episode_group_id, bangumi_id, tvdb_id, douban_id, imdb_id)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE
                        tmdb_id = VALUES(tmdb_id), tmdb_episode_group_id = VALUES(tmdb_episode_group_id),
                        bangumi_id = VALUES(bangumi_id), tvdb_id = VALUES(tvdb_id),
                        douban_id = VALUES(douban_id), imdb_id = VALUES(imdb_id)
                """, (anime_id, update_data.tmdb_id, update_data.tmdb_episode_group_id, update_data.bangumi_id, update_data.tvdb_id, update_data.douban_id, update_data.imdb_id))

                # 3. 更新 anime_aliases 表 (使用新的 AS alias 语法)
                await cursor.execute("""
                    INSERT INTO anime_aliases (anime_id, name_en, name_jp, name_romaji, alias_cn_1, alias_cn_2, alias_cn_3)
                    VALUES (%s, %s, %s, %s, %s, %s, %s) ON DUPLICATE KEY UPDATE
                        name_en = VALUES(name_en), name_jp = VALUES(name_jp),
                        name_romaji = VALUES(name_romaji), alias_cn_1 = VALUES(alias_cn_1),
                        alias_cn_2 = VALUES(alias_cn_2), alias_cn_3 = VALUES(alias_cn_3)
                """, (
                    anime_id, update_data.name_en, update_data.name_jp, update_data.name_romaji,
                    update_data.alias_cn_1, update_data.alias_cn_2, update_data.alias_cn_3
                ))

                await conn.commit()
                return True
            except Exception as e:
                await conn.rollback()
                logging.getLogger(__name__).error(f"更新番剧详情 (ID: {anime_id}) 时出错: {e}", exc_info=True)
                return False

async def save_tmdb_episode_group_mappings(
    pool: aiomysql.Pool,
    tmdb_tv_id: int,
    group_id: str,
    group_details: models.TMDBEpisodeGroupDetails
):
    """
    在事务中保存TMDB剧集组的季度和分集映射。
    会先删除该剧集组的旧映射，再插入新映射。
    """
    async with pool.acquire() as conn:
        async with conn.cursor() as cursor:
            try:
                await conn.begin()

                # 1. 删除旧映射
                await cursor.execute(
                    "DELETE FROM tmdb_episode_mapping WHERE tmdb_episode_group_id = %s", (group_id,)
                )

                # 2. 准备并插入新映射
                mappings_to_insert = []

                # 按 order 字段对剧集组（季度）进行排序
                sorted_groups = sorted(group_details.groups, key=lambda g: g.order)

                for custom_season_group in sorted_groups:
                    if not custom_season_group.episodes:
                        continue
                    
                    # 使用 enumerate 来获取分集在当前自定义季度中的索引
                    for custom_episode_index, episode in enumerate(custom_season_group.episodes):
                        mappings_to_insert.append(
                            (
                                tmdb_tv_id,
                                group_id,
                                episode.id,
                                episode.season_number,
                                episode.episode_number,
                                custom_season_group.order, # 自定义季度号
                                custom_episode_index + 1,  # 自定义本季集数
                                episode.order + 1          # 绝对集数
                            )
                        )
                
                # 3. 批量插入
                if mappings_to_insert:
                    query = """
                        INSERT INTO tmdb_episode_mapping (tmdb_tv_id, tmdb_episode_group_id, tmdb_episode_id, tmdb_season_number, tmdb_episode_number, custom_season_number, custom_episode_number, absolute_episode_number)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    """
                    await cursor.executemany(query, mappings_to_insert)

                await conn.commit()
                logging.info(f"成功为剧集组 {group_id} 保存了 {len(mappings_to_insert)} 条分集映射。")

            except Exception as e:
                await conn.rollback()
                logging.error(f"保存TMDB映射时出错 (group_id={group_id}): {e}", exc_info=True)
                raise

async def delete_anime_source(pool: aiomysql.Pool, source_id: int, conn: Optional[aiomysql.Connection] = None) -> bool:
    """
    删除一个数据源及其所有关联的分集和弹幕。
    如果提供了 conn 参数，则在该连接上执行，不进行事务管理。
    """
    _conn = conn or await pool.acquire()
    try:
        async with _conn.cursor() as cursor:
            if not conn: await _conn.begin()

            await cursor.execute("SELECT 1 FROM anime_sources WHERE id = %s", (source_id,))
            if not await cursor.fetchone():
                if not conn: await _conn.rollback()
                return False

            await cursor.execute("SELECT id FROM episode WHERE source_id = %s", (source_id,))
            episode_ids = [row[0] for row in await cursor.fetchall()]

            if episode_ids:
                format_strings = ','.join(['%s'] * len(episode_ids))
                await cursor.execute(f"DELETE FROM comment WHERE episode_id IN ({format_strings})", tuple(episode_ids))
                await cursor.execute(f"DELETE FROM episode WHERE id IN ({format_strings})", tuple(episode_ids))

            await cursor.execute("DELETE FROM anime_sources WHERE id = %s", (source_id,))
            
            if not conn: await _conn.commit()
            return True
    except Exception as e:
        if not conn: await _conn.rollback()
        logging.error(f"删除源 (ID: {source_id}) 时发生错误: {e}", exc_info=True)
        return False
    finally:
        if not conn: pool.release(_conn)

async def delete_episode(pool: aiomysql.Pool, episode_id: int, conn: Optional[aiomysql.Connection] = None) -> bool:
    """删除一个分集及其所有关联的弹幕。"""
    _conn = conn or await pool.acquire()
    try:
        async with _conn.cursor() as cursor:
            if not conn: await _conn.begin()
            await cursor.execute("DELETE FROM comment WHERE episode_id = %s", (episode_id,))
            affected_rows = await cursor.execute("DELETE FROM episode WHERE id = %s", (episode_id,))
            if not conn: await _conn.commit()
            return affected_rows > 0
    except Exception as e:
        if not conn: await _conn.rollback()
        logging.error(f"删除分集 (ID: {episode_id}) 时发生错误: {e}", exc_info=True)
        return False
    finally:
        if not conn: pool.release(_conn)

async def reassociate_anime_sources(pool: aiomysql.Pool, source_anime_id: int, target_anime_id: int) -> bool:
    """将一个作品的所有数据源移动到另一个作品，并删除原作品。如果目标作品已存在相同源，则会删除源作品的重复源及其数据。"""
    async with pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cursor:
            try:
                await conn.begin()
                await cursor.execute("SELECT 1 FROM anime WHERE id = %s", (source_anime_id,))
                if not await cursor.fetchone(): return False
                await cursor.execute("SELECT 1 FROM anime WHERE id = %s", (target_anime_id,))
                if not await cursor.fetchone(): return False

                await cursor.execute("SELECT id, provider_name, media_id FROM anime_sources WHERE anime_id = %s", (source_anime_id,))
                source_sources = await cursor.fetchall()
                for src in source_sources:
                    try:
                        await cursor.execute("UPDATE anime_sources SET anime_id = %s WHERE id = %s", (target_anime_id, src['id']))
                    except aiomysql.IntegrityError:
                        logging.info(f"处理重复源: 正在删除来自源作品 {source_anime_id} 的 {src['provider_name']} (media_id: {src['media_id']})")
                        await delete_anime_source(pool, src['id'], conn)

                await cursor.execute("DELETE FROM anime WHERE id = %s", (source_anime_id,))
                await conn.commit()
                return True
            except Exception as e:
                await conn.rollback()
                logging.error(f"重新关联源从 {source_anime_id} 到 {target_anime_id} 时出错: {e}", exc_info=True)
                return False

async def update_episode_info(pool: aiomysql.Pool, episode_id: int, update_data: models.EpisodeInfoUpdate) -> bool:
    """
    更新分集信息。
    此函数具有弹性：如果原始 episode_id 不再存在（可能由于刷新任务），
    它会尝试使用 source_id 和 original_episode_index 找到新的 episode_id 并更新它。
    """
    async with pool.acquire() as conn:
        async with conn.cursor() as cursor:
            await conn.begin()
            try:
                # 步骤 1: 确定要更新的实际 episode_id
                actual_episode_id = None
                
                # 1a: 尝试通过原始 ID 查找
                await cursor.execute("SELECT id FROM episode WHERE id = %s", (episode_id,))
                result = await cursor.fetchone()
                if result:
                    actual_episode_id = result[0]
                else:
                    # 1b: 如果找不到，尝试通过 source_id 和 original_index 查找
                    logging.warning(f"Episode ID {episode_id} not found for update. Attempting to find replacement via source_id={update_data.source_id} and original_index={update_data.original_episode_index}.")
                    await cursor.execute(
                        "SELECT id FROM episode WHERE source_id = %s AND episode_index = %s",
                        (update_data.source_id, update_data.original_episode_index)
                    )
                    result = await cursor.fetchone()
                    if result:
                        actual_episode_id = result[0]
                        logging.info(f"Found replacement episode with ID {actual_episode_id}. Proceeding with update.")
                
                if actual_episode_id is None:
                    await conn.rollback()
                    return False

                await cursor.execute("SELECT id FROM episode WHERE source_id = %s AND episode_index = %s AND id != %s", (update_data.source_id, update_data.episode_index, actual_episode_id))
                if await cursor.fetchone(): raise ValueError("该集数已存在，请使用其他集数。")

                await cursor.execute("UPDATE episode SET title = %s, episode_index = %s, source_url = %s WHERE id = %s", (update_data.title, update_data.episode_index, update_data.source_url, actual_episode_id))
                await conn.commit()
                return True
            except Exception as e:
                await conn.rollback()
                if isinstance(e, ValueError): raise
                logging.error(f"更新分集信息时发生数据库错误: {e}", exc_info=True)
                return False

async def sync_scrapers_to_db(pool: aiomysql.Pool, provider_names: List[str]):
    """将发现的搜索源同步到数据库，新的搜索源会被添加进去。"""
    if not provider_names:
        return
    async with pool.acquire() as conn:
        async with conn.cursor() as cursor:
            # 1. 获取所有已存在的搜索源
            await cursor.execute("SELECT provider_name FROM scrapers")
            existing_providers = {row[0] for row in await cursor.fetchall()}

            # 2. 找出新发现的搜索源
            new_providers = [name for name in provider_names if name not in existing_providers]

            if not new_providers:
                return

            # 3. 如果有新的搜索源，则插入它们
            await cursor.execute("SELECT MAX(display_order) FROM scrapers")
            max_order = (await cursor.fetchone())[0] or 0

            # 只插入新的搜索源
            query = "INSERT INTO scrapers (provider_name, display_order, use_proxy) VALUES (%s, %s, %s)"
            data_to_insert = [(name, max_order + i + 1, False) for i, name in enumerate(new_providers)]
            await cursor.executemany(query, data_to_insert)

async def get_all_scraper_settings(pool: aiomysql.Pool) -> List[Dict[str, Any]]:
    """获取所有搜索源的设置，按顺序排列。"""
    async with pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cursor:
            await cursor.execute("SELECT provider_name, is_enabled, display_order, use_proxy FROM scrapers ORDER BY display_order ASC")
            return await cursor.fetchall()

async def update_scrapers_settings(pool: aiomysql.Pool, settings: List[models.ScraperSetting]):
    """批量更新搜索源的设置。"""
    async with pool.acquire() as conn:
        async with conn.cursor() as cursor:
            query = "UPDATE scrapers SET is_enabled = %s, display_order = %s, use_proxy = %s WHERE provider_name = %s"
            data_to_update = [(s.is_enabled, s.display_order, s.use_proxy, s.provider_name) for s in settings]
            await cursor.executemany(query, data_to_update)

async def remove_stale_scrapers(pool: aiomysql.Pool, discovered_providers: List[str]):
    """
    从数据库中移除不再存在于文件系统中的搜索源。
    """
    # 如果由于某种原因发现列表为空，则不执行任何操作，以防意外清空整个表。
    if not discovered_providers:
        logging.warning("发现的搜索源列表为空，跳过清理过时源的操作。")
        return

    # 创建占位符字符串，例如: (%s, %s, %s)
    format_strings = ','.join(['%s'] * len(discovered_providers))
    query = f"DELETE FROM scrapers WHERE provider_name NOT IN ({format_strings})"
    params = tuple(discovered_providers)

    async with pool.acquire() as conn:
        async with conn.cursor() as cursor:
            deleted_rows = await cursor.execute(query, params)
            if deleted_rows > 0:
                logging.info(f"已从数据库中移除了 {deleted_rows} 个过时的搜索源。")

# --- Metadata Source Management ---

async def sync_metadata_sources_to_db(pool: aiomysql.Pool, provider_names: List[str]):
    """将发现的元数据源同步到数据库。"""
    if not provider_names:
        return
    async with pool.acquire() as conn:
        async with conn.cursor() as cursor:
            await cursor.execute("SELECT provider_name FROM metadata_sources")
            existing_providers = {row[0] for row in await cursor.fetchall()}
            new_providers = [name for name in provider_names if name not in existing_providers]
            if not new_providers:
                return
            await cursor.execute("SELECT MAX(display_order) FROM metadata_sources")
            max_order = (await cursor.fetchone())[0] or 0
            data_to_insert = []
            for i, name in enumerate(new_providers):
                is_aux_enabled = True if name == 'tmdb' else False
                data_to_insert.append((name, max_order + i + 1, is_aux_enabled, False))
            
            query = "INSERT INTO metadata_sources (provider_name, display_order, is_aux_search_enabled, use_proxy) VALUES (%s, %s, %s, %s)"
            await cursor.executemany(query, data_to_insert)

async def get_all_metadata_source_settings(pool: aiomysql.Pool) -> List[Dict[str, Any]]:
    """获取所有元数据源的设置。"""
    async with pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cursor:
            await cursor.execute("SELECT provider_name, is_enabled, is_aux_search_enabled, display_order, use_proxy FROM metadata_sources ORDER BY display_order ASC")
            return await cursor.fetchall()

async def update_metadata_sources_settings(pool: aiomysql.Pool, settings: List['models.MetadataSourceSettingUpdate']):
    """批量更新元数据源的设置。"""
    async with pool.acquire() as conn:
        async with conn.cursor() as cursor:
            query = "UPDATE metadata_sources SET is_aux_search_enabled = %s, display_order = %s, use_proxy = %s WHERE provider_name = %s"
            data_to_update = []
            for s in settings:
                is_aux_enabled = True if s.provider_name == 'tmdb' else s.is_aux_search_enabled
                data_to_update.append((is_aux_enabled, s.display_order, s.use_proxy, s.provider_name))
            await cursor.executemany(query, data_to_update)

async def update_episode_fetch_time(pool: aiomysql.Pool, episode_id: int):
    """更新分集的采集时间"""
    async with pool.acquire() as conn:
        async with conn.cursor() as cursor:
            await cursor.execute("UPDATE episode SET fetched_at = %s WHERE id = %s", (datetime.now(), episode_id))

# --- 数据库缓存服务 ---

async def update_metadata_if_empty(
    pool: aiomysql.Pool, anime_id: int, 
    tmdb_id: Optional[str], imdb_id: Optional[str], 
    tvdb_id: Optional[str], douban_id: Optional[str]
):
    """如果一个作品记录的元数据ID为空，则使用提供的新ID进行更新。"""
    async with pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cursor:
            # 1. 确保元数据行存在
            await cursor.execute("INSERT IGNORE INTO anime_metadata (anime_id) VALUES (%s)", (anime_id,))
            
            # 2. 获取当前元数据
            await cursor.execute("SELECT tmdb_id, imdb_id, tvdb_id, douban_id FROM anime_metadata WHERE anime_id = %s", (anime_id,))
            current = await cursor.fetchone()
            if not current: return

            # 3. 构建需要更新的字段
            updates, params = [], []
            if not current.get('tmdb_id') and tmdb_id: updates.append("tmdb_id = %s"); params.append(tmdb_id)
            if not current.get('imdb_id') and imdb_id: updates.append("imdb_id = %s"); params.append(imdb_id)
            if not current.get('tvdb_id') and tvdb_id: updates.append("tvdb_id = %s"); params.append(tvdb_id)
            if not current.get('douban_id') and douban_id: updates.append("douban_id = %s"); params.append(douban_id)

            # 4. 如果有需要更新的字段，则执行更新
            if updates:
                query = f"UPDATE anime_metadata SET {', '.join(updates)} WHERE anime_id = %s"
                params.append(anime_id)
                await cursor.execute(query, tuple(params))
                logging.info(f"为作品 ID {anime_id} 更新了 {len(updates)} 个元数据ID。")

async def check_source_exists_by_media_id(pool: aiomysql.Pool, provider: str, media_id: str) -> bool:
    """通过 provider 和 media_id 检查数据源是否已存在于任何番剧下。"""
    async with pool.acquire() as conn:
        async with conn.cursor() as cursor:
            await cursor.execute(
                "SELECT 1 FROM anime_sources WHERE provider_name = %s AND media_id = %s LIMIT 1",
                (provider, media_id)
            )
            return await cursor.fetchone() is not None

async def get_config_value(pool: aiomysql.Pool, key: str, default: str) -> str:
    """从数据库获取配置值。"""
    async with pool.acquire() as conn:
        async with conn.cursor() as cursor:
            await cursor.execute("SELECT config_value FROM config WHERE config_key = %s", (key,))
            result = await cursor.fetchone()
            return result[0] if result else default

async def get_cache(pool: aiomysql.Pool, key: str) -> Optional[Any]:
    """从数据库缓存中获取数据。"""
    async with pool.acquire() as conn:
        async with conn.cursor() as cursor:
            await cursor.execute("SELECT cache_value FROM cache_data WHERE cache_key = %s AND expires_at > NOW()", (key,))
            result = await cursor.fetchone()
            if result:
                try:
                    return json.loads(result[0])
                except json.JSONDecodeError:
                    return None # 缓存数据损坏
    return None

async def set_cache(pool: aiomysql.Pool, key: str, value: Any, ttl_seconds: int, provider: Optional[str] = None):
    """将数据存入数据库缓存。"""
    async with pool.acquire() as conn:
        async with conn.cursor() as cursor:
            json_value = json.dumps(value, ensure_ascii=False)
            query = """
                INSERT INTO cache_data (cache_provider, cache_key, cache_value, expires_at)
                VALUES (%s, %s, %s, NOW() + INTERVAL %s SECOND)
                ON DUPLICATE KEY UPDATE
                    cache_provider = VALUES(cache_provider),
                    cache_value = VALUES(cache_value),
                    expires_at = VALUES(expires_at)
            """
            await cursor.execute(query, (provider, key, json_value, ttl_seconds))

async def update_config_value(pool: aiomysql.Pool, key: str, value: str):
    """更新或插入一个配置项。"""
    async with pool.acquire() as conn:
        async with conn.cursor() as cursor:
            query = "INSERT INTO config (config_key, config_value) VALUES (%s, %s) ON DUPLICATE KEY UPDATE config_value = VALUES(config_value)"
            await cursor.execute(query, (key, value))

async def clear_expired_cache(pool: aiomysql.Pool):
    """从数据库中清除过期的缓存条目。"""
    async with pool.acquire() as conn:
        async with conn.cursor() as cursor:
            deleted_rows = await cursor.execute("DELETE FROM cache_data WHERE expires_at <= NOW()")
            if deleted_rows > 0:
                logging.getLogger(__name__).info(f"清除了 {deleted_rows} 条过期的数据库缓存。")

async def clear_expired_oauth_states(pool: aiomysql.Pool):
    """从数据库中清除过期的OAuth state条目。"""
    async with pool.acquire() as conn:
        async with conn.cursor() as cursor:
            deleted_rows = await cursor.execute("DELETE FROM oauth_states WHERE expires_at <= NOW()")
            if deleted_rows > 0:
                logging.getLogger(__name__).info(f"清除了 {deleted_rows} 条过期的OAuth states。")

async def clear_all_cache(pool: aiomysql.Pool) -> int:
    """从数据库中清除所有缓存条目。返回删除的行数。"""
    async with pool.acquire() as conn:
        async with conn.cursor() as cursor:
            # 使用 DELETE 并返回受影响的行数
            deleted_rows = await cursor.execute("DELETE FROM cache_data")
            if deleted_rows > 0:
                logging.getLogger(__name__).info(f"清除了所有 ({deleted_rows} 条) 数据库缓存。")
            return deleted_rows

async def delete_cache(pool: aiomysql.Pool, key: str) -> bool:
    """从数据库缓存中删除指定的键。"""
    async with pool.acquire() as conn:
        async with conn.cursor() as cursor:
            affected_rows = await cursor.execute("DELETE FROM cache_data WHERE cache_key = %s", (key,))
            return affected_rows > 0

async def update_episode_fetch_time(pool: aiomysql.Pool, episode_id: int):
    """更新分集的采集时间"""
    async with pool.acquire() as conn:
        async with conn.cursor() as cursor:
            await cursor.execute("UPDATE episode SET fetched_at = %s WHERE id = %s", (datetime.now(), episode_id))

# --- API Token 管理服务 ---

async def get_all_api_tokens(pool: aiomysql.Pool) -> List[Dict[str, Any]]:
    """获取所有 API Token。"""
    async with pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cursor:
            await cursor.execute("SELECT id, name, token, is_enabled, expires_at, created_at FROM api_tokens ORDER BY created_at DESC")
            return await cursor.fetchall()

async def get_api_token_by_id(pool: aiomysql.Pool, token_id: int) -> Optional[Dict[str, Any]]:
    """通过ID获取一个 API Token。"""
    async with pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cursor:
            await cursor.execute("SELECT id, name, token, is_enabled, expires_at, created_at FROM api_tokens WHERE id = %s", (token_id,))
            return await cursor.fetchone()

async def get_api_token_by_token_str(pool: aiomysql.Pool, token_str: str) -> Optional[Dict[str, Any]]:
    """通过token字符串获取一个 API Token。"""
    async with pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cursor:
            await cursor.execute("SELECT id, name, token, is_enabled, expires_at, created_at FROM api_tokens WHERE token = %s", (token_str,))
            return await cursor.fetchone()

async def create_api_token(pool: aiomysql.Pool, name: str, token: str, validity_period: str) -> int:
    """创建一个新的 API Token。"""
    expires_at = None
    if validity_period != "permanent":
        days = int(validity_period.replace('d', ''))
        expires_at = datetime.now(timezone.utc) + timedelta(days=days)

    async with pool.acquire() as conn:
        async with conn.cursor() as cursor:
            await cursor.execute(
                "INSERT INTO api_tokens (name, token, expires_at) VALUES (%s, %s, %s)",
                (name, token, expires_at)
            )
            return cursor.lastrowid

async def delete_api_token(pool: aiomysql.Pool, token_id: int) -> bool:
    """删除一个 API Token。"""
    async with pool.acquire() as conn:
        async with conn.cursor() as cursor:
            affected_rows = await cursor.execute("DELETE FROM api_tokens WHERE id = %s", (token_id,))
            return affected_rows > 0

async def toggle_api_token(pool: aiomysql.Pool, token_id: int) -> bool:
    """切换一个 API Token 的启用/禁用状态。"""
    async with pool.acquire() as conn:
        async with conn.cursor() as cursor:
            affected_rows = await cursor.execute("UPDATE api_tokens SET is_enabled = NOT is_enabled WHERE id = %s", (token_id,))
            return affected_rows > 0

async def validate_api_token(pool: aiomysql.Pool, token: str) -> Optional[Dict[str, Any]]:
    """验证一个 API Token 是否有效且已启用。如果有效，返回token信息，否则返回None。"""
    async with pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cursor:
            await cursor.execute(
                "SELECT id, expires_at FROM api_tokens WHERE token = %s AND is_enabled = TRUE",
                (token,)
            )
            token_info = await cursor.fetchone()
            if not token_info:
                return None
            
            if token_info['expires_at'] and token_info['expires_at'].replace(tzinfo=timezone.utc) < datetime.now(timezone.utc):
                return None # Token has expired

            return token_info

# --- UA Filter and Log Services ---

async def get_ua_rules(pool: aiomysql.Pool) -> List[Dict[str, Any]]:
    async with pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cursor:
            await cursor.execute("SELECT id, ua_string, created_at FROM ua_rules ORDER BY created_at DESC")
            return await cursor.fetchall()

async def add_ua_rule(pool: aiomysql.Pool, ua_string: str) -> int:
    async with pool.acquire() as conn:
        async with conn.cursor() as cursor:
            await cursor.execute("INSERT INTO ua_rules (ua_string) VALUES (%s)", (ua_string,))
            return cursor.lastrowid

async def delete_ua_rule(pool: aiomysql.Pool, rule_id: int) -> bool:
    async with pool.acquire() as conn:
        async with conn.cursor() as cursor:
            affected_rows = await cursor.execute("DELETE FROM ua_rules WHERE id = %s", (rule_id,))
            return affected_rows > 0

async def create_token_access_log(pool: aiomysql.Pool, token_id: int, ip_address: str, user_agent: Optional[str], log_status: str, path: Optional[str] = None):
    async with pool.acquire() as conn:
        async with conn.cursor() as cursor:
            await cursor.execute(
                "INSERT INTO token_access_logs (token_id, ip_address, user_agent, status, path) VALUES (%s, %s, %s, %s, %s)",
                (token_id, ip_address, user_agent, log_status, path)
            )

async def get_token_access_logs(pool: aiomysql.Pool, token_id: int) -> List[Dict[str, Any]]:
    async with pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cursor:
            await cursor.execute("SELECT ip_address, user_agent, access_time, status, path FROM token_access_logs WHERE token_id = %s ORDER BY access_time DESC LIMIT 200", (token_id,))
            return await cursor.fetchall()

async def toggle_source_favorite_status(pool: aiomysql.Pool, source_id: int) -> bool:
    """切换一个数据源的精确标记状态。一个作品只能有一个精确标记的源。"""
    async with pool.acquire() as conn:
        async with conn.cursor() as cursor:
            # 首先，获取此源关联的 anime_id
            await cursor.execute("SELECT anime_id FROM anime_sources WHERE id = %s", (source_id,))
            result = await cursor.fetchone()
            if not result:
                return False
            anime_id = result[0]

            try:
                await conn.begin()
                # 将此作品下的所有其他源都设为非精确
                await cursor.execute("UPDATE anime_sources SET is_favorited = FALSE WHERE anime_id = %s AND id != %s", (anime_id, source_id))
                # 切换目标源的精确标记状态
                await cursor.execute("UPDATE anime_sources SET is_favorited = NOT is_favorited WHERE id = %s", (source_id,))
                await conn.commit()
                return True
            except Exception as e:
                await conn.rollback()
                logging.error(f"切换源收藏状态时出错: {e}", exc_info=True)
                return False

async def toggle_source_incremental_refresh(pool: aiomysql.Pool, source_id: int) -> bool:
    """切换一个数据源的定时增量更新状态。"""
    async with pool.acquire() as conn:
        async with conn.cursor() as cursor:
            affected_rows = await cursor.execute(
                "UPDATE anime_sources SET incremental_refresh_enabled = NOT incremental_refresh_enabled WHERE id = %s",
                (source_id,)
            )
            return affected_rows > 0

async def increment_incremental_refresh_failures(pool: aiomysql.Pool, source_id: int) -> int:
    """将指定源的增量更新失败次数加一，并返回新的失败次数。"""
    async with pool.acquire() as conn:
        async with conn.cursor() as cursor:
            await conn.begin()
            try:
                await cursor.execute(
                    "UPDATE anime_sources SET incremental_refresh_failures = incremental_refresh_failures + 1 WHERE id = %s",
                    (source_id,)
                )
                await cursor.execute("SELECT incremental_refresh_failures FROM anime_sources WHERE id = %s", (source_id,))
                result = await cursor.fetchone()
                await conn.commit()
                return result[0] if result else 0
            except Exception:
                await conn.rollback()
                raise

async def reset_incremental_refresh_failures(pool: aiomysql.Pool, source_id: int):
    """重置指定源的增量更新失败次数为0。"""
    async with pool.acquire() as conn:
        async with conn.cursor() as cursor:
            await cursor.execute(
                "UPDATE anime_sources SET incremental_refresh_failures = 0 WHERE id = %s",
                (source_id,)
            )

async def disable_incremental_refresh(pool: aiomysql.Pool, source_id: int) -> bool:
    """禁用指定源的增量更新。"""
    async with pool.acquire() as conn:
        async with conn.cursor() as cursor:
            affected_rows = await cursor.execute(
                "UPDATE anime_sources SET incremental_refresh_enabled = FALSE WHERE id = %s",
                (source_id,)
            )
            return affected_rows > 0

# --- OAuth State Management ---

async def create_oauth_state(pool: aiomysql.Pool, user_id: int) -> str:
    """为OAuth流程创建一个唯一的、有有效期的state，并与用户ID关联。"""
    state = secrets.token_urlsafe(32)
    # 10分钟有效期
    expires_at = datetime.now() + timedelta(minutes=10)
    async with pool.acquire() as conn:
        async with conn.cursor() as cursor:
            await cursor.execute(
                "INSERT INTO oauth_states (state_key, user_id, expires_at) VALUES (%s, %s, %s)",
                (state, user_id, expires_at)
            )
    return state

async def consume_oauth_state(pool: aiomysql.Pool, state: str) -> Optional[int]:
    """验证并消费一个OAuth state。如果state有效且未过期，则返回关联的用户ID并删除该state。"""
    async with pool.acquire() as conn:
        async with conn.cursor() as cursor:
            await conn.begin()
            try:
                await cursor.execute("SELECT user_id FROM oauth_states WHERE state_key = %s AND expires_at > NOW() FOR UPDATE", (state,))
                result = await cursor.fetchone()
                if result:
                    await cursor.execute("DELETE FROM oauth_states WHERE state_key = %s", (state,))
                await conn.commit()
                return result[0] if result else None
            except Exception:
                await conn.rollback()
                raise

# --- Bangumi 授权服务 ---

async def get_bangumi_auth(pool: aiomysql.Pool, user_id: int) -> Optional[Dict[str, Any]]:
    """获取用户的 Bangumi 授权信息。"""
    async with pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cursor:
            await cursor.execute("SELECT * FROM bangumi_auth WHERE user_id = %s", (user_id,))
            return await cursor.fetchone()

async def save_bangumi_auth(pool: aiomysql.Pool, user_id: int, auth_data: Dict[str, Any]):
    """保存或更新用户的 Bangumi 授权信息。"""
    async with pool.acquire() as conn:
        async with conn.cursor() as cursor:
            # 当记录不存在时，authorized_at 会被设置为 NOW()。
            # 当记录已存在时（ON DUPLICATE KEY UPDATE），authorized_at 不会被更新，保留首次授权时间。
            query = """
                INSERT INTO bangumi_auth (user_id, bangumi_user_id, nickname, avatar_url, access_token, refresh_token, expires_at, authorized_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    bangumi_user_id = VALUES(bangumi_user_id),
                    nickname = VALUES(nickname),
                    avatar_url = VALUES(avatar_url),
                    access_token = VALUES(access_token),
                    refresh_token = VALUES(refresh_token),
                    expires_at = VALUES(expires_at),
                    authorized_at = IF(bangumi_auth.authorized_at IS NULL, VALUES(authorized_at), bangumi_auth.authorized_at)
            """
            await cursor.execute(query, (
                user_id, auth_data.get('bangumi_user_id'), auth_data.get('nickname'),
                auth_data.get('avatar_url'), auth_data.get('access_token'),
                auth_data.get('refresh_token'), auth_data.get('expires_at'),
                datetime.now()
            ))

async def delete_bangumi_auth(pool: aiomysql.Pool, user_id: int) -> bool:
    """删除用户的 Bangumi 授权信息。"""
    async with pool.acquire() as conn:
        async with conn.cursor() as cursor:
            affected_rows = await cursor.execute("DELETE FROM bangumi_auth WHERE user_id = %s", (user_id,))
            return affected_rows > 0

async def get_sources_with_incremental_refresh_enabled(pool: aiomysql.Pool) -> List[int]:
    """获取所有启用了定时增量更新的数据源的ID。"""
    async with pool.acquire() as conn:
        async with conn.cursor() as cursor:
            await cursor.execute("SELECT id FROM anime_sources WHERE incremental_refresh_enabled = TRUE")
            return [row[0] for row in await cursor.fetchall()]

# --- Scheduled Tasks ---

async def get_animes_with_tmdb_id(pool: aiomysql.Pool) -> List[Dict[str, Any]]:
    """获取所有已关联TMDB ID的电视节目。"""
    async with pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cursor:
            await cursor.execute("""
                SELECT a.id as anime_id, a.title, m.tmdb_id, m.tmdb_episode_group_id
                FROM anime a
                JOIN anime_metadata m ON a.id = m.anime_id
                WHERE a.type = 'tv_series' AND m.tmdb_id IS NOT NULL AND m.tmdb_id != ''
            """)
            return await cursor.fetchall()

async def update_anime_tmdb_group_id(pool: aiomysql.Pool, anime_id: int, group_id: str):
    """更新一个作品的TMDB剧集组ID。"""
    async with pool.acquire() as conn:
        async with conn.cursor() as cursor:
            await cursor.execute(
                "UPDATE anime_metadata SET tmdb_episode_group_id = %s WHERE anime_id = %s",
                (group_id, anime_id)
            )

async def update_anime_aliases_if_empty(pool: aiomysql.Pool, anime_id: int, aliases: Dict[str, Any]):
    """如果本地别名字段为空，则使用提供的别名进行更新。"""
    async with pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cursor:
            await cursor.execute("SELECT name_en, name_jp, name_romaji, alias_cn_1, alias_cn_2, alias_cn_3 FROM anime_aliases WHERE anime_id = %s", (anime_id,))
            current = await cursor.fetchone()
            if not current: return

            updates, params = [], []
            if not current.get('name_en') and aliases.get('name_en'):
                updates.append("name_en = %s"); params.append(aliases['name_en'])
            if not current.get('name_jp') and aliases.get('name_jp'):
                updates.append("name_jp = %s"); params.append(aliases['name_jp'])
            if not current.get('name_romaji') and aliases.get('name_romaji'):
                updates.append("name_romaji = %s"); params.append(aliases['name_romaji'])
            
            cn_aliases = aliases.get('aliases_cn', [])
            if not current.get('alias_cn_1') and len(cn_aliases) > 0:
                updates.append("alias_cn_1 = %s"); params.append(cn_aliases[0])
            if not current.get('alias_cn_2') and len(cn_aliases) > 1:
                updates.append("alias_cn_2 = %s"); params.append(cn_aliases[1])
            if not current.get('alias_cn_3') and len(cn_aliases) > 2:
                updates.append("alias_cn_3 = %s"); params.append(cn_aliases[2])

            if updates:
                query = f"UPDATE anime_aliases SET {', '.join(updates)} WHERE anime_id = %s"
                params.append(anime_id)
                await cursor.execute(query, tuple(params))
                logging.info(f"为作品 ID {anime_id} 更新了 {len(updates)} 个别名字段。")

async def get_scheduled_tasks(pool: aiomysql.Pool) -> List[Dict[str, Any]]:
    async with pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cursor:
            await cursor.execute("SELECT id, name, job_type, cron_expression, is_enabled, last_run_at, next_run_at FROM scheduled_tasks ORDER BY name")
            return await cursor.fetchall()

async def check_scheduled_task_exists_by_type(pool: aiomysql.Pool, job_type: str) -> bool:
    """检查指定类型的定时任务是否已存在。"""
    async with pool.acquire() as conn:
        async with conn.cursor() as cursor:
            await cursor.execute("SELECT 1 FROM scheduled_tasks WHERE job_type = %s LIMIT 1", (job_type,))
            result = await cursor.fetchone()
            return result is not None

async def get_scheduled_task(pool: aiomysql.Pool, task_id: str) -> Optional[Dict[str, Any]]:
    async with pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cursor:
            await cursor.execute("SELECT id, name, job_type, cron_expression, is_enabled, last_run_at, next_run_at FROM scheduled_tasks WHERE id = %s", (task_id,))
            return await cursor.fetchone()

async def create_scheduled_task(pool: aiomysql.Pool, task_id: str, name: str, job_type: str, cron: str, is_enabled: bool):
    async with pool.acquire() as conn:
        async with conn.cursor() as cursor:
            await cursor.execute("INSERT INTO scheduled_tasks (id, name, job_type, cron_expression, is_enabled) VALUES (%s, %s, %s, %s, %s)", (task_id, name, job_type, cron, is_enabled))

async def update_scheduled_task(pool: aiomysql.Pool, task_id: str, name: str, cron: str, is_enabled: bool):
    async with pool.acquire() as conn:
        async with conn.cursor() as cursor:
            await cursor.execute("UPDATE scheduled_tasks SET name = %s, cron_expression = %s, is_enabled = %s WHERE id = %s", (name, cron, is_enabled, task_id))

async def delete_scheduled_task(pool: aiomysql.Pool, task_id: str):
    async with pool.acquire() as conn:
        async with conn.cursor() as cursor:
            await cursor.execute("DELETE FROM scheduled_tasks WHERE id = %s", (task_id,))

async def update_scheduled_task_run_times(pool: aiomysql.Pool, task_id: str, last_run: Optional[datetime], next_run: Optional[datetime]):
    async with pool.acquire() as conn:
        async with conn.cursor() as cursor:
            await cursor.execute("UPDATE scheduled_tasks SET last_run_at = %s, next_run_at = %s WHERE id = %s", (last_run, next_run, task_id))

# --- Task History ---

async def create_task_in_history(pool: aiomysql.Pool, task_id: str, title: str, status: str, description: str):
    """在 task_history 表中创建一条新的任务记录。"""
    async with pool.acquire() as conn:
        async with conn.cursor() as cursor:
            await cursor.execute(
                "INSERT INTO task_history (id, title, status, description) VALUES (%s, %s, %s, %s)",
                (task_id, title, status, description)
            )

async def update_task_progress_in_history(pool: aiomysql.Pool, task_id: str, status: str, progress: int, description: str):
    """更新任务历史记录中的进度和状态。"""
    async with pool.acquire() as conn:
        async with conn.cursor() as cursor:
            await cursor.execute(
                "UPDATE task_history SET status = %s, progress = %s, description = %s WHERE id = %s",
                (status, progress, description, task_id)
            )

async def finalize_task_in_history(pool: aiomysql.Pool, task_id: str, status: str, description: str):
    """标记任务为最终状态（完成或失败）并记录完成时间。"""
    async with pool.acquire() as conn:
        async with conn.cursor() as cursor:
            await cursor.execute(
                "UPDATE task_history SET status = %s, description = %s, progress = 100, finished_at = NOW() WHERE id = %s",
                (status, description, task_id)
            )

async def update_task_status(pool: aiomysql.Pool, task_id: str, status: str):
    """仅更新任务的状态，不改变进度和描述。"""
    async with pool.acquire() as conn:
        async with conn.cursor() as cursor:
            await cursor.execute(
                "UPDATE task_history SET status = %s WHERE id = %s", (status, task_id)
            )

async def get_tasks_from_history(pool: aiomysql.Pool, search_term: Optional[str], status_filter: str) -> List[Dict[str, Any]]:
    """从数据库获取任务历史记录，支持搜索和过滤。"""
    query = "SELECT id as task_id, title, status, progress, description, created_at FROM task_history"
    conditions, params = [], []

    if search_term:
        conditions.append("title LIKE %s")
        params.append(f"%{search_term}%")

    if status_filter == 'in_progress':
        conditions.append("status IN ('排队中', '运行中', '已暂停')")
    elif status_filter == 'completed':
        conditions.append("status = '已完成'")

    if conditions:
        query += " WHERE " + " AND ".join(conditions)
    
    query += " ORDER BY created_at DESC LIMIT 100"

    async with pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cursor:
            await cursor.execute(query, tuple(params))
            return await cursor.fetchall()

async def get_task_from_history_by_id(pool: aiomysql.Pool, task_id: str) -> Optional[Dict[str, Any]]:
    """从数据库获取单个任务历史记录。"""
    async with pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cursor:
            await cursor.execute("SELECT id, title, status FROM task_history WHERE id = %s", (task_id,))
            return await cursor.fetchone()

async def delete_task_from_history(pool: aiomysql.Pool, task_id: str) -> bool:
    """从 task_history 表中删除一个任务记录。"""
    async with pool.acquire() as conn:
        async with conn.cursor() as cursor:
            affected_rows = await cursor.execute("DELETE FROM task_history WHERE id = %s", (task_id,))
            return affected_rows > 0

async def mark_interrupted_tasks_as_failed(pool: aiomysql.Pool) -> int:
    """
    在应用启动时，将所有处于“运行中”或“已暂停”状态的任务标记为失败。
    这用于处理应用异常关闭导致的状态不一致问题。
    """
    async with pool.acquire() as conn:
        async with conn.cursor() as cursor:
            query = "UPDATE task_history SET status = %s, description = %s, finished_at = NOW() WHERE status IN (%s, %s)"
            affected_rows = await cursor.execute(query, ("失败", "因程序重启而中断", "运行中", "已暂停"))
            return affected_rows

async def initialize_configs(pool: aiomysql.Pool, defaults: Dict[str, tuple[Any, str]]):
    """
    初始化配置表的默认值。如果配置项不存在，则创建它。
    """
    if not defaults:
        return

    async with pool.acquire() as conn:
        async with conn.cursor() as cursor:
            # 1. 获取所有已存在的配置
            await cursor.execute("SELECT config_key FROM config")
            existing_keys = {row[0] for row in await cursor.fetchall()}

            # 2. 准备插入新配置
            configs_to_insert = []
            for key, (value, description) in defaults.items():
                if key not in existing_keys:
                    logging.getLogger(__name__).info(f"正在初始化配置项 '{key}'...")
                    configs_to_insert.append((key, str(value), description))

            # 3. 批量插入新配置
            if configs_to_insert:
                query_insert = "INSERT INTO config (config_key, config_value, description) VALUES (%s, %s, %s)"
                await cursor.executemany(query_insert, configs_to_insert)
                logging.getLogger(__name__).info(f"成功初始化 {len(configs_to_insert)} 个新配置项。")

    logging.getLogger(__name__).info("默认配置检查完成。")

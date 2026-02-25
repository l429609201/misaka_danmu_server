"""
Import相关的API端点 - 弹幕导入功能
"""
import hashlib
import logging

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from src import security, tasks
from src.db import crud, models, get_db_session, ConfigManager
from src.rate_limiter import RateLimiter
from src.services import TaskManager, ScraperManager, MetadataSourceManager

from src.api.dependencies import (
    get_scraper_manager, get_task_manager, get_metadata_manager,
    get_config_manager, get_rate_limiter, get_title_recognition_manager
)
from .models import UITaskResponse, ImportFromUrlRequest, ValidateUrlRequest, ValidateUrlResponse

logger = logging.getLogger(__name__)

router = APIRouter()

@router.post("/import", status_code=status.HTTP_202_ACCEPTED, summary="从指定数据源导入弹幕", response_model=UITaskResponse)
async def import_from_provider(
    request_data: models.ImportRequest,
    current_user: models.User = Depends(security.get_current_user),
    session: AsyncSession = Depends(get_db_session),
    scraper_manager: ScraperManager = Depends(get_scraper_manager),
    task_manager: TaskManager = Depends(get_task_manager),
    rate_limiter: RateLimiter = Depends(get_rate_limiter),
    metadata_manager: MetadataSourceManager = Depends(get_metadata_manager),
    config_manager: ConfigManager = Depends(get_config_manager),
    title_recognition_manager = Depends(get_title_recognition_manager)
):
    logger.info(f"导入请求: 用户={current_user.username}, provider={request_data.provider}, title={request_data.animeTitle}")
    try:
        # 在启动任务前检查provider是否存在
        scraper_manager.get_scraper(request_data.provider)
        logger.info(f"用户 '{current_user.username}' 正在从 '{request_data.provider}' 导入 '{request_data.animeTitle}' (media_id={request_data.mediaId})")
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))

    # 替换原有的重复检查逻辑
    duplicate_reason = await crud.check_duplicate_import(
        session=session,
        provider=request_data.provider,
        media_id=request_data.mediaId,
        anime_title=request_data.animeTitle,
        media_type=request_data.type,
        season=request_data.season,
        year=request_data.year,
        is_single_episode=request_data.currentEpisodeIndex is not None,
        episode_index=request_data.currentEpisodeIndex,
        title_recognition_manager=title_recognition_manager
    )
    if duplicate_reason:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=duplicate_reason
        )

    # 创建一个将传递给任务管理器的协程工厂 (lambda)
    task_coro = lambda session, callback: tasks.generic_import_task(
        provider=request_data.provider,
        mediaId=request_data.mediaId,
        animeTitle=request_data.animeTitle,
        mediaType=request_data.type,
        season=request_data.season,
        year=request_data.year,
        currentEpisodeIndex=request_data.currentEpisodeIndex,
        imageUrl=request_data.imageUrl,
        doubanId=request_data.doubanId,
        config_manager=config_manager,
        tmdbId=request_data.tmdbId,
        imdbId=None,
        tvdbId=None, # 手动导入时这些ID为空,
        bangumiId=request_data.bangumiId,
        metadata_manager=metadata_manager,
        task_manager=task_manager, # 传递 task_manager
        progress_callback=callback,
        session=session,
        manager=scraper_manager,
        rate_limiter=rate_limiter,
        title_recognition_manager=title_recognition_manager,
        # 新增: 补充源信息
        supplementProvider=request_data.supplementProvider,
        supplementMediaId=request_data.supplementMediaId
    )
    
    # 预先应用识别词转换来生成正确的任务标题
    display_title = request_data.animeTitle
    display_season = request_data.season

    if title_recognition_manager:
        try:
            converted_title, converted_season, was_converted, metadata_info = await title_recognition_manager.apply_storage_postprocessing(
                request_data.animeTitle, request_data.season, request_data.provider
            )
            if was_converted:
                display_title = converted_title
                display_season = converted_season
                logger.info(f"任务标题应用识别词转换: '{request_data.animeTitle}' S{request_data.season:02d} -> '{display_title}' S{display_season:02d}")
        except Exception as e:
            logger.warning(f"任务标题识别词转换失败: {e}")

    # 构造任务标题（使用转换后的标题）
    task_title = f"导入: {display_title} ({request_data.provider})"
    # 如果是电视剧且指定了单集导入，则在标题中追加季和集信息
    if request_data.type == "tv_series" and request_data.currentEpisodeIndex is not None and display_season is not None:
        task_title += f" - S{display_season:02d}E{request_data.currentEpisodeIndex:02d}"

    # 生成unique_key以避免重复任务
    unique_key_parts = [request_data.provider, request_data.mediaId]
    if request_data.season is not None:
        unique_key_parts.append(f"season-{request_data.season}")
    if request_data.currentEpisodeIndex is not None:
        unique_key_parts.append(f"episode-{request_data.currentEpisodeIndex}")
    if request_data.type:
        unique_key_parts.append(request_data.type)
    unique_key = f"ui-import-{'-'.join(unique_key_parts)}"

    # 提交任务并获取任务ID
    task_parameters = {
        "provider": request_data.provider,
        "mediaId": request_data.mediaId,
        "animeTitle": request_data.animeTitle,
        "mediaType": request_data.type,
        "season": request_data.season,
        "year": request_data.year,
        "currentEpisodeIndex": request_data.currentEpisodeIndex,
        "imageUrl": request_data.imageUrl,
        "doubanId": request_data.doubanId,
        "tmdbId": request_data.tmdbId,
        "imdbId": None,
        "tvdbId": None,
        "bangumiId": request_data.bangumiId,
        "supplementProvider": request_data.supplementProvider,
        "supplementMediaId": request_data.supplementMediaId,
    }
    task_id, _ = await task_manager.submit_task(
        task_coro, task_title, unique_key=unique_key,
        task_type="generic_import", task_parameters=task_parameters
    )

    return {"message": f"'{request_data.animeTitle}' 的导入任务已提交。请在任务管理器中查看进度。", "taskId": task_id}



@router.post("/import/edited", status_code=status.HTTP_202_ACCEPTED, summary="导入编辑后的分集列表", response_model=UITaskResponse)
async def import_edited_episodes(
    request_data: models.EditedImportRequest,
    current_user: models.User = Depends(security.get_current_user),
    task_manager: TaskManager = Depends(get_task_manager),
    scraper_manager: ScraperManager = Depends(get_scraper_manager),
    rate_limiter: RateLimiter = Depends(get_rate_limiter),
    metadata_manager: MetadataSourceManager = Depends(get_metadata_manager),
    config_manager: ConfigManager = Depends(get_config_manager),
    title_recognition_manager = Depends(get_title_recognition_manager)
):
    """提交一个后台任务，使用用户在前端编辑过的分集列表进行导入。"""
    # 预先应用识别词转换来生成正确的任务标题
    display_title = request_data.animeTitle
    display_season = request_data.season

    if title_recognition_manager:
        try:
            converted_title, converted_season, was_converted, metadata_info = await title_recognition_manager.apply_storage_postprocessing(
                request_data.animeTitle, request_data.season, request_data.provider
            )
            if was_converted:
                display_title = converted_title
                display_season = converted_season
                logger.info(f"编辑导入任务标题应用识别词转换: '{request_data.animeTitle}' S{request_data.season:02d} -> '{display_title}' S{display_season:02d}")
        except Exception as e:
            logger.warning(f"编辑导入任务标题识别词转换失败: {e}")

    task_title = f"编辑后导入: {display_title} ({request_data.provider})"
    task_coro = lambda session, callback: tasks.edited_import_task(
        request_data=request_data,
        progress_callback=callback,
        session=session,
        manager=scraper_manager,
        config_manager=config_manager,
        rate_limiter=rate_limiter,
        metadata_manager=metadata_manager,
        title_recognition_manager=title_recognition_manager
    )
    # 修正：为编辑后导入任务添加一个唯一的键，以防止重复提交，同时允许对同一作品的不同分集范围进行排队。
    # 这个键基于提供商、媒体ID和正在导入的分集索引列表的哈希值。
    # 这修复了在一次导入完成后，立即为同一作品提交另一次导入时，因任务标题相同而被拒绝的问题。
    episode_indices_str = ",".join(sorted([str(ep.episodeIndex) for ep in request_data.episodes]))
    episodes_hash = hashlib.md5(episode_indices_str.encode('utf-8')).hexdigest()[:8]
    unique_key = f"import-{request_data.provider}-{request_data.mediaId}-{episodes_hash}"

    try:
        task_id, _ = await task_manager.submit_task(task_coro, task_title, unique_key=unique_key)
    except HTTPException as e:
        # 重新抛出由 task_manager 引发的冲突错误
        raise e
    except Exception as e:
        logger.error(f"提交编辑后导入任务时发生未知错误: {e}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="提交任务时发生内部错误。")
    return {"message": f"'{request_data.animeTitle}' 的编辑导入任务已提交。", "taskId": task_id}


@router.post("/validate-url", summary="校验并解析导入URL", response_model=ValidateUrlResponse)
async def validate_import_url(
    request_data: ValidateUrlRequest,
    current_user: models.User = Depends(security.get_current_user),
    scraper_manager: ScraperManager = Depends(get_scraper_manager)
):
    """
    动态校验 URL 并返回解析结果。

    - 自动检测 URL 属于哪个平台（基于各 scraper 的 handled_domains）
    - 调用对应 scraper 的 get_info_from_url() 获取详细信息
    - 返回解析出的平台、媒体ID、标题、封面等信息

    用途：
    1. 前端在用户输入URL后调用此接口进行校验
    2. 返回识别出的平台、媒体ID等信息
    3. 前端可据此自动填充表单字段
    """
    url = request_data.url.strip()

    if not url:
        return ValidateUrlResponse(isValid=False, errorMessage="URL不能为空")

    # 1. 动态查找 scraper（基于 _domain_map，由各 scraper 的 handled_domains 构建）
    scraper = scraper_manager.get_scraper_by_domain(url)
    if not scraper:
        return ValidateUrlResponse(isValid=False, errorMessage="不支持的URL或平台，请检查URL是否正确")

    provider = scraper.provider_name

    try:
        # 2. 调用 scraper 的解析方法
        info = await scraper.get_info_from_url(url)
        if not info:
            return ValidateUrlResponse(
                isValid=False,
                provider=provider,
                errorMessage=f"无法从URL解析出有效信息，请检查URL格式是否正确"
            )

        # 3. 返回解析结果
        return ValidateUrlResponse(
            isValid=True,
            provider=provider,
            mediaId=info.mediaId,
            title=info.title,
            imageUrl=info.imageUrl,
            mediaType=info.type,
            year=info.year,
            episodeIndex=info.currentEpisodeIndex  # 如果URL中包含集数信息
        )
    except Exception as e:
        logger.error(f"解析URL时发生错误: {e}", exc_info=True)
        return ValidateUrlResponse(
            isValid=False,
            provider=provider,
            errorMessage=f"解析URL时发生错误: {str(e)}"
        )


@router.post("/import-from-url", status_code=status.HTTP_202_ACCEPTED, summary="从URL导入弹幕", response_model=UITaskResponse)
async def import_from_url(
    request_data: ImportFromUrlRequest,
    current_user: models.User = Depends(security.get_current_user),
    session: AsyncSession = Depends(get_db_session),
    scraper_manager: ScraperManager = Depends(get_scraper_manager),
    task_manager: TaskManager = Depends(get_task_manager),
    rate_limiter: RateLimiter = Depends(get_rate_limiter),
    metadata_manager: MetadataSourceManager = Depends(get_metadata_manager),
    config_manager: ConfigManager = Depends(get_config_manager),
    title_recognition_manager = Depends(get_title_recognition_manager)
):
    """
    从URL导入弹幕（重构版 - 动态解析）。

    - 如果不指定 provider，会自动从URL检测平台
    - 如果不指定 title，会自动从源获取标题
    - 调用各 scraper 的 get_info_from_url() 方法进行解析，无需硬编码
    """
    url = request_data.url.strip()

    if not url:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="URL不能为空")

    # 1. 确定 scraper：优先使用指定的 provider，否则自动检测
    scraper = None
    provider = request_data.provider

    if provider:
        # 用户指定了 provider
        try:
            scraper = scraper_manager.get_scraper(provider)
        except ValueError as e:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    else:
        # 自动检测 provider
        scraper = scraper_manager.get_scraper_by_domain(url)
        if not scraper:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="无法识别URL所属平台，请检查URL是否正确或手动指定平台"
            )
        provider = scraper.provider_name

    # 2. 调用 scraper 的 get_info_from_url() 获取作品信息
    try:
        info = await scraper.get_info_from_url(url)
        if not info:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"无法从URL '{url}' 解析出有效的作品信息"
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"从URL解析作品信息时出错: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"从URL解析作品信息时出错: {str(e)}"
        )

    # 3. 确定最终使用的参数（用户指定的优先，否则使用从源获取的）
    final_title = request_data.title if request_data.title else info.title
    final_media_type = request_data.media_type if request_data.media_type else (info.type or "tv_series")
    final_season = request_data.season if request_data.season is not None else (info.season or 1)
    media_id = info.mediaId
    image_url = info.imageUrl
    year = info.year

    logger.info(f"URL导入: provider={provider}, mediaId={media_id}, title={final_title}, type={final_media_type}, season={final_season}")

    # 4. 创建导入任务
    task_coro = lambda session, callback: tasks.generic_import_task(
        provider=provider,
        mediaId=media_id,
        animeTitle=final_title,
        mediaType=final_media_type,
        season=final_season,
        year=year,
        currentEpisodeIndex=None,
        imageUrl=image_url,
        doubanId=None,
        tmdbId=None,
        imdbId=None,
        tvdbId=None,
        bangumiId=None,
        metadata_manager=metadata_manager,
        progress_callback=callback,
        session=session,
        manager=scraper_manager,
        task_manager=task_manager,
        config_manager=config_manager,
        rate_limiter=rate_limiter,
        title_recognition_manager=title_recognition_manager
    )

    # 5. 生成 unique_key 以避免重复任务
    unique_key_parts = ["url-import", provider, media_id, final_media_type]
    if final_season:
        unique_key_parts.append(f"season-{final_season}")
    unique_key = "-".join(filter(None, unique_key_parts))

    task_title = f"URL导入: {final_title} ({provider})"
    task_id, _ = await task_manager.submit_task(task_coro, task_title, unique_key=unique_key)

    return {"message": f"'{final_title}' 的URL导入任务已提交。", "taskId": task_id}




"""UI 单集操作与查询仓储字段契约回归，不连接数据库或执行任务。"""
import unittest
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
from src.api.ui import episode as api


class EpisodeUIContractTests(unittest.IsolatedAsyncioTestCase):
    """用实际查询返回字段检查刷新及删除任务提交。"""

    async def test_refresh_and_delete_use_query_title_after_transaction(self) -> None:
        """事务结束后提交任务，保留真实标题和原去重键。"""
        active = False

        @asynccontextmanager
        async def transaction():
            nonlocal active
            active = True
            try:
                yield
            finally:
                active = False

        async def submit(*args, **kwargs):
            self.assertFalse(active)
            return 'fixture-task', None

        details = {'episodeId': 25000794010001, 'episodeTitle': '第1集',
                   'episodeNumber': 1, 'providerName': 'bilibili', 'mediaId': 'ss1'}
        db = SimpleNamespace(transaction=transaction, episode=SimpleNamespace(
            get_episode_for_refresh=AsyncMock(return_value=details)))
        manager = SimpleNamespace(submit_task=AsyncMock(side_effect=submit))
        with patch.object(api, 'get_database_service', return_value=db), patch.object(api, 'get_task_manager', return_value=manager), patch.object(api, 'get_scraper_manager', return_value=object()), patch.object(api, 'get_rate_limiter', return_value=object()), patch.object(api, 'get_config_service', return_value=object()):
            result = await api.refresh_single_episode(details['episodeId'], SimpleNamespace(username='fixture'))
            self.assertEqual(result['taskId'], 'fixture-task')
            self.assertIn('第1集', result['message'])
            self.assertEqual(manager.submit_task.await_args.kwargs['unique_key'], 'refresh-episode-25000794010001')
            self.assertIn('第1集', manager.submit_task.await_args.args[1])
            result = await api.delete_episode_from_source(details['episodeId'], False, SimpleNamespace(username='fixture'))
            self.assertIn('第1集', result['message'])
            self.assertEqual(manager.submit_task.await_args.kwargs['unique_key'], 'delete-episode-25000794010001')
            self.assertIn('保留文件', manager.submit_task.await_args.args[1])
            db.episode.get_episode_for_refresh.return_value = None
            with self.assertRaises(HTTPException) as caught:
                await api.refresh_single_episode(details['episodeId'], SimpleNamespace(username='fixture'))
            self.assertEqual(caught.exception.status_code, 404)
            self.assertEqual(manager.submit_task.await_count, 2)

"""流控 SSE 的局部传输诊断，不改变响应内容和异常传播行为。"""

import asyncio
import logging

from fastapi.responses import StreamingResponse
from starlette.types import Message, Receive, Scope, Send

logger = logging.getLogger(__name__)


class RateLimitStreamingResponse(StreamingResponse):
    """记录生成器外的响应执行异常，避免只看到响应头后断流。"""

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        request_id = f"{id(self):x}"
        stage = "响应执行开始"
        first_body_sent = False

        async def diagnostic_send(message: Message) -> None:
            nonlocal stage, first_body_sent
            # 静默跟踪发送阶段，仅在发生异常时输出定位信息。
            if message["type"] == "http.response.start":
                stage = "发送响应头"
                await send(message)
                stage = "响应头已发送"
            elif message["type"] == "http.response.body" and not first_body_sent:
                stage = "发送首帧"
                await send(message)
                first_body_sent = True
                stage = "首帧已发送"
            else:
                await send(message)

        try:
            await super().__call__(scope, receive, diagnostic_send)
        except asyncio.CancelledError:
            raise
        except Exception:
            # 生成器内捕获不到 ASGI send/响应迭代异常，在边界记录后原样抛出。
            logger.exception("流控 SSE [%s] 响应执行失败，最后阶段：%s", request_id, stage)
            raise

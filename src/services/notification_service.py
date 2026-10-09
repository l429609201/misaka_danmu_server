"""通知会话与进度消息状态端口，不持有菜单和发送业务。"""

from typing import Dict, Optional

from src.notification.base import ConversationState


class NotificationService:
    """保存通知输入会话和输出进度消息关联。"""

    def __init__(self, session_factory=None) -> None:
        self._conversations: Dict[str, ConversationState] = {}
        self._task_progress_tg_msg: Dict[str, Dict[int, int]] = {}

    def cleanup_task_progress(self, task_id: str) -> None:
        """清除终态任务的消息关联。"""
        self._task_progress_tg_msg.pop(task_id, None)

    def get_progress_messages(self, task_id: str) -> Dict[int, int]:
        """读取任务在各渠道的进度消息快照。"""
        return dict(self._task_progress_tg_msg.get(task_id, {}))

    def set_progress_message(self, task_id: str, channel_id: int, message_id: int) -> None:
        """记录首次发送或编辑降级后的消息编号。"""
        self._task_progress_tg_msg.setdefault(task_id, {})[channel_id] = message_id

    def remove_progress_message(self, task_id: str, channel_id: int) -> None:
        """成功发送终态后清除单渠道的进度关联。"""
        messages = self._task_progress_tg_msg.get(task_id, {})
        messages.pop(channel_id, None)
        if not messages:
            self.cleanup_task_progress(task_id)

    def get_conversation(self, user_id: str) -> Optional[ConversationState]:
        """获取用户当前对话状态（自动清理过期状态）"""
        conv = self._conversations.get(user_id)
        if conv and conv.is_expired:
            del self._conversations[user_id]
            return None
        return conv

    def set_conversation(self, user_id: str, state: str, data: dict = None,
                         message_id: int = None, chat_id: int = None) -> None:
        """设置用户对话状态"""
        self._conversations[user_id] = ConversationState(
            state=state,
            data=data or {},
            message_id=message_id,
            chat_id=chat_id,
        )

    def clear_conversation(self, user_id: str) -> None:
        """清除用户对话状态"""
        self._conversations.pop(user_id, None)

    def update_conversation_message_id(self, user_id: str, message_id: int) -> None:
        """更新对话关联的消息ID（由渠道层回写）"""
        conv = self._conversations.get(user_id)
        if conv:
            conv.message_id = message_id

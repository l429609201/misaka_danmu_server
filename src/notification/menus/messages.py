"""
事件消息格式化 Mixin（已废弃）

历史上此处维护 _format_event_message / _format_task_progress_message 两套
Markdown V1 风格的通知模板。现已统一迁移到 src/notification/messages/*.py
的消息类（NotificationMessage 子类），通过 NotificationManager 的
render_event_for_channel 按渠道能力输出 MarkdownV2 或纯文本。

本 Mixin 仅保留空壳，维持 NotificationService 的继承链不变。
"""


class MessagesMixin:
    """事件消息格式化 Mixin（空壳，模板逻辑已迁移至 messages/ 消息类）"""
    pass

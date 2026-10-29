"""任务异常定义模块

why：TaskSuccess / TaskFailed / TaskPauseForRateLimit 被 task_manager.py 和
     task_profiler.py 同时需要，而 task_manager.py 依赖 src.db，task_profiler.py
     被 src.db 间接依赖，若直接从 task_manager 导入会产生循环导入。

解法：将三个异常类提取到本模块（零外部依赖），两侧分别从此处导入。
     task_manager.py 通过 re-export 保持对外接口不变，其他业务文件无需改动。
"""


class TaskSuccess(Exception):
    """自定义异常，用于表示任务成功完成并附带一条最终消息。"""
    pass


class TaskFailed(Exception):
    """自定义异常，用于表示任务【业务失败】并附带一条失败消息。

    why：区别于 TaskSuccess（会标记 COMPLETED 并发"成功"通知）和未捕获的普通异常
    （会被当作程序崩溃、打印完整 traceback）。TaskFailed 表示"可预期的业务失败"
    （如数据源验证失败、未获取到弹幕、未创建条目），任务框架会标记 FAILED 并发
    "失败"通知，但不打印 traceback（失败原因已在消息中说明，避免日志噪音）。
    """
    pass


class TaskPauseForRateLimit(Exception):
    """自定义异常，用于表示任务因速率限制需要暂停"""
    def __init__(self, retry_after_seconds: float, message: str = ""):
        self.retry_after_seconds = retry_after_seconds
        self.message = message
        super().__init__(message)

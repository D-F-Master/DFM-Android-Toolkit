"""
queue_config.py - 轻量级内存任务队列

================================================================================
使用方法
================================================================================

1. 在 FastAPI 主应用中挂载生命周期（自动启停消费者线程）:

    from fastapi import FastAPI
    from queue_config import task_queue, task_queue_lifespan

    app = FastAPI(lifespan=task_queue_lifespan)


2. 定义普通的同步业务函数（不需要任何装饰器或特殊签名）:

    def send_email(to: str, subject: str):
        print(f"Sending email to {to}: {subject}")

    def generate_report(report_id: int, include_charts: bool = True):
        print(f"Generating report {report_id}, charts={include_charts}")


3. 在接口或任意代码中一行提交任务:

    # 位置参数 + 关键字参数都可以
    task_queue.submit_task(send_email, "user@example.com", subject="Hello")
    task_queue.submit_task(generate_report, 42, include_charts=False)


4. （可选）查看当前队列积压量:

    size = task_queue.qsize


5. （可选）自定义队列容量和并发数:

    from queue_config import TaskQueue, task_queue_lifespan

    custom_queue = TaskQueue(maxsize=500, num_workers=4)

    # 注意：如果使用自定义实例，需要自行管理生命周期，
    # 或者将 task_queue_lifespan 中的 task_queue 替换为你的实例。


================================================================================
注意事项
================================================================================

- submit_task 只接受【同步函数】，async def 函数不会被正确执行。
- 队列为纯内存实现，进程重启后未消费的任务会丢失。
- maxsize > 0 时队列满则 submit_task 阻塞；设为 0 表示无限队列。
- 如需非阻塞提交，可将 submit_task 改为 put(..., block=False) 并捕获 Full 异常。
- CPU 密集型任务受 GIL 限制无法真正并行，请改用 multiprocessing 或 Celery。
- 生产环境建议配合日志、监控和优雅关闭使用，本模块已内置基础支持。

================================================================================
"""

import threading
import queue
from contextlib import asynccontextmanager
from typing import Callable, Any

from config import logger


class TaskQueue:
    """轻量级内存任务队列 + 消费者线程"""

    def __init__(self, maxsize: int = 0, num_workers: int = 1):
        """
        :param maxsize:     队列最大长度，0 表示不限制
        :param num_workers: 消费者线程数量
        """
        self._queue: queue.Queue = queue.Queue(maxsize=maxsize)
        self._stop_event = threading.Event()
        self._workers: list[threading.Thread] = []
        self._num_workers = num_workers

    def submit_task(self, func: Callable, *args: Any, **kwargs: Any) -> None:
        """
        提交一个同步任务到队列

        :param func:   要执行的同步函数
        :param args:   传给 func 的位置参数
        :param kwargs: 传给 func 的关键字参数

        示例:
            task_queue.submit_task(send_email, "a@b.com", subject="Hi")
        """
        self._queue.put((func, args, kwargs))
        logger.info(
            "Task submitted: %s | queue_size=%d",
            func.__name__,
            self._queue.qsize(),
        )

    def _worker_loop(self) -> None:
        """消费者工作循环，内部使用，无需手动调用"""
        while not self._stop_event.is_set():
            try:
                func, args, kwargs = self._queue.get(timeout=1)
            except queue.Empty:
                continue
            try:
                logger.info("Executing task: %s", func.__name__)
                func(*args, **kwargs)
            except Exception:
                logger.exception("Task failed: %s", func.__name__)
            finally:
                self._queue.task_done()

    def start(self) -> None:
        """启动所有消费者线程"""
        if self._workers:
            logger.warning("TaskQueue already started")
            return
        self._stop_event.clear()
        for i in range(self._num_workers):
            t = threading.Thread(
                target=self._worker_loop,
                name=f"task-queue-worker-{i}",
                daemon=True,
            )
            t.start()
            self._workers.append(t)
        logger.info("TaskQueue started with %d worker(s)", self._num_workers)

    def stop(self, timeout: float = 5.0) -> None:
        """优雅停止所有消费者线程"""
        self._stop_event.set()
        for t in self._workers:
            t.join(timeout=timeout)
        self._workers.clear()
        logger.info("TaskQueue stopped")

    @property
    def qsize(self) -> int:
        """当前队列中的待处理任务数"""
        return self._queue.qsize()


# 全局单例，默认 200 容量、2 个消费者线程
task_queue = TaskQueue(maxsize=200, num_workers=2)


@asynccontextmanager
async def task_queue_lifespan(app):
    """
    FastAPI lifespan 钩子，自动管理队列启停

    用法:
        app = FastAPI(lifespan=task_queue_lifespan)
    """
    task_queue.start()
    yield
    task_queue.stop()
from fastapi import APIRouter

from config import logger
from config.queue_config import task_queue

router = APIRouter(
    prefix="/test",
    tags=["测试方法"]
)


# ========== 定义要异步执行的同步任务函数 ==========
def reboot(host: str, delay: int = 3):
    """
    模拟重启操作
    :param host:  目标主机地址
    :param delay: 模拟耗时（秒）
    """
    import time
    logger.info("Rebooting %s ...", host)
    time.sleep(delay)
    logger.info("Host %s rebooted successfully", host)


def send_notification(user_id: int, message: str):
    """模拟发送通知"""
    import time
    logger.info("Sending notification to user %d: %s", user_id, message)
    time.sleep(2)
    logger.info("Notification sent to user %d", user_id)


# ========== 接口 ==========
@router.get("/task")
def task():
    """
    测试接口：向任务队列提交一个 reboot 任务
    submit_task 签名: submit_task(func, *args, **kwargs)
    """
    task_queue.submit_task(reboot, "192.168.1.100", delay=5)
    return {
        "msg": "reboot task queued",
        "queue_size": task_queue.qsize,
    }


@router.post("/notify/{user_id}")
def notify(user_id: int, message: str = "hello"):
    """演示提交另一个任务"""
    task_queue.submit_task(send_notification, user_id, message=message)
    return {
        "msg": "notification queued",
        "queue_size": task_queue.qsize,
    }
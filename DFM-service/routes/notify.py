"""
脚本执行结果邮件通知接口。

复用 DFM_MCP_mail_service 的 mailboxes.json 账号配置（当前 163test）。
主服务运行在 Python 3.9，无法直接 import 使用了 3.10 语法（X | None）
的 mail_client.py，故此处用 3.9 兼容语法实现同样的 SMTP 发送逻辑。
"""
import json
import smtplib
import ssl
import sys
from email.message import EmailMessage
from email.utils import formataddr
from pathlib import Path
from typing import List

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(
    prefix="/api/notify",
    tags=["通知"]
)

MAILBOX_ID = "163test"


def _mailboxes_path() -> Path:
    """与 mail_client.py 完全相同的配置路径解析（配置含凭据，已 gitignore）"""
    if getattr(sys, "frozen", False):
        base = Path(sys.executable).parent
    else:
        base = Path(__file__).resolve().parent.parent / "data" / "mcp_servers" / "DFM_MCP_mail_service"
    return base / "config" / "mailboxes.json"


class NotifyEmailRequest(BaseModel):
    to: List[str]
    subject: str
    body: str


@router.post("/email")
def send_notify_email(request: NotifyEmailRequest):
    """用配置的邮箱发送一封通知邮件（当前 163test）。"""
    cfg = None
    try:
        with _mailboxes_path().open("r", encoding="utf-8") as f:
            for item in json.load(f):
                if item.get("id") == MAILBOX_ID:
                    cfg = item
                    break
    except Exception as e:
        return {"code": 500, "message": f"读取邮箱配置失败: {e}", "data": None}
    if not cfg:
        return {"code": 500, "message": f"未找到邮箱配置 {MAILBOX_ID}", "data": None}

    msg = EmailMessage()
    msg["From"] = formataddr((cfg.get("name", ""), cfg["email"]))
    msg["To"] = ", ".join(request.to)
    msg["Subject"] = request.subject
    msg.set_content(request.body)

    try:
        context = ssl.create_default_context()
        if cfg.get("use_ssl_smtp", True):
            with smtplib.SMTP_SSL(cfg["smtp_host"], int(cfg["smtp_port"]), context=context) as server:
                server.login(cfg["email"], cfg["password"])
                server.send_message(msg, to_addrs=request.to)
        else:
            with smtplib.SMTP(cfg["smtp_host"], int(cfg["smtp_port"])) as server:
                server.starttls(context=context)
                server.login(cfg["email"], cfg["password"])
                server.send_message(msg, to_addrs=request.to)
        return {"code": 200, "message": "发送成功", "data": {"to": request.to}}
    except smtplib.SMTPAuthenticationError:
        return {"code": 500, "message": "SMTP 认证失败，请检查邮箱/授权码", "data": None}
    except Exception as e:
        return {"code": 500, "message": f"发送失败: {e}", "data": None}

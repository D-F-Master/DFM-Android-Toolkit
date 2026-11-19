"""
MCP 服务端实现，暴露四个工具：
- list_bound_mailboxes：列出已绑定的邮箱
- add_bound_mailbox：新增绑定邮箱
- send_email：通过 SMTP 发送邮件
- read_email：通过 IMAP 读取邮箱列表

使用官方 MCP SDK 2.x (MCPServer)。默认 stdio 传输（最适合本地客户端）；
带 --port 参数启动时使用 Streamable HTTP（供 DFM MCP 管理工具拉起，端点 http://127.0.0.1:<port>/mcp）。
"""
import argparse
import json
import os
import threading
import time
import urllib.request
from pathlib import Path
from typing import Any

from mcp.server.mcpserver import MCPServer

try:
    from importlib.metadata import version as pkg_version
except ImportError:
    from importlib_metadata import version as pkg_version

from mail_client import MailboxConfig, mail_client

# 根目录相对于 .py 文件
BASE_DIR = Path(__file__).parent

# ========== 心跳上报（DFM MCP 管理工具） ==========
SERVER_NAME = "DFM_MCP_mail_service"   # 与 manifest.json 的 name 保持一致
SERVER_VERSION = "0.1.0"               # 与 manifest.json 的 version 保持一致
MANAGER_URL = os.environ.get("DFM_MCP_MANAGER_URL", "http://localhost:3000")


def _report_heartbeat(port: int):
    """每 10 秒向管理端上报一次心跳，携带版本、mcp SDK 版本与端口"""
    def loop():
        while True:
            try:
                payload = json.dumps({
                    "name": SERVER_NAME,
                    "version": SERVER_VERSION,
                    "mcpVersion": pkg_version("mcp"),
                    "port": port,
                }).encode("utf-8")
                req = urllib.request.Request(
                    MANAGER_URL + "/api/mcp/heartbeat",
                    data=payload,
                    headers={"Content-Type": "application/json"},
                )
                urllib.request.urlopen(req, timeout=3)
            except Exception:
                pass  # 管理端未启动时静默跳过
            time.sleep(10)

    threading.Thread(target=loop, daemon=True).start()


def _build_server() -> MCPServer:
    """构建并注册三个工具的 MCPServer 实例"""
    server = MCPServer(
        name="DFM-MCP-Mail-Service",
        version="0.1.0",
        instructions="一个简单的邮件 MCP 服务，支持绑定邮箱、发送邮件、读取邮件列表。",
    )

    # --- 工具 1：list_bound_mailboxes ---
    @server.tool(name="list_bound_mailboxes", title="列出已绑定邮箱", description="返回已配置的邮箱列表（不含密码）")
    def _list_tool() -> list[dict[str, Any]]:
        return mail_client.list_mailboxes()

    # --- 工具 2：add_bound_mailbox ---
    @server.tool(name="add_bound_mailbox", title="新增绑定邮箱", description="新增一个邮箱账号配置并持久化到 config/mailboxes.json")
    def _add_mailbox(
        id: str,
        name: str,
        email: str,
        smtp_host: str,
        imap_host: str,
        password: str,
        smtp_port: int = 465,
        imap_port: int = 993,
        use_ssl_smtp: bool = True,
        use_ssl_imap: bool = True,
    ) -> dict[str, Any]:
        if not id or not name or not email or not smtp_host or not imap_host:
            return {"success": False, "message": "参数错误：id/name/email/smtp_host/imap_host 不能为空"}
        if "@" not in email:
            return {"success": False, "message": f"参数错误：邮箱地址无效: {email}"}

        cfg = MailboxConfig(
            id=id,
            name=name,
            email=email,
            smtp_host=smtp_host,
            smtp_port=int(smtp_port),
            imap_host=imap_host,
            imap_port=int(imap_port),
            password=password,
            use_ssl_smtp=bool(use_ssl_smtp),
            use_ssl_imap=bool(use_ssl_imap),
        )
        ok = mail_client.add_mailbox(cfg)
        if ok:
            return {"success": True, "message": f"邮箱已绑定: {id} ({email})", "mailbox_id": id}
        return {"success": False, "message": f"邮箱 id 已存在，无法重复绑定: {id}"}

    # --- 工具 3：send_email ---
    @server.tool(name="send_email", title="发送邮件", description="通过 SMTP 发送邮件；需先绑定邮箱并配置真实凭据")
    def _send_email(
        mailbox_id: str,
        to: list[str],
        subject: str,
        body: str,
        cc: list[str] | None = None,
        bcc: list[str] | None = None,
        attachments: list[dict[str, Any]] | None = None,
        html: bool = False,
    ) -> dict[str, Any]:
        # 输入验证：basic guard
        if not mailbox_id or not isinstance(mailbox_id, str):
            return {"success": False, "message": "参数错误：mailbox_id 必须是非空字符串"}
        if not to or not isinstance(to, list):
            return {"success": False, "message": "参数错误：to 必须是非空列表"}
        for addr in to:
            if not isinstance(addr, str) or "@" not in addr:
                return {"success": False, "message": f"参数错误：收件人地址无效: {addr}"}

        result = mail_client.send_email(
            mailbox_id=mailbox_id,
            to=to,
            subject=subject,
            body=body,
            cc=cc,
            bcc=bcc,
            attachments=attachments,
            html=html,
        )
        # 返回给 MCP 客户端
        return result

    # --- 工具 4：read_email ---
    @server.tool(name="read_email", title="读取邮件", description="通过 IMAP 读取邮箱邮件列表（仅元数据）")
    def _read_email(
        mailbox_id: str,
        folder: str = "INBOX",
        limit: int = 10,
        unread_only: bool = False,
    ) -> dict[str, Any]:
        if not mailbox_id or not isinstance(mailbox_id, str):
            return {"error": "参数错误：mailbox_id 必须是非空字符串"}
        results = mail_client.read_emails(
            mailbox_id=mailbox_id,
            folder=folder,
            limit=limit,
            unread_only=unread_only,
        )
        return {"results": results}

    return server


def main():
    """主入口：默认 stdio 传输；带 --port 时用 Streamable HTTP 供管理工具拉起"""
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=None, help="指定端口则以 Streamable HTTP 运行")
    args = parser.parse_args()

    server = _build_server()
    if args.port is None:
        # transport 默认 'stdio'，最适合桌面客户端（Claude Desktop/Cursor 等）
        server.run()
    else:
        _report_heartbeat(args.port)
        server.run("streamable-http", host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
"""
邮件客户端模块：封装 SMTP 发送与 IMAP 读取，支持多账号配置。
"""
import json
import os
import smtplib
import ssl
import sys
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import formataddr, parseaddr
from pathlib import Path
from typing import Any

import imaplib


def _base_dir() -> Path:
    """返回配置文件所在的基础目录。
    PyInstaller 打包后 __file__ 指向临时解压目录（_MEIPASS），
    因此冻结状态下改用 exe 所在目录，保证配置能持久保存。
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).parent


CONFIG_DIR = _base_dir() / "config"
CONFIG_FILE = CONFIG_DIR / "mailboxes.json"
CONFIG_DIR.mkdir(exist_ok=True)


@dataclass
class MailboxConfig:
    """单个邮箱账号配置"""
    id: str                    # 唯一标识，如 "personal"
    name: str                  # 显示名称，如 "个人邮箱"
    email: str                 # 完整邮箱地址
    smtp_host: str             # SMTP 服务器
    smtp_port: int             # SMTP 端口（465 SSL / 587 STARTTLS）
    imap_host: str             # IMAP 服务器
    imap_port: int             # IMAP 端口（993 SSL）
    password: str              # 授权码/应用专用密码
    use_ssl_smtp: bool = True  # True=SSL(465), False=STARTTLS(587)
    use_ssl_imap: bool = True  # IMAP 通常用 SSL(993)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "MailboxConfig":
        # 兼容示例文件里的占位符
        pw = d.get("password", "")
        if pw == "***REPLACE_WITH_AUTH_CODE***":
            pw = os.environ.get(f"MAIL_PASSWORD_{d['id'].upper()}", "")
        return cls(
            id=d["id"],
            name=d["name"],
            email=d["email"],
            smtp_host=d["smtp_host"],
            smtp_port=int(d["smtp_port"]),
            imap_host=d["imap_host"],
            imap_port=int(d["imap_port"]),
            password=pw,
            use_ssl_smtp=d.get("use_ssl_smtp", True),
            use_ssl_imap=d.get("use_ssl_imap", True),
        )


class MailClient:
    """统一管理多邮箱的发送/读取"""

    def __init__(self):
        self._mailboxes: dict[str, MailboxConfig] = {}
        self._load_config()

    def _load_config(self):
        if CONFIG_FILE.exists():
            try:
                with CONFIG_FILE.open("r", encoding="utf-8") as f:
                    data = json.load(f)
                for item in data:
                    cfg = MailboxConfig.from_dict(item)
                    self._mailboxes[cfg.id] = cfg
            except Exception as e:
                raise RuntimeError(f"加载邮箱配置失败: {e}")

    def save_config(self):
        """保存配置到 config/mailboxes.json（含凭据；该文件已在 .gitignore 中）"""
        data = [
            {
                "id": m.id,
                "name": m.name,
                "email": m.email,
                "smtp_host": m.smtp_host,
                "smtp_port": m.smtp_port,
                "imap_host": m.imap_host,
                "imap_port": m.imap_port,
                "password": m.password,
                "use_ssl_smtp": m.use_ssl_smtp,
                "use_ssl_imap": m.use_ssl_imap,
            }
            for m in self._mailboxes.values()
        ]
        with CONFIG_FILE.open("w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def add_mailbox(self, cfg: MailboxConfig) -> bool:
        """新增邮箱；id 已存在则返回 False"""
        if cfg.id in self._mailboxes:
            return False
        self._mailboxes[cfg.id] = cfg
        self.save_config()
        return True

    def remove_mailbox(self, mailbox_id: str) -> bool:
        if mailbox_id in self._mailboxes:
            del self._mailboxes[mailbox_id]
            self.save_config()
            return True
        return False

    def list_mailboxes(self) -> list[dict[str, str]]:
        """返回不含密码的列表，供 MCP 工具调用"""
        return [
            {"id": m.id, "name": m.name, "email": m.email}
            for m in self._mailboxes.values()
        ]

    def get_mailbox(self, mailbox_id: str) -> MailboxConfig | None:
        return self._mailboxes.get(mailbox_id)

    # ---------- SMTP 发送 ----------
    def send_email(
        self,
        mailbox_id: str,
        to: list[str],
        subject: str,
        body: str,
        cc: list[str] | None = None,
        bcc: list[str] | None = None,
        attachments: list[dict[str, Any]] | None = None,
        html: bool = False,
    ) -> dict[str, Any]:
        """
        发送邮件。
        attachments: [{"filename": "a.pdf", "content": b"..."}] 或 [{"filename": "a.pdf", "path": "/abs/path"}]
        返回: {"success": bool, "message": str, "message_id": str|None}
        """
        cfg = self.get_mailbox(mailbox_id)
        if not cfg:
            return {"success": False, "message": f"未找到邮箱: {mailbox_id}"}

        msg = EmailMessage()
        msg["From"] = formataddr((cfg.name, cfg.email))
        msg["To"] = ", ".join(to)
        if cc:
            msg["Cc"] = ", ".join(cc)
        msg["Subject"] = subject

        if html:
            msg.add_alternative(body, subtype="html")
        else:
            msg.set_content(body)

        # 处理附件
        if attachments:
            for att in attachments:
                if "content" in att:
                    content = att["content"]
                    if isinstance(content, str):
                        content = content.encode("utf-8")
                elif "path" in att:
                    with open(att["path"], "rb") as f:
                        content = f.read()
                else:
                    continue
                filename = att.get("filename", "attachment")
                maintype, subtype = self._guess_mime(filename)
                msg.add_attachment(content, maintype=maintype, subtype=subtype, filename=filename)

        try:
            if cfg.use_ssl_smtp:
                context = ssl.create_default_context()
                with smtplib.SMTP_SSL(cfg.smtp_host, cfg.smtp_port, context=context) as server:
                    server.login(cfg.email, cfg.password)
                    all_rcpts = to + (cc or []) + (bcc or [])
                    server.send_message(msg, to_addrs=all_rcpts)
            else:
                with smtplib.SMTP(cfg.smtp_host, cfg.smtp_port) as server:
                    server.starttls(context=ssl.create_default_context())
                    server.login(cfg.email, cfg.password)
                    all_rcpts = to + (cc or []) + (bcc or [])
                    server.send_message(msg, to_addrs=all_rcpts)

            return {"success": True, "message": "发送成功", "message_id": msg.get("Message-ID")}
        except smtplib.SMTPAuthenticationError:
            return {"success": False, "message": "SMTP 认证失败，请检查邮箱/授权码"}
        except smtplib.SMTPRecipientsRefused as e:
            return {"success": False, "message": f"收件人被拒绝: {e}"}
        except smtplib.SMTPException as e:
            return {"success": False, "message": f"SMTP 错误: {e}"}
        except Exception as e:
            return {"success": False, "message": f"发送失败: {e}"}

    @staticmethod
    def _guess_mime(filename: str) -> tuple[str, str]:
        ext = filename.lower().split(".")[-1]
        mapping = {
            "pdf": ("application", "pdf"),
            "txt": ("text", "plain"),
            "csv": ("text", "csv"),
            "png": ("image", "png"),
            "jpg": ("image", "jpeg"),
            "jpeg": ("image", "jpeg"),
            "gif": ("image", "gif"),
            "zip": ("application", "zip"),
            "doc": ("application", "msword"),
            "docx": ("application", "vnd.openxmlformats-officedocument.wordprocessingml.document"),
            "xls": ("application", "vnd.ms-excel"),
            "xlsx": ("application", "vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
        }
        return mapping.get(ext, ("application", "octet-stream"))

    # ---------- IMAP 读取 ----------
    def read_emails(
        self,
        mailbox_id: str,
        folder: str = "INBOX",
        limit: int = 10,
        unread_only: bool = False,
    ) -> list[dict[str, Any]]:
        """
        读取邮件列表（仅元数据，不下载正文/附件以防大数据量）。
        返回每封邮件的: uid, subject, from, date, flags, size
        """
        cfg = self.get_mailbox(mailbox_id)
        if not cfg:
            return [{"error": f"未找到邮箱: {mailbox_id}"}]

        try:
            if cfg.use_ssl_imap:
                imap = imaplib.IMAP4_SSL(cfg.imap_host, cfg.imap_port)
            else:
                imap = imaplib.IMAP4(cfg.imap_host, cfg.imap_port)
                imap.starttls()

            imap.login(cfg.email, cfg.password)
            # 163/126/yeah.net IMAP 要求发送 ID 命令声明客户端身份，否则 SELECT 被拒
            if cfg.imap_host.endswith("163.com") or cfg.imap_host.endswith("126.com") or cfg.imap_host.endswith("yeah.net"):
                try:
                    tag = imap._new_tag()
                    imap.send(tag + b' ID ("name" "DFM-MCP" "version" "1.0")\r\n')
                    imap._get_response()  # 忽略返回，仅需发送
                except Exception:
                    pass  # ID 失败不阻塞主流程
            typ, _ = imap.select(folder)
            if typ != "OK":
                imap.logout()
                return [{"error": f"IMAP 选中文件夹失败: {folder}"}]

            criteria = "UNSEEN" if unread_only else "ALL"
            typ, data = imap.search(None, criteria)
            if typ != "OK" or not data[0]:
                imap.logout()
                return []

            uids = data[0].split()
            # 取最新的 limit 封（UID 递增，最新在末尾）
            uids = uids[-limit:] if len(uids) > limit else uids

            results = []
            for uid in reversed(uids):  # 最新在前
                typ, msg_data = imap.fetch(uid, "(RFC822.SIZE FLAGS ENVELOPE)")
                if typ != "OK":
                    continue

                # 解析 FETCH 返回
                raw = msg_data[0]
                size = 0
                flags = []
                envelope = None

                # raw 形如: b'1 (UID 123 RFC822.SIZE 456 FLAGS (\\Seen) ENVELOPE (...))'
                # 简单解析
                import re
                size_m = re.search(rb'RFC822\.SIZE\s+(\d+)', raw)
                if size_m:
                    size = int(size_m.group(1))
                flags_m = re.search(rb'FLAGS\s+\(([^)]*)\)', raw)
                if flags_m:
                    flags = flags_m.group(1).decode().split()
                env_m = re.search(rb'ENVELOPE\s*\(', raw)
                if env_m:
                    # 从 ENVELOPE 的 '(' 开始做括号配平扫描，避免贪婪匹配吃掉后续字段
                    start = env_m.start() + len(env_m.group(0)) - 1
                    depth = 0
                    in_quote = False
                    i = start
                    while i < len(raw):
                        ch = raw[i]
                        if ch == ord('"') and (i == 0 or raw[i-1] != ord('\\')):
                            in_quote = not in_quote
                        elif not in_quote:
                            if ch == ord('('):
                                depth += 1
                            elif ch == ord(')'):
                                depth -= 1
                                if depth == 0:
                                    break
                        i += 1
                    envelope = self._parse_envelope(raw[start:i+1])

                results.append({
                    "uid": uid.decode(),
                    "subject": envelope.get("subject", "") if envelope else "",
                    "from": envelope.get("from", "") if envelope else "",
                    "date": envelope.get("date", "") if envelope else "",
                    "flags": flags,
                    "size": size,
                })

            imap.logout()
            return results
        except imaplib.IMAP4.error as e:
            return [{"error": f"IMAP 认证/操作失败: {e}"}]
        except Exception as e:
            return [{"error": f"读取失败: {e}"}]

    @staticmethod
    def _parse_envelope(data: bytes) -> dict[str, str]:
        """Parse IMAP ENVELOPE response (RFC 3501).
        ENVELOPE format: (date subject from sender reply-to to cc bcc in-reply-to message-id)
        Each field can be NIL, a simple string (possibly MIME-encoded), or a parenthesized
        list of addresses. We extract date (field 1), subject (field 2), from (field 3).
        Pure bytes-safe parser.
        """
        result = {"date": "", "subject": "", "from": ""}
        try:
            from email.header import decode_header

            if not data or not data.startswith(b"("):
                return result

            # Remove outer parentheses
            inner = data[1:-1]

            # Split top-level fields; spaces inside nested parens or inside
            # quoted strings don't split.
            fields = []
            depth = 0
            in_quote = False
            current = []
            for ch in inner:
                if ch == 0x22 and not in_quote:  # opening "
                    in_quote = True
                    current.append(ch)
                elif ch == 0x22 and in_quote:  # closing "
                    in_quote = False
                    current.append(ch)
                elif not in_quote:
                    if ch == 0x28:  # '('
                        depth += 1
                        current.append(ch)
                    elif ch == 0x29:  # ')'
                        depth -= 1
                        current.append(ch)
                    elif ch == 0x20 and depth == 0:  # top-level space
                        fields.append(bytes(current) if current else b"NIL")
                        current = []
                        continue
                    else:
                        current.append(ch)
                else:
                    current.append(ch)
            fields.append(bytes(current) if current else b"NIL")

            # ENVELOPE has exactly 10 fields; pad or truncate
            while len(fields) < 10:
                fields.append(b"NIL")
            fields = fields[:10]

            def decode_mime(text: bytes) -> str:
                if text == b"NIL" or not text:
                    return ""
                # IMAP returns quoted strings; strip outer quotes if present
                if text.startswith(b'"') and text.endswith(b'"') and len(text) >= 2:
                    text = text[1:-1]
                decoded = decode_header(text.decode(errors="ignore"))
                return "".join(
                    s.decode(enc or "utf-8", errors="ignore") if isinstance(s, bytes) else s
                    for s, enc in decoded
                )

            def top_split(s: bytes) -> list[bytes]:
                """Split top-level fields by space, ignoring spaces inside
                nested parens and quoted strings."""
                out = []
                depth = 0
                in_q = False
                cur = []
                for ch in s:
                    if ch == 0x22:
                        in_q = not in_q
                        cur.append(ch)
                    elif not in_q:
                        if ch == 0x28:
                            depth += 1
                            cur.append(ch)
                        elif ch == 0x29:
                            depth -= 1
                            cur.append(ch)
                        elif ch == 0x20 and depth == 0:
                            out.append(bytes(cur) if cur else b"NIL")
                            cur = []
                        else:
                            cur.append(ch)
                    else:
                        cur.append(ch)
                out.append(bytes(cur) if cur else b"NIL")
                return out

            def parse_addr_list(addr_bytes: bytes) -> str:
                """Parse an IMAP address field into 'Name <email>' strings.
                Address field = "(" addr addr ... ")" where addr =
                (name adl mailbox host). Handles quoted + MIME parts.
                """
                if not addr_bytes or addr_bytes == b"NIL":
                    return ""
                s = addr_bytes.strip()
                if not s.startswith(b"("):
                    return decode_mime(s)
                # strip one level of outer parens
                if s.startswith(b"(") and s.endswith(b")"):
                    s = s[1:-1]
                # collect balanced groups "(...)" at depth 1
                groups = []
                i = 0
                while i < len(s):
                    if s[i] != 0x28:
                        i += 1
                        continue
                    start = i
                    depth = 0
                    in_q = False
                    while i < len(s):
                        ch = s[i]
                        if ch == 0x22:
                            in_q = not in_q
                        elif not in_q:
                            if ch == 0x28:
                                depth += 1
                            elif ch == 0x29:
                                depth -= 1
                                if depth == 0:
                                    groups.append(s[start:i + 1])
                                    i += 1
                                    break
                        i += 1
                    else:
                        break
                addresses = []
                for g in groups:
                    inner_g = g[1:-1] if g.startswith(b"(") and g.endswith(b")") else g
                    flds = top_split(inner_g)
                    while len(flds) < 4:
                        flds.append(b"NIL")
                    name = decode_mime(flds[0])
                    mailbox = decode_mime(flds[2])
                    host = decode_mime(flds[3])
                    if not mailbox and not host:
                        continue
                    email = f"{mailbox}@{host}" if mailbox and host else (mailbox or host)
                    addresses.append(f"{name} <{email}>" if name else email)
                return ", ".join(addresses)

            # Field 1 = date, Field 2 = subject, Field 3 = from
            if len(fields) > 0:
                result["date"] = decode_mime(fields[0])
            if len(fields) > 1:
                result["subject"] = decode_mime(fields[1])
            if len(fields) > 2:
                result["from"] = parse_addr_list(fields[2])

        except Exception:
            pass
        return result


# 单例实例
mail_client = MailClient()


def create_example_config():
    """创建示例配置文件"""
    example = [
        {
            "id": "personal",
            "name": "个人邮箱",
            "email": "your_email@qq.com",
            "smtp_host": "smtp.qq.com",
            "smtp_port": 465,
            "imap_host": "imap.qq.com",
            "imap_port": 993,
            "password": "***REPLACE_WITH_AUTH_CODE***",
            "use_ssl_smtp": True,
            "use_ssl_imap": True,
        },
        {
            "id": "work",
            "name": "工作邮箱",
            "email": "your_email@company.com",
            "smtp_host": "smtp.exmail.qq.com",
            "smtp_port": 465,
            "imap_host": "imap.exmail.qq.com",
            "imap_port": 993,
            "password": "***REPLACE_WITH_AUTH_CODE***",
            "use_ssl_smtp": True,
            "use_ssl_imap": True,
        },
    ]
    with (CONFIG_DIR / "mailboxes.example.json").open("w", encoding="utf-8") as f:
        json.dump(example, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    # 直接运行生成示例配置
    create_example_config()
    print(f"示例配置已生成: {CONFIG_DIR / 'mailboxes.example.json'}")
    print("请复制为 mailboxes.json 并填入真实授权码，或设置环境变量 MAIL_PASSWORD_<ID>")
# DFM_MCP_mail_service

一个基于官方 MCP SDK 的邮件服务端，向 MCP 客户端（Claude Desktop / Cursor 等）暴露三个工具：

| 工具 | 说明 |
|------|------|
| `list_bound_mailboxes` | 列出已绑定的邮箱（不含密码） |
| `add_bound_mailbox` | 新增绑定邮箱并持久化到 config/mailboxes.json |
| `send_email` | 通过 SMTP 发送邮件 |
| `read_email` | 通过 IMAP 读取邮箱邮件列表（仅元数据） |

## 环境要求
- Python >= 3.10（官方 `mcp` SDK 要求）
- 本项目已用 `uv` 建好 3.10.20 的 venv，位于 `.venv`

## 快速开始

```bash
# 1. 安装依赖（如未装）
.venv\Scripts\python.exe -m pip install -r requirements.txt

# 2. 绑定邮箱：复制示例配置为真实配置
copy config\mailboxes.example.json config\mailboxes.json
#    编辑 config\mailboxes.json，填入真实邮箱、SMTP/IMAP 服务器和授权码
#    或改用环境变量提供授权码：MAIL_PASSWORD_PERSONAL、MAIL_PASSWORD_WORK 等

# 3. 启动服务（stdio 传输，供本地 MCP 客户端连接）
.venv\Scripts\python.exe server.py

# 4. 端到端自测
.venv\Scripts\python.exe test_e2e_stdio.py
```

## 配置说明（config/mailboxes.json）
每个邮箱一条记录，字段如下：

| 字段 | 说明 |
|------|------|
| `id` | 唯一标识，工具调用时用（如 `personal`） |
| `name` | 显示名称 |
| `email` | 完整邮箱地址 |
| `smtp_host` / `smtp_port` | SMTP 服务器（465 走 SSL，587 走 STARTTLS） |
| `imap_host` / `imap_port` | IMAP 服务器（通常 993 SSL） |
| `password` | 邮箱**授权码**（不是登录密码），或填占位符后从环境变量 `MAIL_PASSWORD_<ID大写>` 读取 |
| `use_ssl_smtp` | true=SSL(465)，false=STARTTLS(587) |
| `use_ssl_imap` | 默认 true（SSL） |

> `config/mailboxes.json` 已加入 `.gitignore`，不会提交真实凭据。
> 各类邮箱需要先在邮箱设置里开启 SMTP/IMAP 服务并生成授权码（QQ 邮箱、163、Gmail 等各有对应设置）。

## 与 MCP 客户端对接（stdio）
在客户端的 MCP server 配置中注册本服务，命令形如：

```
.venv\Scripts\python.exe  D:\pycode\DFM_MCP_mail_service\server.py
```
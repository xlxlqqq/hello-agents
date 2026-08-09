"""邮件发送模块：用 163 邮箱 SMTP 发送纯文本简报。

只用标准库 smtplib + email，不引入额外依赖（符合 PRD 依赖最小化）。
授权码等敏感信息从 .env 读取，不硬编码。
"""

import os
import smtplib
from email.header import Header
from email.mime.text import MIMEText

from dotenv import load_dotenv

# 确保环境变量已加载（mailer 可能被单独 import，此时 agent.py 的 load_dotenv 还没跑）
load_dotenv()


def send_email(subject: str, body: str, recipients: list) -> str:
    """通过 163 SMTP 发送纯文本邮件，支持群发。

    Args:
        subject: 邮件主题
        body: 邮件正文（纯文本，简报 Markdown 原样作为正文）
        recipients: 收件人邮箱列表（群发）

    Returns:
        成功返回「已发送给 N 位收件人」；失败返回友好错误字符串，不抛异常。
    """
    username = os.getenv("MAIL_USERNAME")
    auth_code = os.getenv("MAIL_AUTH_CODE")
    smtp_host = os.getenv("MAIL_SMTP_HOST", "smtp.163.com")
    smtp_port = int(os.getenv("MAIL_SMTP_PORT", "465"))

    # 前置校验：授权码是 163 发邮件的必需项（不是登录密码）
    if not username or not auth_code or auth_code == "your-163-auth-code":
        return "未配置 MAIL_AUTH_CODE，请在 .env 填写 163 授权码（非登录密码）"
    if not recipients:
        return "收件人列表为空，请先用 add_recipient 添加收件人"

    try:
        # MIMEText 构建纯文本邮件：plain + utf-8 编码，中文不乱码
        msg = MIMEText(body, "plain", "utf-8")
        msg["From"] = username
        # 群发：To 头部用逗号拼接所有收件人（收件人可见彼此）
        msg["To"] = ", ".join(recipients)
        # Subject 用 Header 编码，避免中文主题乱码
        msg["Subject"] = Header(subject, "utf-8")

        # SMTP_SSL 直接连 465 端口（加密连接，163 推荐）
        with smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=30) as smtp:
            # 163 用授权码登录，login 第二个参数是授权码不是密码
            smtp.login(username, auth_code)
            smtp.sendmail(username, recipients, msg.as_string())

        return f"已发送给 {len(recipients)} 位收件人：{', '.join(recipients)}"
    except Exception as e:
        # 网络超时、授权码错误、收件人格式错等，统一转成字符串返回，不崩程序
        return f"邮件发送失败：{type(e).__name__}: {e}"

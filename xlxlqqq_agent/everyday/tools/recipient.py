from datetime import datetime

from tools.base import BaseTool
from tools.config import _load_config, _save_config


class ListRecipientsTool(BaseTool):
    name = "list_recipients"
    description = "查看当前简报邮件的收件人列表和每天的发送时间"

    def parameters(self):
        return {}

    def required(self):
        return []

    def run(self) -> str:
        try:
            config = _load_config()
            recipients = config.get("recipients", [])
            send_time = config.get("send_time", "08:00")
            if not recipients:
                return f"发送时间：{send_time}\n收件人：暂无"
            lines = [f"发送时间：{send_time}", "收件人："]
            for i, r in enumerate(recipients, 1):
                lines.append(f"  {i}. {r}")
            return "\n".join(lines)
        except Exception as e:
            return f"查询收件人失败：{type(e).__name__}: {e}"


class AddRecipientTool(BaseTool):
    name = "add_recipient"
    description = "新增一个简报邮件收件人（支持群发，自动去重）"

    def parameters(self):
        return {
            "email": {"type": "string", "description": "收件人邮箱地址，如 friend@example.com"}
        }

    def required(self):
        return ["email"]

    def run(self, email: str) -> str:
        try:
            email = email.strip()
            config = _load_config()
            recipients = config.get("recipients", [])
            if email in recipients:
                return f"收件人 {email} 已存在，无需重复添加"
            recipients.append(email)
            config["recipients"] = recipients
            _save_config(config)
            return f"已新增收件人：{email}（当前共 {len(recipients)} 位）"
        except Exception as e:
            return f"新增收件人失败：{type(e).__name__}: {e}"


class RemoveRecipientTool(BaseTool):
    name = "remove_recipient"
    description = "删除一个简报邮件收件人"

    def parameters(self):
        return {
            "email": {"type": "string", "description": "要删除的收件人邮箱地址"}
        }

    def required(self):
        return ["email"]

    def run(self, email: str) -> str:
        try:
            email = email.strip()
            config = _load_config()
            recipients = config.get("recipients", [])
            if email not in recipients:
                return f"收件人 {email} 不在列表中"
            recipients.remove(email)
            config["recipients"] = recipients
            _save_config(config)
            return f"已删除收件人：{email}（当前共 {len(recipients)} 位）"
        except Exception as e:
            return f"删除收件人失败：{type(e).__name__}: {e}"


class SetSendTimeTool(BaseTool):
    name = "set_send_time"
    description = "设置每天自动发送简报邮件的时间（24 小时制，格式 HH:MM）"

    def parameters(self):
        return {
            "time": {
                "type": "string",
                "description": "发送时间，24 小时制 HH:MM，如 08:00、09:30",
            }
        }

    def required(self):
        return ["time"]

    def run(self, time: str) -> str:
        try:
            time = time.strip()
            # 用 strptime 校验时间格式，非法格式会抛 ValueError
            datetime.strptime(time, "%H:%M")
            config = _load_config()
            config["send_time"] = time
            _save_config(config)
            return f"已将每天发送时间设为 {time}（--daemon 模式下到点自动发送）"
        except ValueError:
            return f"时间格式错误：{time}，请用 HH:MM 格式，如 08:00"
        except Exception as e:
            return f"设置发送时间失败：{type(e).__name__}: {e}"

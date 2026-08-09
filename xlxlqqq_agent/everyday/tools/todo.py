import json
import os
from datetime import datetime

from tools.base import BaseTool

# todo 数据持久化路径：data/todos.json
# 用绝对路径基于本文件位置推算，保证从任意工作目录启动都能找到数据文件
_DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
_TODOS_FILE = os.path.join(_DATA_DIR, "todos.json")


def _ensure_data_dir():
    """确保 data 目录存在（首次运行时自动创建）"""
    os.makedirs(_DATA_DIR, exist_ok=True)


def _load_todos() -> list:
    """读取 todo 列表，文件不存在或损坏时返回空列表，不崩程序"""
    _ensure_data_dir()
    if not os.path.exists(_TODOS_FILE):
        return []
    try:
        with open(_TODOS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return []


def _save_todos(todos: list):
    """写入 todo 列表"""
    _ensure_data_dir()
    with open(_TODOS_FILE, "w", encoding="utf-8") as f:
        json.dump(todos, f, ensure_ascii=False, indent=2)


def _next_id(todos: list) -> str:
    """生成自增 id，格式 todo_001、todo_002..."""
    max_num = 0
    for t in todos:
        # 解析现有 id 的数字部分，取最大值 +1
        try:
            num = int(t.get("id", "todo_0").replace("todo_", ""))
            max_num = max(max_num, num)
        except ValueError:
            continue
    return f"todo_{max_num + 1:03d}"


class ListTodoTool(BaseTool):
    name = "list_todos"
    description = "查看当前所有待办事项"

    def parameters(self):
        return {}

    def required(self):
        return []

    def run(self) -> str:
        try:
            todos = _load_todos()
            if not todos:
                return "当前没有待办事项"
            # 格式化成易读的列表，未完成在前
            lines = []
            for t in todos:
                status = "✓" if t.get("done") else " "
                priority = t.get("priority", "normal")
                lines.append(f"[{status}] {t['id']} [{priority}] {t['content']}")
            return "\n".join(lines)
        except Exception as e:
            return f"查询待办失败：{type(e).__name__}: {e}"


class AddTodoTool(BaseTool):
    name = "add_todo"
    description = "新增一条待办事项"

    def parameters(self):
        return {
            "content": {"type": "string", "description": "待办内容，如 下午3点开会"},
            "priority": {
                "type": "string",
                "enum": ["high", "normal", "low"],
                "description": "优先级：high=高 / normal=中 / low=低",
            },
        }

    def required(self):
        return ["content"]

    def run(self, content: str, priority: str = "normal") -> str:
        try:
            todos = _load_todos()
            todo = {
                "id": _next_id(todos),
                "content": content,
                "priority": priority,
                "done": False,
                "created_at": datetime.now().isoformat(timespec="seconds"),
            }
            todos.append(todo)
            _save_todos(todos)
            return f"已新增待办：{todo['id']} [{priority}] {content}"
        except Exception as e:
            return f"新增待办失败：{type(e).__name__}: {e}"


class CompleteTodoTool(BaseTool):
    name = "complete_todo"
    description = "标记某条待办为已完成"

    def parameters(self):
        return {
            "todo_id": {"type": "string", "description": "待办 id，如 todo_001"}
        }

    def required(self):
        return ["todo_id"]

    def run(self, todo_id: str) -> str:
        try:
            todos = _load_todos()
            for t in todos:
                if t["id"] == todo_id:
                    t["done"] = True
                    _save_todos(todos)
                    return f"已标记完成：{todo_id} {t['content']}"
            return f"未找到待办：{todo_id}"
        except Exception as e:
            return f"标记完成失败：{type(e).__name__}: {e}"


class DeleteTodoTool(BaseTool):
    name = "delete_todo"
    description = "删除某条待办事项"

    def parameters(self):
        return {
            "todo_id": {"type": "string", "description": "待办 id，如 todo_001"}
        }

    def required(self):
        return ["todo_id"]

    def run(self, todo_id: str) -> str:
        try:
            todos = _load_todos()
            for i, t in enumerate(todos):
                if t["id"] == todo_id:
                    removed = todos.pop(i)
                    _save_todos(todos)
                    return f"已删除待办：{removed['id']} {removed['content']}"
            return f"未找到待办：{todo_id}"
        except Exception as e:
            return f"删除待办失败：{type(e).__name__}: {e}"

"""
pytest 全局配置与 fixture（DocGuard Agent 测试共享）
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# 将项目根目录加入 sys.path（确保 test 模块能 import 项目代码）
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


@pytest.fixture
def project_root() -> Path:
    """项目根目录"""
    return PROJECT_ROOT


@pytest.fixture
def test_data_dir() -> Path:
    """测试数据目录"""
    return PROJECT_ROOT / "tests" / "test_data"


@pytest.fixture
def output_tmp_dir(tmp_path: Path) -> Path:
    """临时输出目录（pytest 自动清理）"""
    return tmp_path


# ============================================================
# 文档构造 fixture（从 test_document.fixtures 导入，跨目录共享）
# 放在顶层 conftest 可被 tests/ 下任意子目录使用。
# ============================================================

@pytest.fixture
def sample_docx_path(tmp_path: Path) -> Path:
    """基础样本文档 DOCX 路径（默认含错别字 & 普通段落，供 Review/Repair 测试）。

    注：若需要"完全干净无任何 issue"的文档，请显式使用 clean_sample_docx_path。
    """
    from tests.test_document.fixtures import create_sample_docx
    return create_sample_docx(tmp_path / "sample.docx")


@pytest.fixture
def clean_sample_docx_path(tmp_path: Path) -> Path:
    """干净样本：不含错别字、字号正确的 DOCX 路径（供 Validation no-issue 场景）。"""
    from tests.test_document.fixtures import create_sample_docx
    return create_sample_docx(
        tmp_path / "clean_sample.docx",
        with_format_issues=False,
    )


@pytest.fixture
def sample_typo_docx_path(tmp_path: Path) -> Path:
    """带错别字/格式问题的 DOCX 路径（适合 Review/Repair/Validation 场景）。

    with_format_issues=True 会故意使用错误字号，并自带含有错别字的段落
    （"格试问题"、"总杰" 等，见 fixtures.create_sample_docx）。
    """
    from tests.test_document.fixtures import create_sample_docx
    return create_sample_docx(
        tmp_path / "typo.docx",
        with_format_issues=True,
    )


@pytest.fixture
def complex_docx_path(tmp_path: Path) -> Path:
    """复杂文档 DOCX 路径（多表格/标题）"""
    from tests.test_document.fixtures import create_complex_docx
    return create_complex_docx(tmp_path / "complex.docx")

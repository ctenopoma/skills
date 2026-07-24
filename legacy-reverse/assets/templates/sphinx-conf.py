# docs-sphinx/conf.py のテンプレート（⓪でプロジェクト名等を埋めて配置する）
# 依存: python -m pip install sphinx sphinx-rtd-theme
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

project = "{{プロジェクト名}} 新コード詳細仕様"
language = "ja"
extensions = ["sphinx.ext.autodoc", "sphinx.ext.napoleon", "sphinx.ext.viewcode"]
html_theme = "sphinx_rtd_theme"  # Read the Docs テーマ（Python系docsの定番）
html_theme_options = {
    "navigation_depth": 3,
    "collapse_navigation": False,
}
autodoc_member_order = "bysource"
napoleon_google_docstring = True
napoleon_custom_sections = [("Side Effects", "params_style")]

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path


def _load_qtpdf():
    script = Path(__file__).resolve().parents[1] / "scripts" / "qtpdf.py"
    spec = spec_from_file_location("qtpdf_under_test", script)
    module = module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


qtpdf = _load_qtpdf()


def test_shadow_keeps_real_h1_sections_with_frontmatter_title(tmp_path):
    src = tmp_path / "manual.md"
    src.write_text(
        """---
title: \"legacy-reverse マニュアル\"
---

# 1. 何をするツールか

本文

## 1.1 詳細
""",
        encoding="utf-8",
    )

    shadow = qtpdf.make_shadow(src)
    text = shadow.read_text(encoding="utf-8")

    assert "shift-heading-level-by" not in text
    assert "# 1. 何をするツールか" in text


def test_shadow_promotes_leading_h1_title_and_shifts_sections(tmp_path):
    src = tmp_path / "readme.md"
    src.write_text(
        """# Sample Document

## Overview

本文
""",
        encoding="utf-8",
    )

    shadow = qtpdf.make_shadow(src)
    text = shadow.read_text(encoding="utf-8")

    assert 'title: "Sample Document"' in text
    assert "shift-heading-level-by: -1" in text
    assert "# Sample Document" not in text


def test_shadow_shifts_h2_sections_when_title_is_only_in_frontmatter(tmp_path):
    src = tmp_path / "notes.md"
    src.write_text(
        """---
title: \"Notes\"
---

## Overview

本文
""",
        encoding="utf-8",
    )

    shadow = qtpdf.make_shadow(src)
    text = shadow.read_text(encoding="utf-8")

    assert "shift-heading-level-by: -1" in text
    assert "## Overview" in text
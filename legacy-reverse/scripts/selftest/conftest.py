"""pytest から selftest を回すための共有フィクスチャ。

test_hazards.py は「pytest 不要・素の assert」で書かれており、`python test_hazards.py`
で走らせると main() が1つの TemporaryDirectory を作って各テストに `tmp` として渡す。
pytest で収集したときは main() を通らないため `tmp` が引数のまま残り、
fixture 'tmp' not found で13件まるごとエラーになっていた（＝ハザード機構のテストが
CI 相当のコマンドで一度も実行されない状態）。

同名のフィクスチャをここで用意して、どちらの起動方法でも同じテストが動くようにする。
pytest 側はテストごとに新しい一時ディレクトリを渡す（main() の共有 tmp より厳しい条件。
各テストはプロジェクト名を分けているので、共有・非共有のどちらでも成立する）。
"""
import tempfile
from pathlib import Path

import pytest


@pytest.fixture
def tmp():
    with tempfile.TemporaryDirectory() as td:
        yield Path(td)

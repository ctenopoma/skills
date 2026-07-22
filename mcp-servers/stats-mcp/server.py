"""統計計算 MCP サーバ(scipy / scikit-learn ベース)。

- 準備:      pip install "mcp[cli]" pandas scipy scikit-learn
- STDIO:     python server.py
- HTTP:      python server.py --http   (http://127.0.0.1:8000/mcp)

設計方針: LLM に計算させない。検定・相関・モデル評価のような「答えが一意に決まる計算」は
すべてこのサーバで行い、Claude は数値の解釈だけを担当する。
各ツールは統計量・p値などの数値を返すだけで、結論の断定はしない。
"""

import sys

import pandas as pd
from scipy import stats

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("stats")


def _load(csv_path: str) -> pd.DataFrame:
    return pd.read_csv(csv_path)


@mcp.tool()
def compare_groups(csv_path: str, value_col: str, group_col: str) -> str:
    """グループ間で数値列の平均に差があるかを検定する。
    2群なら Welch の t 検定、3群以上なら一元配置分散分析(ANOVA)を自動選択。
    「AとBで差があるか?」という問いに使う。

    Args:
        csv_path: CSV ファイルのパス
        value_col: 比較する数値列
        group_col: グループを表す列
    """
    df = _load(csv_path)
    groups = [g.dropna() for _, g in df.groupby(group_col)[value_col]]
    names = [str(k) for k, _ in df.groupby(group_col)[value_col]]
    desc = "\n".join(
        f"- {n}: n={len(g)}, mean={g.mean():.4g}, std={g.std():.4g}"
        for n, g in zip(names, groups)
    )
    if len(groups) == 2:
        stat, p = stats.ttest_ind(groups[0], groups[1], equal_var=False)
        method = "Welch の t 検定"
    else:
        stat, p = stats.f_oneway(*groups)
        method = f"一元配置 ANOVA({len(groups)} 群)"
    return (
        f"{method}\n{desc}\n統計量 = {stat:.4g}, p 値 = {p:.4g}\n"
        "(有意水準・多重比較の扱いは分析の文脈に応じて判断すること)"
    )


@mcp.tool()
def test_independence(csv_path: str, col_a: str, col_b: str) -> str:
    """2つのカテゴリ列が独立かをカイ二乗検定で調べる。
    「属性Aと属性Bに関連があるか?」という問いに使う。

    Args:
        csv_path: CSV ファイルのパス
        col_a: カテゴリ列1
        col_b: カテゴリ列2
    """
    df = _load(csv_path)
    table = pd.crosstab(df[col_a], df[col_b])
    chi2, p, dof, _ = stats.chi2_contingency(table)
    small = (table.values < 5).mean()
    warn = "\n注意: 期待度数5未満のセルが多く、結果の信頼性が低い" if small > 0.2 else ""
    return (
        f"カイ二乗検定(クロス表 {table.shape[0]}×{table.shape[1]})\n"
        f"χ² = {chi2:.4g}, 自由度 = {dof}, p 値 = {p:.4g}{warn}"
    )


@mcp.tool()
def correlation(csv_path: str, columns: str = "", method: str = "pearson") -> str:
    """数値列同士の相関行列を返す。関係の当たりを付けるのに使う。

    Args:
        csv_path: CSV ファイルのパス
        columns: 対象列(カンマ区切り。省略時は全数値列)
        method: "pearson"(線形) / "spearman"(順位・非線形も拾う)
    """
    df = _load(csv_path)
    cols = [c.strip() for c in columns.split(",") if c.strip()] or None
    num = df[cols] if cols else df.select_dtypes("number")
    corr = num.corr(method=method).round(3)
    return f"相関行列({method})\n{corr.to_string()}"


@mcp.tool()
def check_normality(csv_path: str, column: str) -> str:
    """数値列が正規分布とみなせるかを Shapiro-Wilk 検定で調べる。
    パラメトリック検定を使ってよいかの下調べに使う。

    Args:
        csv_path: CSV ファイルのパス
        column: 対象の数値列
    """
    x = _load(csv_path)[column].dropna()
    n = len(x)
    sample = x.sample(5000, random_state=0) if n > 5000 else x
    stat, p = stats.shapiro(sample)
    note = f"(n={n} が大きいため 5000 件に抽出して検定)" if n > 5000 else f"(n={n})"
    return (
        f"Shapiro-Wilk 検定 {note}\nW = {stat:.4g}, p 値 = {p:.4g}\n"
        f"歪度 = {stats.skew(x):.3g}, 尖度 = {stats.kurtosis(x):.3g}"
    )


@mcp.tool()
def quick_fit(csv_path: str, target: str, features: str = "") -> str:
    """ベースラインモデルを交差検証付きで学習し、性能と特徴量重要度を返す。
    目的変数が数値なら回帰(RandomForest)、カテゴリなら分類を自動選択。
    「どの変数が効いていそうか」「そもそも予測できそうか」の当たりを付けるのに使う。
    本格的なモデリングの代わりではない。

    Args:
        csv_path: CSV ファイルのパス
        target: 目的変数の列
        features: 説明変数(カンマ区切り。省略時は他の全数値列)
    """
    from sklearn.ensemble import RandomForestClassifier, RandomForestRegressor
    from sklearn.model_selection import cross_val_score

    df = _load(csv_path).dropna(subset=[target])
    y = df[target]
    cols = [c.strip() for c in features.split(",") if c.strip()]
    X = df[cols] if cols else df.drop(columns=[target]).select_dtypes("number")
    X = X.fillna(X.median(numeric_only=True))
    is_reg = pd.api.types.is_numeric_dtype(y) and y.nunique() > 10
    if is_reg:
        model, scoring, label = RandomForestRegressor(n_estimators=200, random_state=0), "r2", "回帰(R²)"
    else:
        model, scoring, label = RandomForestClassifier(n_estimators=200, random_state=0), "accuracy", "分類(accuracy)"
    scores = cross_val_score(model, X, y, cv=5, scoring=scoring)
    model.fit(X, y)
    imp = sorted(zip(X.columns, model.feature_importances_), key=lambda t: -t[1])[:10]
    imp_txt = "\n".join(f"- {c}: {v:.3f}" for c, v in imp)
    return (
        f"RandomForest ベースライン — {label}\n"
        f"5-fold CV スコア: {scores.mean():.4g} ± {scores.std():.3g}\n"
        f"特徴量重要度(上位10):\n{imp_txt}\n"
        "(重要度は相関的な寄与であり因果ではない)"
    )


if __name__ == "__main__":
    if "--http" in sys.argv:
        mcp.run(transport="streamable-http")
    else:
        mcp.run()  # 既定は STDIO

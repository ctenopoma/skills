#!/usr/bin/env python3
"""レンダリング済みの docs/_site をローカルホストで配信する（WBSがトップ）。

`python -m http.server` を直接使わずにこれを通す理由:

  - **プロジェクトごとに固定ポート**: ポートをプロジェクト名から決めるので、複数の
    移植プロジェクトを同時に立てても衝突しない（衝突したら空きへずらす）。
    レビュアーは毎回同じ URL をブックマークできる。
  - **127.0.0.1 のみに bind**: 仕様書は社内資料なので、既定で LAN に漏らさない。
    共有したいときだけ明示的に `--host 0.0.0.0`（警告つき）。
  - **キャッシュ無効**: 再レンダリング後にブラウザの更新だけで最新が見える
    （no-store。ブラウザが古い .html/.js を握ったままになるのを防ぐ）。
  - **--watch**: docs/ の .md を書き換えたら自動で再レンダリング。
    ⑤の結果を貼りながら WBS を眺める用。
  - **EXE 化できる**: このファイルが PyInstaller のエントリポイント。
    `build_viewer.py` で _site を同梱した単体実行ファイルにできるので、
    Python も Quarto も入っていない相手に「実行するだけ」で渡せる。

使い方（開発機）:
    python serve_site.py --root .              # docs/_site を配信してブラウザを開く
    python serve_site.py --root . --render     # 先に render_site.py で作り直してから配信
    python serve_site.py --root . --watch      # docs/ の変更を監視して自動再レンダリング
    python serve_site.py --root . --port 8800 --no-open

配布用 EXE（_site 同梱。build_viewer.py が作る）:
    wbs-viewer.exe                             # ダブルクリック → ブラウザが開く
    wbs-viewer.exe --port 9000 --no-open
"""
import argparse
import functools
import http.server
import json
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import webbrowser
import zlib
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))   # 隣の *.py を import できるように

FROZEN = getattr(sys, "frozen", False)
BUNDLE = Path(getattr(sys, "_MEIPASS", "")) if FROZEN else None
CONFIG_NAME = ".viewer.json"          # build_viewer.py が同梱サイトに書く { name, port }
WATCH_SUFFIXES = {".md", ".qmd", ".css", ".yml", ".yaml", ".json", ".png", ".svg"}

# 配信するものは Quarto の出力（html/js/css/woff/json）。Windows のレジストリ由来の
# mimetypes は当てにならない（.js が text/plain になって mermaid が動かない環境がある）
EXTRA_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
    ".ttf": "font/ttf",
    ".otf": "font/otf",
    ".map": "application/json; charset=utf-8",
}


def use_utf8_console() -> None:
    """Windows の cp932 コンソールで日本語を print しても落ちないようにする。

    line_buffering は必須。EXE の出力をファイルやパイプに流したとき、既定のブロック
    バッファだと URL が表示されないまま溜まる（サーバは動いているのに無反応に見える）。
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
        except (AttributeError, OSError):
            pass


# --- サイトの場所とポート -------------------------------------------------

def resolve_site(args) -> tuple[Path, list[Path]]:
    """配信するディレクトリを決める。EXE では同梱サイト → exe の隣 → --root の順。

    戻り値は (採用したディレクトリ, 探した場所)。見つからなかったときの案内に使う。
    """
    if args.site:
        return Path(args.site).expanduser().resolve(), []
    tried = []
    if FROZEN:
        tried.append(BUNDLE / "_site")                       # 同梱版
        tried.append(Path(sys.executable).resolve().parent / "_site")   # EXE の隣
    tried.append(Path(args.root).expanduser().resolve() / "docs" / "_site")
    for cand in tried:
        if (cand / "index.html").exists():
            return cand, tried
    return tried[-1], tried


def site_config(site: Path) -> dict:
    try:
        return json.loads((site / CONFIG_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def lan_address() -> str | None:
    """LAN 側の自分の IP。--host 0.0.0.0 のときに配る URL を出すために使う。

    gethostbyname(gethostname()) は環境次第で 127.0.0.1 しか返さないので、
    外向きの経路を選ばせて出口側のアドレスを読む（UDP なのでパケットは飛ばない）。
    """
    for probe in ("192.0.2.1", "8.8.8.8"):           # TEST-NET-1 → 到達不能でも経路選択はされる
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.connect((probe, 9))
                ip = s.getsockname()[0]
            if not ip.startswith("127."):
                return ip
        except OSError:
            continue
    return None


def default_port(key: str) -> int:
    """プロジェクト名から 8100-8899 の固定ポートを決める（プロジェクトごとに別ポート）。"""
    return 8100 + zlib.crc32(key.encode("utf-8")) % 800


def bind_server(host: str, port: int, handler, tries: int = 40):
    """port から順に空きを探して bind する。0 は OS 任せ。"""
    last = None
    for candidate in ([0] if port == 0 else range(port, port + tries)):
        try:
            return http.server.ThreadingHTTPServer((host, candidate), handler)
        except OSError as e:
            last = e
    raise SystemExit(f"error: ポート {port}〜{port + tries - 1} が全部埋まっている（{last}）")


# --- バッチのライブ進捗ページ（/pipeline.html。Quarto を通さず JSON をポーリング） ---

PIPELINE_PAGE = """<!DOCTYPE html><html lang="ja"><head><meta charset="utf-8">
<title>バッチ実行状況</title>
<style>
:root{color-scheme:light dark}
body{font-family:"Segoe UI",system-ui,sans-serif;max-width:1080px;margin:24px auto;padding:0 16px;
     color:#1f2937;background:#fff;line-height:1.6}
@media(prefers-color-scheme:dark){body{color:#e5e7eb;background:#111827}
 .card{background:#1f2937!important}.recent tr:hover{background:#374151!important}}
h1{font-size:1.3rem;margin:0 0 4px}
a{color:#4f46e5}
.muted{color:#9ca3af;font-size:.85rem}
.badge{display:inline-block;padding:2px 12px;border-radius:999px;font-weight:600;font-size:.9rem}
.running{background:#dcfce7;color:#166534}.waiting{background:#fef9c3;color:#854d0e}
.stopped{background:#fee2e2;color:#991b1b}.finished{background:#dbeafe;color:#1e40af}
.none{background:#e5e7eb;color:#374151}
.cards{display:flex;flex-wrap:wrap;gap:12px;margin:16px 0}
.card{background:#f9fafb;border-radius:10px;padding:10px 16px;min-width:120px}
.card .v{font-size:1.25rem;font-weight:700}.card .k{font-size:.78rem;color:#9ca3af}
.bar{height:10px;background:#e5e7eb;border-radius:5px;overflow:hidden;margin:6px 0}
.bar>div{height:100%;background:#4f46e5;transition:width .5s}
.bar>div.f{background:#dc2626}
table{border-collapse:collapse;width:100%;font-size:.88rem;margin:8px 0}
th,td{padding:5px 10px;border-bottom:1px solid #37415122;text-align:left;vertical-align:top}
.ok{color:#16a34a;font-weight:700}.ng{color:#dc2626;font-weight:700}
details pre{white-space:pre-wrap;background:#37415115;padding:8px;border-radius:6px;
            font-size:.8rem;max-height:220px;overflow:auto}
details summary{cursor:pointer}
.ngdetail{background:#fee2e2;border-radius:8px;padding:8px 12px;color:#991b1b;margin-top:6px}
.ngdetail ul{margin:4px 0 0;padding-left:20px}
@media(prefers-color-scheme:dark){.ngdetail{background:#7f1d1d33;color:#fca5a5}}
</style></head><body>
<h1>バッチ実行状況 <span id="state" class="badge none">--</span></h1>
<div class="muted"><a href="/">← WBS へ</a> ／ 2.5秒ごとに自動更新 ／
  最終更新: <span id="upd">--</span></div>
<div class="card" style="margin:14px 0;padding:12px 16px">
  <div style="font-weight:600;margin-bottom:6px">連続実行（①〜⑤の機械実行を自動で回す）</div>
  <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center">
    <input id="bmax" type="number" min="1" placeholder="上限件数（空=全部）"
           style="width:150px;padding:5px 8px;border:1px solid #9ca3af;border-radius:6px">
    <input id="bbudget" type="number" min="0" step="0.5" placeholder="予算$（空=無制限）"
           style="width:150px;padding:5px 8px;border:1px solid #9ca3af;border-radius:6px">
    <button onclick="batchStart()" style="background:#4f46e5;color:#fff;border:0;
            border-radius:6px;padding:7px 16px;font-weight:600;cursor:pointer">開始</button>
    <button onclick="batchStop()" style="background:#fff;border:1px solid #991b1b;color:#991b1b;
            border-radius:6px;padding:7px 16px;cursor:pointer">停止</button>
    <span id="bmsg" class="muted"></span>
  </div>
  <div class="muted" style="margin-top:4px">承認待ち（①②）と裁定待ち（⑤⛔）はスキップして進みます。
    実行中もWBS上で承認・裁定でき、次の走査で自動的に拾われます。</div>
</div>
<div id="now" style="margin:14px 0;font-size:1.05rem"></div>
<div class="bar"><div id="barok" style="width:0%"></div></div>
<div id="counts" class="muted"></div>
<div class="cards" id="cards"></div>
<div id="ngbox"></div>
<h2 style="font-size:1.05rem">直近の結果</h2>
<table class="recent"><thead><tr><th>時刻</th><th>関数</th><th>結果</th><th>試行</th>
<th>秒</th><th>ターン</th><th>$</th><th>内容</th></tr></thead><tbody id="recent"></tbody></table>
<script>
const esc = s => (s??"").toString().replace(/[&<>"]/g, c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
const fmtEta = s => s==null ? "--" : (s>=3600 ? Math.floor(s/3600)+"時間"+Math.round(s%3600/60)+"分" : Math.round(s/60)+"分");
const STATES = {running:["実行中","running"], waiting_rate:["レート待機中","waiting"],
                stopped:["停止","stopped"], finished:["完了","finished"], not_running:["未実行","none"]};
async function tick(){
  let d;
  try{ d = await (await fetch("/pipeline-status.json",{cache:"no-store"})).json(); }
  catch(e){ return; }
  const [label, cls] = STATES[d.state] || [d.state,"none"];
  const st = document.getElementById("state");
  st.textContent = label + (d.reason ? "：" + d.reason : ""); st.className = "badge " + cls;
  document.getElementById("upd").textContent = d.updated_at || "--";
  if(d.state === "not_running"){
    document.getElementById("now").textContent = "パイプラインはまだ実行されていません（pipeline.py 実行中にこのページが更新されます）";
    return;
  }
  const total = d.total_targets||0, done = d.done||0, failed = d.failed||0;
  let now = "";
  if(d.state === "waiting_rate"){
    now = "⏸ レートリミット/利用枠の回復待ち（" + esc(d.wait_until||"") + " に再開予定）";
  } else if(d.current){
    const t0 = new Date(d.current.started_at), el = Math.max(0, Math.round((Date.now()-t0)/1000));
    now = "▶ いま <b>" + esc(d.current.func_id) + "</b> を実行中（試行" + d.current.attempt +
          "・" + Math.floor(el/60) + "分" + el%60 + "秒経過）";
  }
  document.getElementById("now").innerHTML = now;
  document.getElementById("barok").style.width = total ? (100*(done+failed)/total)+"%" : "0%";
  document.getElementById("counts").textContent =
    `処理 ${done+failed} / ${total} 件（成功 ${done}・失敗 ${failed}・残り ${Math.max(total-done-failed,0)}）`;
  const m = d.metrics||{};
  document.getElementById("cards").innerHTML = [
    ["成功率", m.success_rate!=null ? Math.round(m.success_rate*100)+"%" : "--"],
    ["中央値/件", m.median_sec!=null ? Math.round(m.median_sec)+"s" : "--"],
    ["残り見込み", fmtEta(m.eta_sec)],
    ["累計コスト", "$"+(d.cost_usd??0).toFixed(2) + (d.budget_usd ? " / $"+d.budget_usd : "")],
    ["平均コスト/件", m.avg_cost_usd!=null ? "$"+m.avg_cost_usd.toFixed(3) : "--"],
    ["レート待機", (d.rate_waited_sec||0)+"s"],
  ].map(([k,v])=>`<div class="card"><div class="v">${v}</div><div class="k">${k}</div></div>`).join("");
  const ng = Object.entries(d.ng_kinds||{});
  document.getElementById("ngbox").innerHTML = !ng.length ? "" :
    "<h2 style='font-size:1.05rem'>失敗の内訳</h2><table>" +
    ng.sort((a,b)=>b[1]-a[1]).map(([k,v])=>`<tr><td>${esc(k)}</td><td><b>${v}</b> 件</td></tr>`).join("") +
    "</table>";
  renderRecent(d.recent||[]);
}

// 「直近の結果」は中身が変わった時だけ再描画する。2.5秒ごとに innerHTML を
// 丸ごと差し替えると、人が読もうと開いた <details> が毎回リセットされて
// 「チカチカして即閉じる」ことになるため（報告された不具合）。
// 変化があって再描画する場合も、開いていた行は開いたまま復元する。
let lastRecentJson = null;
function renderRecent(recent){
  const json = JSON.stringify(recent);
  if(json === lastRecentJson) return;      // 何も変わっていなければ触らない
  lastRecentJson = json;
  const box = document.getElementById("recent");
  const openIds = new Set([...box.querySelectorAll("details[open]")].map(el => el.dataset.rid));
  box.innerHTML = recent.map((r, i) => {
    const rid = r.func_id + "|" + (r.at || i);
    const res = r.ok ? '<span class="ok">OK</span>' : '<span class="ng">NG</span>';
    let body = "";
    if(!r.ok){
      const problems = r.problems || [];
      const ngbox = problems.length
        ? `<div class="ngdetail"><b>${esc(r.kind||"")}（${problems.length}件）</b>` +
          `<ul>${problems.map(p => `<li>${esc(p)}</li>`).join("")}</ul></div>`
        : (r.why ? `<p>${esc(r.why)}</p>` : "");
      body = `<details${openIds.has(rid) ? " open" : ""} data-rid="${esc(rid)}">` +
        `<summary>${esc(r.kind||"")}${problems.length ? "" : (r.why ? ": " + esc(r.why) : "")}</summary>` +
        ngbox +
        `<pre>${esc(r.tail || "(応答記録なし)")}</pre>` +
        `<a href="/agent-logs/${esc(r.func_id)}.txt" target="_blank">応答全文を開く</a></details>`;
    }
    return `<tr><td class="muted">${esc((r.at||"").slice(11))}</td><td>${esc(r.func_id)}</td>` +
           `<td>${res}</td><td>${r.attempt||""}</td><td>${r.sec??""}</td>` +
           `<td>${r.num_turns??""}</td><td>${r.cost_usd!=null ? r.cost_usd.toFixed(2):""}</td>` +
           `<td>${body}</td></tr>`;
  }).join("");
}
function batchPost(url, body){
  const msg = document.getElementById("bmsg");
  fetch(url, {method:"POST", headers:{"Content-Type":"application/json"},
              body: JSON.stringify(body || {})})
    .then(r => r.json())
    .then(d => { msg.textContent = d.message || (d.ok ? "OK" : "失敗"); tick(); })
    .catch(e => { msg.textContent = "通信エラー: " + e
                  + "（serve_site.py で配信していますか？）"; });
}
function batchStart(){
  batchPost("/run-batch", {max_funcs: +document.getElementById("bmax").value || 0,
                           budget_usd: +document.getElementById("bbudget").value || 0});
}
function batchStop(){ batchPost("/run-cancel", {}); }
tick(); setInterval(tick, 2500);
</script></body></html>
"""


# --- HTTP ハンドラ --------------------------------------------------------

class Handler(http.server.SimpleHTTPRequestHandler):
    server_version = "wbs-viewer"
    sys_version = ""
    extensions_map = {**http.server.SimpleHTTPRequestHandler.extensions_map, **EXTRA_TYPES}
    verbose = False
    state_root: Path = None      # プロジェクトルート（.legacy-reverse のライブ状態の読み元）

    def _send_bytes(self, body: bytes, ctype: str, code: int = 200) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, code: int, data: dict) -> None:
        self._send_bytes(json.dumps(data, ensure_ascii=False).encode("utf-8"),
                         "application/json; charset=utf-8", code)

    def do_GET(self) -> None:
        if self.path == "/favicon.ico" and not (Path(self.directory) / "favicon.ico").exists():
            self.send_response(204)                  # ブラウザが必ず取りに来る。404 でログを汚さない
            self.end_headers()
            return
        # --- バッチのライブ進捗（pipeline.py が書く状態ファイルをそのまま配る） ---
        if self.path in ("/pipeline", "/pipeline.html"):
            self._send_bytes(PIPELINE_PAGE.encode("utf-8"), "text/html; charset=utf-8")
            return
        if self.path == "/pipeline-status.json":
            body = b'{"state": "not_running"}'
            if self.state_root:
                sp = self.state_root / ".legacy-reverse" / "pipeline-status.json"
                try:
                    body = sp.read_bytes()
                except OSError:
                    pass
            self._send_bytes(body, "application/json; charset=utf-8")
            return
        if self.path.startswith("/agent-logs/"):
            name = self.path.rsplit("/", 1)[-1]
            if self.state_root and re.fullmatch(r"[\w.\-]+\.txt", name):
                lp = self.state_root / ".legacy-reverse" / "agent-logs" / name
                if lp.is_file():
                    self._send_bytes(lp.read_bytes(), "text/plain; charset=utf-8")
                    return
            self.send_error(404)
            return
        super().do_GET()

    WRITE_ROUTES = ("/review-action", "/run-phase", "/run-check", "/run-cancel",
                    "/run-batch", "/run-analyze")
    LOCAL_NAMES = ("127.0.0.1", "localhost", "::1")

    def _cross_site_reason(self) -> str | None:
        """DNSリバインディング・クロスサイトのフォームPOST対策。問題があれば理由を返す。

        接続元 127.0.0.1 の確認だけでは、(a) 攻撃者ドメインのDNSを 127.0.0.1 に
        切り替えて同一オリジンで fetch させる、(b) text/plain フォームで preflight
        なしに JSON を送り込む、の2経路が残る。Host と Origin のホスト名が
        ローカルホストであることまで確認して両方を塞ぐ。
        """
        host = self.headers.get("Host") or ""
        try:
            hostname = urlsplit("//" + host).hostname
        except ValueError:
            hostname = None
        if hostname not in self.LOCAL_NAMES:
            return f"不正な Host ヘッダ（{host or 'なし'}）"
        origin = self.headers.get("Origin")
        if origin:
            try:
                oh = urlsplit(origin).hostname
            except ValueError:
                oh = None
            if oh not in self.LOCAL_NAMES:
                return f"クロスサイトの呼び出し（Origin: {origin}）"
        return None

    def do_POST(self) -> None:
        # 仕様書ページに埋め込まれたウィジェット（review_actions / browser_run）が叩く。
        # 書き込み・実行系なので、配信ホストに関わらずローカルホストからの呼び出しのみ受け付ける
        # （--host 0.0.0.0 で LAN 公開していても、リモートから成果物を書き換え・実行させない）。
        if self.path not in self.WRITE_ROUTES:
            self.send_error(404)
            return
        if self.client_address[0] not in ("127.0.0.1", "::1"):
            self._send_json(403, {"ok": False, "message": "ローカルホストからのみ実行できます"})
            return
        reason = self._cross_site_reason()
        if reason:
            self._send_json(403, {"ok": False, "message": reason})
            return
        if FROZEN:
            self._send_json(403, {"ok": False,
                                  "message": "配布版（EXE）はスナップショットのため操作できません。"
                                             "生成元のプロジェクトを serve_site.py で開いてください"})
            return
        if not self.state_root:
            self._send_json(400, {"ok": False, "message": "プロジェクトルートが特定できません"})
            return
        try:
            length = int(self.headers.get("Content-Length", 0))
            payload = json.loads(self.rfile.read(length) or b"{}")
        except (ValueError, OSError):
            self._send_json(400, {"ok": False, "message": "不正なリクエスト"})
            return

        try:
            if self.path == "/review-action":
                res = self._handle_review_action(payload)
            elif self.path == "/run-check":
                res = self._handle_run_check(payload)
            elif self.path == "/run-cancel":
                res = self._handle_run_cancel(payload)
            elif self.path == "/run-batch":
                res = self._handle_run_batch(payload)
            elif self.path == "/run-analyze":
                res = self._handle_run_analyze(payload)
            else:
                res = self._handle_run_phase(payload)
        except Exception as e:                       # noqa: BLE001 — 500 で終わらせず理由を返す
            res = {"ok": False, "message": f"内部エラー: {e}"}
        self._send_json(200 if res.get("ok") else 422, res)

    def _handle_review_action(self, payload: dict) -> dict:
        import review_actions
        action = payload.get("action")
        if action == "approve":
            return review_actions.approve(str(self.state_root), payload.get("kind", ""),
                                          payload.get("func_id", ""),
                                          payload.get("approver") or "unknown")
        if action == "request_changes":
            return review_actions.request_changes(
                str(self.state_root), payload.get("kind", ""), payload.get("func_id", ""),
                payload.get("approver") or "unknown", payload.get("comment") or "")
        if action == "adjudicate":
            return review_actions.adjudicate(
                str(self.state_root), payload.get("func_id", ""), payload.get("issue_id", ""),
                payload.get("approver") or "unknown", payload.get("comment") or "")
        return {"ok": False, "message": f"不明な action: {action}"}

    def _handle_run_phase(self, payload: dict) -> dict:
        # 試作: ①〜⑤（spec/testspec/testcode/impl/test）対応。増やす場合は
        # browser_run.py の KINDS に追記する
        import browser_run
        return browser_run.start(str(self.state_root), payload.get("func_id", ""),
                                 payload.get("kind", "spec"))

    def _handle_run_check(self, payload: dict) -> dict:
        # ⑥完了検証。func_id 不要（全関数横断）・LLM不使用で同期的に終わるので
        # /run-phase とは別ルートにしている
        import browser_run
        return browser_run.run_check(str(self.state_root))

    def _handle_run_cancel(self, payload: dict) -> dict:
        # 実行中のブラウザ単発/連続実行（このプロセスのバックグラウンドスレッド）を中止する
        import browser_run
        return browser_run.cancel(str(self.state_root))

    def _handle_run_batch(self, payload: dict) -> dict:
        # 連続実行（①〜⑤の機械実行を承認・裁定待ち以外で回し続ける）
        import browser_run
        return browser_run.batch_start(str(self.state_root),
                                       payload.get("max_funcs") or 0,
                                       payload.get("budget_usd") or 0)

    def _handle_run_analyze(self, payload: dict) -> dict:
        # ⑦分析（定量評価 → 施策候補の提案。適用はしない）
        import browser_run
        return browser_run.analyze_start(str(self.state_root))

    def end_headers(self) -> None:
        # 再レンダリング後にリロードだけで最新が出るように、一切キャッシュさせない
        self.send_header("Cache-Control", "no-store, must-revalidate")
        self.send_header("X-Content-Type-Options", "nosniff")
        super().end_headers()

    def log_message(self, fmt: str, *args) -> None:
        status = args[1] if len(args) > 1 else ""
        if self.verbose or not str(status).startswith("2"):
            sys.stderr.write(f"  {self.address_string()} {fmt % args}\n")

    def log_error(self, fmt: str, *args) -> None:      # 404 等は上の log_message に集約
        self.log_message(fmt, *args)


# --- レンダリング（開発機のみ。EXE には quarto が入っていない）-----------

def render(root: Path) -> bool:
    if FROZEN:
        print("note: EXE では再レンダリングできない（Quarto が必要）。開発機で作り直して EXE を作り直すこと",
              file=sys.stderr)
        return False
    import render_site                                   # 隣の render_site.py を再利用

    docs = root / "docs"
    if not docs.is_dir():
        print(f"error: {docs} がない", file=sys.stderr)
        return False
    # render_site.py の差分レンダリングをそのまま使う（--watch の1周を数十秒に抑える）
    r = subprocess.run([sys.executable, str(Path(render_site.__file__)),
                        "--root", str(root)])
    if r.returncode != 0:
        print(f"error: render_site.py 失敗（exit={r.returncode}）", file=sys.stderr)
        return False
    return True


def watch(root: Path, interval: float = 1.0) -> None:
    """docs/ の成果物が変わったら再レンダリングする（--watch。デーモンスレッドで回す）。"""
    docs = root / "docs"

    def signature() -> frozenset:
        out = set()
        for p in docs.rglob("*"):
            rel = p.relative_to(docs)
            if any(part.startswith(("_", ".")) for part in rel.parts):
                continue                                  # _site / _sitework / .quarto
            if p.is_file() and p.suffix in WATCH_SUFFIXES:
                out.add((str(rel), p.stat().st_mtime_ns))
        return frozenset(out)

    prev = signature()
    while True:
        time.sleep(interval)
        try:
            cur = signature()
        except OSError:
            continue                                      # 書き込み途中に消えた等は次回に回す
        if cur == prev:
            continue
        time.sleep(0.6)                                    # 連続保存をまとめる
        try:
            cur = signature()
        except OSError:
            continue
        prev = cur
        print("\n変更を検知 → 再レンダリング中…")
        render(root)
        print("完了。ブラウザをリロードしてください")


# --- main -----------------------------------------------------------------

def main() -> None:
    use_utf8_console()
    ap = argparse.ArgumentParser(description="WBS/仕様書サイトをローカルホストで配信する")
    ap.add_argument("--root", default=".", help="対象プロジェクトのルート（既定: カレント）")
    ap.add_argument("--site", help="配信するディレクトリを直接指定（既定: <root>/docs/_site）")
    ap.add_argument("--port", type=int, help="待受ポート（既定: プロジェクト名から固定。0 で OS 任せ）")
    ap.add_argument("--host", default="127.0.0.1",
                    help="bind するアドレス（既定: 127.0.0.1。LAN に公開するなら 0.0.0.0）")
    ap.add_argument("--render", action="store_true", help="配信前に render_site.py で作り直す")
    ap.add_argument("--watch", action="store_true", help="docs/ を監視して自動再レンダリング（--render を含む）")
    ap.add_argument("--no-open", dest="open", action="store_false", help="ブラウザを自動で開かない")
    ap.add_argument("--verbose", action="store_true", help="全リクエストをログに出す")
    args = ap.parse_args()

    root = Path(args.root).expanduser().resolve()
    if args.render or args.watch:
        ok = render(root)                                # EXE では note を出して False
        if FROZEN:
            args.watch = False                           # 再レンダリングできないので監視も無意味
        elif not ok:
            sys.exit(1)

    site, tried = resolve_site(args)
    if not (site / "index.html").exists():
        where = "".join(f"\n  探した場所: {t}" for t in tried) or f"\n  探した場所: {site}"
        hint = ("実行ファイルの隣に _site フォルダを置く（同梱版なら build_viewer.py で作り直す）"
                if FROZEN else
                f"先に `python {Path(__file__).name} --root {args.root} --render` か "
                f"`python render_site.py --root {args.root}` を実行する")
        sys.exit(f"error: index.html が見つからない。{hint}{where}")

    cfg = site_config(site)
    name = cfg.get("name") or root.name
    port = args.port if args.port is not None else cfg.get("port") or default_port(name)

    Handler.verbose = args.verbose
    Handler.state_root = root      # /pipeline.html 用のライブ状態（.legacy-reverse/）の読み元
    httpd = bind_server(args.host, port, functools.partial(Handler, directory=str(site)))
    actual = httpd.server_address[1]
    url = f"http://{'127.0.0.1' if args.host in ('0.0.0.0', '::') else args.host}:{actual}/"

    print(f"\n  {name} の WBS/仕様書サイト")
    print(f"  配信元: {site}")
    print(f"  URL:    {url}")
    print(f"  ライブ: {url}pipeline.html（無人バッチ実行中の進捗・失敗内訳・ETA）")
    if args.host not in ("127.0.0.1", "localhost", "::1"):
        lan = lan_address()
        print(f"  LAN:    http://{lan}:{actual}/" if lan else "  LAN:    このマシンの IP でも開ける")
        print("  warning: ローカルホスト以外にも公開中。仕様書は社内資料である点に注意")
    if args.watch:
        print("  監視:   docs/ の変更で自動再レンダリング")
    print("  終了:   Ctrl+C\n")

    if args.watch:
        threading.Thread(target=watch, args=(root,), daemon=True).start()
    if args.open:
        threading.Timer(0.3, lambda: webbrowser.open(url)).start()   # bind 後に開く

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n停止しました")
    finally:
        if not FROZEN:
            try:
                import browser_run
                browser_run.shutdown()   # 実行中のブラウザ単発を中止し、後片付けを待つ
            except ImportError:          # EXE 等で同梱されていない場合（実行も不可能）は不要
                pass
        httpd.server_close()


if __name__ == "__main__":
    main()

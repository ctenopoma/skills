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
import socket
import subprocess
import sys
import threading
import time
import webbrowser
import zlib
from pathlib import Path
from urllib.parse import unquote, urlsplit

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
body{font-family:"Segoe UI",system-ui,sans-serif;max-width:1240px;margin:24px auto;padding:0 16px;
     color:#1f2937;background:#fff;line-height:1.6}
@media(prefers-color-scheme:dark){body{color:#e5e7eb;background:#111827}
 .card,.panel{background:#1f2937!important}.row:hover{background:#374151!important}}
h1{font-size:1.3rem;margin:0 0 4px}
a{color:#4f46e5}
.muted{color:#9ca3af;font-size:.85rem}
.badge{display:inline-block;padding:2px 12px;border-radius:999px;font-weight:600;font-size:.9rem}
.running{background:#dcfce7;color:#166534}.waiting{background:#fef9c3;color:#854d0e}
.stopped{background:#fee2e2;color:#991b1b}.finished{background:#dbeafe;color:#1e40af}
.none{background:#e5e7eb;color:#374151}
.cards{display:flex;flex-wrap:wrap;gap:12px;margin:14px 0}
.card{background:#f9fafb;border-radius:10px;padding:10px 16px;min-width:120px}
.card .v{font-size:1.25rem;font-weight:700}.card .k{font-size:.78rem;color:#9ca3af}
.bar{height:10px;background:#e5e7eb;border-radius:5px;overflow:hidden;margin:6px 0}
.bar>div{height:100%;background:#4f46e5;transition:width .5s}
.ok{color:#16a34a;font-weight:700}.ng{color:#dc2626;font-weight:700}
.panel{background:#f9fafb;border-radius:10px;padding:12px 16px}
.cols{display:grid;grid-template-columns:5fr 7fr;gap:16px;align-items:start;margin-top:16px}
@media(max-width:960px){.cols{grid-template-columns:1fr}}
.panel h2{font-size:1.02rem;margin:0 0 8px}
.tabs{display:flex;gap:6px;flex-wrap:wrap;margin-bottom:8px}
.tab{border:1px solid #9ca3af55;background:transparent;border-radius:999px;
     padding:2px 12px;font-size:.82rem;cursor:pointer;color:inherit}
.tab.on{background:#4f46e5;border-color:#4f46e5;color:#fff}
.row{display:flex;gap:8px;align-items:center;padding:6px 8px;border-bottom:1px solid #37415122;
     font-size:.9rem;border-radius:6px}
.kind{font-weight:700;width:1.4em;text-align:center}
.fid{font-weight:600;min-width:5.5em}
.ord{color:#9ca3af;font-size:.85rem;width:3.2em;text-align:right;flex-shrink:0}
.grow{flex:1;min-width:0}
.btn{border:1px solid #9ca3af88;background:transparent;border-radius:6px;color:inherit;
     padding:2px 10px;font-size:.8rem;cursor:pointer;white-space:nowrap;text-decoration:none}
select,input[type=text],input[type=search]{padding:5px 8px;border:1px solid #9ca3af;border-radius:6px;
     background:transparent;color:inherit}
.tag{font-size:.72rem;border-radius:4px;padding:1px 6px;font-weight:600;white-space:nowrap}
.tag.wait{background:#fef9c3;color:#854d0e}
.tag.block{background:#fee2e2;color:#991b1b}
.tag.now{background:#dcfce7;color:#166534}
.ngdetail{background:#fee2e2;border-radius:8px;padding:6px 10px;color:#991b1b;
          font-size:.82rem;margin:4px 0}
.ngdetail ul{margin:2px 0 0;padding-left:20px}
@media(prefers-color-scheme:dark){.ngdetail{background:#7f1d1d33;color:#fca5a5}}
.resrow{border-bottom:1px solid #37415122;padding:6px 4px;font-size:.9rem}
.resrow .meta{color:#9ca3af;font-size:.78rem}
.actions{display:flex;gap:6px;flex-wrap:wrap;margin-top:2px;align-items:center}
details pre{white-space:pre-wrap;background:#37415115;padding:8px;border-radius:6px;
            font-size:.8rem;max-height:220px;overflow:auto}
details summary{cursor:pointer;font-size:.8rem;color:#9ca3af}
</style></head><body>

<h1>バッチ実行状況 <span id="state" class="badge none">--</span></h1>
<div class="muted"><a href="/">← WBS へ</a> ／ 2.5秒ごとに自動更新 ／
  最終更新: <span id="upd">--</span></div>

<div class="panel" style="margin:14px 0">
  <div style="font-weight:600">このページは表示専用です（実行・承認の操作は持ちません）</div>
  <div class="muted" style="margin-top:4px">
    連続実行はターミナルから: <code>python &lt;LR&gt;/scripts/pipeline.py run --root .</code>
    （停止は Ctrl+C／⭐割り込みは <code>pipeline.py priority F-xxxx</code>）。
    承認・裁定はチャット・ファイル記入・CLI（<code>review_actions.py</code>）で行い、
    方法は各仕様書ページの案内パネルに出ます。</div>
</div>

<div class="panel" id="nowcard" style="display:none;margin:12px 0;
     display:none;gap:14px;align-items:center;flex-wrap:wrap">
  <div style="font-size:1.05rem" id="nowmain"></div>
  <div style="flex:1;min-width:180px;max-width:280px">
    <div class="bar" style="margin:2px 0"><div id="nowbar" style="width:0%"></div></div>
    <div class="muted" id="nowela"></div>
  </div>
  <a class="btn" id="nowlog" target="_blank" href="#">応答ログ（前回まで）</a>
</div>

<div class="bar"><div id="barok" style="width:0%"></div></div>
<div id="counts" class="muted"></div>
<div class="cards" id="cards"></div>

<div class="cols">
<div class="panel">
  <h2>残タスク <span class="muted" id="qtotal">--</span></h2>
  <input type="search" id="qsearch" placeholder="検索: F-番号・関数名・レガシーファイル"
         style="width:100%;box-sizing:border-box;margin-bottom:8px">
  <div class="tabs" id="qchips"></div>
  <div id="queue" class="muted">読み込み中…</div>
  <div class="muted" id="qfoot" style="margin-top:6px"></div>
</div>

<div class="panel">
  <h2>結果 <span class="muted" id="rcount"></span>
    <span id="rmsg" class="muted" style="font-weight:400"></span></h2>
  <div class="tabs">
    <button class="tab on" id="rtab-" onclick="setRtab('')">すべて</button>
    <button class="tab" id="rtab-ng" onclick="setRtab('ng')">NGのみ <span id="ngn"></span></button>
  </div>
  <div id="results" class="muted">--</div>
</div>
</div>

<script>
const esc = s => (s??"").toString().replace(/[&<>"]/g, c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
// 識別子は関数ID（F-0001）とは限らない。辞書バッチは "dict:V-0001〜V-0040" のように
// URLに直接置けない文字を含み、仕様書ページも持たない（正は辞書ページ）。
// リンクの組み立てはこの2つに集約する（生の識別子をURLに埋めると 404 になる）
const logHref  = fid => "/agent-logs/" + encodeURIComponent(fid) + ".txt";
const pageHref = fid => String(fid).startsWith("dict:")
  ? "/variables.html" : "/specs/" + encodeURIComponent(fid) + ".html";
const fmtEta = s => s==null ? "--" : (s>=3600 ? Math.floor(s/3600)+"時間"+Math.round(s%3600/60)+"分" : Math.round(s/60)+"分");
const STATES = {running:["実行中","running"], waiting_rate:["レート待機中","waiting"],
                stopped:["停止","stopped"], finished:["完了","finished"], not_running:["未実行","none"]};
const PHASES = {spec:["①","仕様書"], testspec:["②","テスト仕様書"], testcode:["③","テストコード"],
                impl:["④","実装"], test:["⑤","テスト"]};
const HUMANS = {"dict-gate":["⓪","変数辞書の語義が未承認（①はここで止まる）","wait"],
                "approve-spec":["①","仕様書の承認待ち","wait"],
                "approve-testspec":["②","テスト仕様書の承認待ち","wait"],
                "adjudicate":["⑤","裁定待ち","block"]};
const CHIP_ORDER = ["spec","testspec","testcode","impl","test"];
let running = false, currentFid = null, medianSec = null, chipK = "", tickN = 0;
let queueItems = [];          // 直近の /batch-queue 結果（実行中カードの名前引きにも使う）

// ---- ステータス（2.5秒ポーリング） ----
async function tick(){
  let d;
  try{ d = await (await fetch("/pipeline-status.json",{cache:"no-store"})).json(); }
  catch(e){ return; }
  const [label, cls] = STATES[d.state] || [d.state,"none"];
  const st = document.getElementById("state");
  st.textContent = label + (d.reason ? "：" + d.reason : ""); st.className = "badge " + cls;
  document.getElementById("upd").textContent = d.updated_at || "--";
  running = d.state === "running" || d.state === "waiting_rate";
  currentFid = d.current ? d.current.func_id : null;
  medianSec = (d.metrics||{}).median_sec || null;
  renderNow(d);
  if(d.state === "not_running"){
    document.getElementById("counts").textContent =
      "パイプラインはまだ実行されていません（ターミナルで pipeline.py run / spec / dict を実行）";
  } else {
    const total = d.total_targets||0, done = d.done||0, failed = d.failed||0;
    document.getElementById("barok").style.width = total ? (100*(done+failed)/total)+"%" : "0%";
    document.getElementById("counts").textContent =
      `処理 ${done+failed} / ${total} 件（成功 ${done}・失敗 ${failed}・残り ${Math.max(total-done-failed,0)}）`
      + (d.rate_waited_sec ? `／レート待機 ${d.rate_waited_sec}s` : "");
    const m = d.metrics||{};
    document.getElementById("cards").innerHTML = [
      ["成功率", m.success_rate!=null ? Math.round(m.success_rate*100)+"%" : "--"],
      ["中央値/件", m.median_sec!=null ? Math.round(m.median_sec)+"s" : "--"],
      ["残り見込み", fmtEta(m.eta_sec)],
      ["累計コスト" + (m.avg_cost_usd!=null ? `（平均 $${m.avg_cost_usd.toFixed(3)}/件）` : ""),
       "$"+(d.cost_usd??0).toFixed(2)],
    ].map(([k,v])=>`<div class="card"><div class="v">${v}</div><div class="k">${k}</div></div>`).join("");
  }
  renderResults(d.recent||[]);
  tickN++;
  if(tickN % 6 === 0) loadQueue();   // 残タスクは走査が重いので約15秒ごと
}

// ---- 実行中カード ----
function renderNow(d){
  const card = document.getElementById("nowcard");
  if(d.state === "waiting_rate"){
    card.style.display = "flex";
    document.getElementById("nowmain").innerHTML =
      "⏸ レートリミット/利用枠の回復待ち（" + esc(d.wait_until||"") + " に再開予定）";
    document.getElementById("nowbar").style.width = "0%";
    document.getElementById("nowela").textContent = "";
    return;
  }
  if(d.state !== "running" || !d.current){ card.style.display = "none"; return; }
  card.style.display = "flex";
  const fid = d.current.func_id;
  const it = queueItems.find(x => x.func_id === fid);
  const ph = PHASES[(it||{}).kind] || null;
  const el = Math.max(0, Math.round((Date.now() - new Date(d.current.started_at))/1000));
  document.getElementById("nowmain").innerHTML =
    `▶ <a href="${pageHref(fid)}"><b>${esc(fid)}</b></a>` +
    (ph ? ` ${ph[0]}${ph[1]}` : "") +
    ` <span class="muted">${it ? esc(it.name + " / " + it.file) + "・" : ""}試行${d.current.attempt}</span>`;
  const med = medianSec || 120;
  const bar = document.getElementById("nowbar");
  bar.style.width = Math.min(100, el/med*100) + "%";
  bar.style.background = el > med*2 ? "#dc2626" : el > med ? "#d97706" : "#16a34a";
  document.getElementById("nowela").textContent =
    `経過 ${Math.floor(el/60)}分${el%60}秒` + (medianSec ? `（中央値 ${Math.round(med)}s）` : "") +
    (el > med*2 ? "・長引いています" : "");
  document.getElementById("nowlog").href = logHref(fid);
}

// ---- 残タスク（表示時・検索時・操作後だけ取得。ポーリングには載せない） ----
function loadQueue(){
  const q = document.getElementById("qsearch").value.trim();
  fetch("/batch-queue?q=" + encodeURIComponent(q) + "&chip=" + encodeURIComponent(chipK),
        {cache:"no-store"})
    .then(r => r.json())
    .then(d => {
      if(!d.ok){
        document.getElementById("queue").innerHTML =
          `<div class="muted">${esc(d.message||"取得できません")}</div>`;
        return;
      }
      renderQueue(d, q);
    })
    .catch(()=>{});
}
function renderQueue(d, q){
  queueItems = d.items || [];
  document.getElementById("qtotal").textContent = d.total.toLocaleString() + "件";
  const c = d.counts || {};
  const autoTotal = d.total - (c.human||0);
  const allOpt = document.querySelector("#bmax option[value='0']");
  if(allOpt) allOpt.textContent = `全部（残り${autoTotal.toLocaleString()}件）`;
  document.getElementById("qchips").innerHTML =
    [`<button class="tab${chipK===""?" on":""}" onclick="setChip('')">すべて</button>`]
    .concat(CHIP_ORDER.filter(k=>c[k]).map(k =>
      `<button class="tab${chipK===k?" on":""}" onclick="setChip('${k}')">${PHASES[k][0]}${c[k]}</button>`))
    .concat(c.human ? [`<button class="tab${chipK==="human"?" on":""}" style="border-color:#d97706;color:${chipK==="human"?"#fff":"#d97706"};${chipK==="human"?"background:#d97706;":""}"
        onclick="setChip('human')">人待ち ${c.human}</button>`] : [])
    .join("");
  const searching = q || chipK;
  const shown = searching ? queueItems : queueItems.slice(0, 9);
  document.getElementById("queue").innerHTML = shown.length
    ? shown.map(rowHtml).join("")
    : `<div class="muted">${searching ? "該当なし" : "残タスクはありません"}</div>`;
  document.getElementById("qfoot").textContent = searching
    ? `${d.hits}件ヒット` + (d.hits > shown.length ? `（先頭${shown.length}件を表示）` : "")
    : `実行順で先頭${Math.min(9, queueItems.length)}件を表示中（検索・絞り込みで全件から探せます）` +
      "。割り込みは pipeline.py priority F-xxxx";
}
function rowHtml(it){
  const isNow = !!it.now || (running && it.func_id === currentFid);
  const ord = it.auto ? (isNow ? "今" : "次" + it.pos) : "";
  const specHref = `/specs/${esc(it.func_id)}.html`;
  let label, act, tag = "";
  if(it.auto){
    const ph = PHASES[it.kind] || ["?", it.kind];
    label = ph[1] + (it.kind === "test" ? "の実行" : "の作成");
    act = isNow ? `<span class="tag now">実行中</span>`
                : (it.starred ? `<span class="tag wait">⭐ 優先中</span>` : "");
    var mark = ph[0];
  } else {
    const h = HUMANS[it.kind] || ["?", it.kind, "wait"];
    var mark = h[0];
    label = h[1];
    tag = ` <span class="tag ${h[2]}">人待ち</span>`;
    if(it.kind === "adjudicate"){
      act = `<a class="btn" href="/issues/${esc(it.issue||"")}.html" target="_blank">${esc(it.issue||"ISSUE")}</a>`;
    } else if(it.kind === "dict-gate"){
      if(it.unapproved) label += `：未承認 ${it.unapproved} 件`;
      act = `<a class="btn" href="/variables.html" target="_blank">辞書を開く（返答方法はページ先頭の案内）</a>`;
    } else {
      const openHref = it.kind === "approve-testspec" ? `/test-specs/${esc(it.func_id)}.html` : specHref;
      act = `<a class="btn" href="${openHref}" target="_blank">開く（返答方法はページ先頭の案内）</a>`;
    }
  }
  return `<div class="row"${isNow ? ' style="background:#dcfce722"' : ""}>
    <span class="ord">${ord}</span><span class="kind">${mark}</span>
    <a class="fid" href="${specHref}">${esc(it.func_id)}</a>
    <span class="grow">${esc(label)}${tag}
      <span class="muted">${esc((it.name||"") + (it.file ? " / " + it.file : ""))}</span></span>
    ${act}</div>`;
}
function setChip(k){ chipK = k; loadQueue(); }

// ---- 結果 ----
// 中身が変わった時だけ再描画する（2.5秒ごとの丸ごと差し替えは、人が開いた
// <details> が毎回閉じてチカチカするため）。再描画時も開いていた行は復元する。
let rtab = "", lastResJson = null;
function setRtab(t){
  rtab = t; lastResJson = null;
  document.querySelectorAll("[id^='rtab-']").forEach(b=>b.classList.remove("on"));
  document.getElementById("rtab-" + t).classList.add("on");
  tick();
}
function renderResults(recent){
  const ngs = recent.filter(r=>!r.ok).length;
  document.getElementById("ngn").textContent = ngs || "";
  document.getElementById("rcount").textContent = recent.length ? `直近${recent.length}件` : "";
  const list = rtab === "ng" ? recent.filter(r=>!r.ok) : recent;
  const json = JSON.stringify([rtab, list, running]);
  if(json === lastResJson) return;
  lastResJson = json;
  const box = document.getElementById("results");
  const openIds = new Set([...box.querySelectorAll("details[open]")].map(el => el.dataset.rid));
  if(!list.length){ box.innerHTML = '<div class="muted">まだ結果がありません</div>'; return; }
  box.innerHTML = list.map((r, i) => {
    const rid = r.func_id + "|" + (r.at || i);
    const ph = PHASES[r.phase] || null;
    const head = (r.ok ? '<span class="ok">OK</span>' : '<span class="ng">NG</span>')
      + ` <a href="${pageHref(r.func_id)}"><b>${esc(r.func_id)}</b></a>`
      + (ph ? ` ${ph[0]}${ph[1]}` : "")
      + (!r.ok && r.kind ? ` — ${esc(r.kind)}` : "")
      + ` <span class="meta">${esc((r.at||"").slice(11))}` +
        `${r.attempt>1 ? "・試行"+r.attempt : ""}・${r.sec??"--"}s` +
        `${r.cost_usd!=null ? "・$"+r.cost_usd.toFixed(2) : ""}` +
        `${r.num_turns!=null ? "・"+r.num_turns+"ターン" : ""}</span>`;
    if(r.ok) return `<div class="resrow">${head}</div>`;
    const problems = r.problems || [];
    const ngbox = problems.length
      ? `<div class="ngdetail"><b>${esc(r.kind||"")}（${problems.length}件）</b>` +
        `<ul>${problems.map(p => `<li>${esc(p)}</li>`).join("")}</ul></div>`
      : (r.why ? `<div class="ngdetail">${esc(r.why)}</div>` : "");
    return `<div class="resrow">${head}${ngbox}
      <div class="actions">
        <a class="btn" href="${logHref(r.func_id)}" target="_blank">応答ログ</a>
        <details${openIds.has(rid) ? " open" : ""} data-rid="${esc(rid)}">
          <summary>応答の末尾</summary><pre>${esc(r.tail || "(応答記録なし)")}</pre></details>
      </div></div>`;
  }).join("");
}

// ---- 初期化 ----
let qtimer = null;
document.getElementById("qsearch").addEventListener("input", () => {
  clearTimeout(qtimer); qtimer = setTimeout(loadQueue, 300);
});
tick(); setInterval(tick, 2500);
loadQueue();
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

    @staticmethod
    def _agent_log_404(d: Path, lp: Path) -> str:
        """応答ログが見つからないときに、切り分けに要る事実だけを返す。"""
        lines = [f"応答ログが見つかりません: {lp}", ""]
        if not d.is_dir():
            lines += [f"ログの置き場そのものがありません: {d}",
                      "  - serve_site.py の --root が対象プロジェクトを指しているか",
                      "  - 無人バッチ（pipeline.py）をまだ一度も実行していないか"]
        else:
            have = sorted(x.name for x in d.glob("*.txt"))
            lines.append(f"この置き場にあるログ {len(have)} 件:")
            lines += [f"  {n}" for n in have[:40]]
            if len(have) > 40:
                lines.append(f"  …ほか {len(have) - 40} 件")
        return "\n".join(lines) + "\n"

    @staticmethod
    def _mark_stale_status(body: bytes) -> bytes:
        """クラッシュ残骸の running を「停止（異常終了）」に補正して配る。

        CLI 側（pipeline.current_run_state）は PID の死活で残骸を無視するが、
        /pipeline.html は状態ファイルをそのまま描くため、補正しないと画面だけが
        永遠に「実行中」に見える。ファイル自体は書き換えない（読み取り専用の補正）。
        EXE 等で pipeline を import できない場合はそのまま返す。
        """
        try:
            import pipeline
            d = json.loads(body)
            if (d.get("state") in ("running", "waiting_rate")
                    and not pipeline._pid_alive(d.get("pid"))):
                d["state"] = "stopped"
                d["current"] = None
                d["reason"] = "前回の実行が異常終了しています（実行プロセスが存在しない）。再実行できます"
                return json.dumps(d, ensure_ascii=False).encode("utf-8")
        except Exception:                        # noqa: BLE001 — 表示補正の失敗で配信を止めない
            pass
        return body

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
                    body = self._mark_stale_status(sp.read_bytes())
                except OSError:
                    pass
            self._send_bytes(body, "application/json; charset=utf-8")
            return
        if self.path.startswith("/batch-queue"):
            # 残タスク一覧（実行順・検索つき・**表示専用**）。走査コストがあるので
            # ポーリング対象にしない
            if FROZEN or not self.state_root:
                self._send_json(200, {"ok": False,
                                      "message": "配布版（EXE）では残タスク一覧を表示できません"})
                return
            from urllib.parse import parse_qs
            qs = parse_qs(urlsplit(self.path).query)
            try:
                import pipeline
                res = pipeline.batch_queue(str(self.state_root),
                                           q=(qs.get("q") or [""])[0],
                                           chip=(qs.get("chip") or [""])[0])
            except SystemExit as e:
                res = {"ok": False, "message": f"データ異常: {e}"}
            except Exception as e:               # noqa: BLE001 — 一覧の失敗で配信を止めない
                res = {"ok": False, "message": f"内部エラー: {e}"}
            self._send_json(200, res)
            return
        if self.path.startswith("/agent-logs/"):
            # 識別子は生のまま来る（"dict:V-0001〜V-0040" のようにファイル名に
            # 使えない文字を含む）。pipeline.agent_log_name と同じ規則で潰してから
            # 引き当てる。この関数は区切り文字（/ \\）を必ず _ にするので、
            # 潰した後の名前でディレクトリを跨ぐことはできない
            import pipeline
            raw = unquote(urlsplit(self.path).path.rsplit("/", 1)[-1])
            stem = raw[:-4] if raw.lower().endswith(".txt") else raw
            if self.state_root and stem:
                d = (self.state_root / ".legacy-reverse" / "agent-logs").resolve()
                lp = (d / pipeline.agent_log_name(stem)).resolve()
                if lp.parent == d and lp.is_file():
                    self._send_bytes(lp.read_bytes(), "text/plain; charset=utf-8")
                    return
                # 素の 404 だと「どこを探して無かったのか」が分からず、原因
                # （--root 違い・skill の版が古い・そもそも未実行）を切り分けられない
                self._send_bytes(self._agent_log_404(d, lp).encode("utf-8"),
                                 "text/plain; charset=utf-8", 404)
                return
            self.send_error(404)
            return
        super().do_GET()

    # 書き込み・実行系の POST ルートは持たない（サイトは**閲覧専用**）。
    # 実行は pipeline.py（CLI）、承認・裁定はチャット / ファイル記入 /
    # review_actions.py（CLI）で行う。

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
        httpd.server_close()


if __name__ == "__main__":
    main()

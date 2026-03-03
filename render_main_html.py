import csv
import json
import sys
import threading
import time
import webbrowser
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from urllib.parse import quote


def build_html(csv_path: Path, out_html: Path, title: str):
    rows = []
    with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
        r = csv.DictReader(f)
        for row in r:
            player = (row.get("player") or row.get("Player") or "").strip()
            count = (row.get("count") or row.get("Count") or "").strip()
            if player:
                try:
                    c = int(count)
                except Exception:
                    c = count
                rows.append((player, c))

    # 默认按 count 降序
    rows.sort(key=lambda x: (-x[1], x[0]) if isinstance(x[1], int) else (str(x[1]), x[0]))

    rows_json = json.dumps([{"player": p, "count": c} for p, c in rows], ensure_ascii=False)

    html = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{title}</title>
  <style>
    :root {{
      --bg: #0b1220;
      --card: #0f1a33;
      --text: #e7ecff;
      --muted: #9fb0ff;
      --border: rgba(255,255,255,.10);
      --row: rgba(255,255,255,.04);
      --row2: rgba(255,255,255,.02);
      --accent: #7aa2ff;
    }}
    body {{
      margin: 0;
      font-family: ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, Arial, "PingFang SC", "Microsoft YaHei", sans-serif;
      background: radial-gradient(1200px 600px at 20% 0%, rgba(122,162,255,.25), transparent 55%),
                  radial-gradient(900px 500px at 100% 10%, rgba(133,255,201,.18), transparent 55%),
                  var(--bg);
      color: var(--text);
    }}
    .wrap {{ max-width: 980px; margin: 40px auto; padding: 0 16px; }}
    .card {{
      background: linear-gradient(180deg, rgba(255,255,255,.06), rgba(255,255,255,.02));
      border: 1px solid var(--border);
      border-radius: 18px;
      box-shadow: 0 10px 40px rgba(0,0,0,.35);
      overflow: hidden;
    }}
    header {{
      padding: 18px 20px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      border-bottom: 1px solid var(--border);
      background: rgba(0,0,0,.15);
    }}
    h1 {{ margin: 0; font-size: 18px; letter-spacing: .2px; }}
    .meta {{ color: var(--muted); font-size: 12px; }}
    .tools {{ display:flex; gap:10px; align-items:center; }}
    input[type="search"] {{
      width: 280px; max-width: 45vw;
      padding: 10px 12px;
      border-radius: 12px;
      border: 1px solid var(--border);
      background: rgba(0,0,0,.25);
      color: var(--text);
      outline: none;
    }}
    input[type="search"]::placeholder {{ color: rgba(231,236,255,.55); }}
    .btn {{
      padding: 10px 12px;
      border-radius: 12px;
      border: 1px solid var(--border);
      background: rgba(0,0,0,.25);
      color: var(--text);
      cursor: pointer;
    }}
    .btn:hover {{ border-color: rgba(122,162,255,.55); }}
    table {{ width: 100%; border-collapse: collapse; }}
    thead th {{
      position: sticky; top: 0;
      background: rgba(10,18,32,.92);
      backdrop-filter: blur(8px);
      text-align: left;
      font-size: 12px;
      color: var(--muted);
      padding: 12px 14px;
      border-bottom: 1px solid var(--border);
      cursor: pointer;
      user-select: none;
      white-space: nowrap;
    }}
    tbody td {{
      padding: 12px 14px;
      border-bottom: 1px solid rgba(255,255,255,.06);
      font-size: 14px;
    }}
    tbody tr:nth-child(odd) {{ background: var(--row); }}
    tbody tr:nth-child(even) {{ background: var(--row2); }}
    tbody tr:hover {{ outline: 1px solid rgba(122,162,255,.35); }}
    .num {{ text-align: right; font-variant-numeric: tabular-nums; }}
    .pill {{
      display:inline-block; padding: 2px 8px; border-radius: 999px;
      border: 1px solid rgba(122,162,255,.35);
      background: rgba(122,162,255,.12);
      color: var(--text);
      font-size: 12px;
      margin-left: 8px;
    }}
    .foot {{
      padding: 12px 16px;
      color: rgba(231,236,255,.7);
      font-size: 12px;
    }}
    .hint {{ color: rgba(231,236,255,.65); }}
    .sort {{ color: var(--accent); margin-left: 6px; }}
  </style>
</head>
<body>
  <div class="wrap">
    <div class="card">
      <header>
        <div>
          <h1>{title}<span class="pill" id="countPill"></span></h1>
          <div class="meta">点击表头排序 · 搜索框实时过滤</div>
        </div>
        <div class="tools">
          <input id="q" type="search" placeholder="搜索 player…" />
          <button class="btn" id="reset">重置</button>
        </div>
      </header>

      <table id="tbl">
        <thead>
          <tr>
            <th data-k="player">player <span class="sort" id="s_player"></span></th>
            <th data-k="count" class="num">count <span class="sort" id="s_count"></span></th>
          </tr>
        </thead>
        <tbody></tbody>
      </table>

      <div class="foot">
        <span class="hint">提示：</span>默认按 count 降序展示；你可以点击表头切换升/降序。
      </div>
    </div>
  </div>

  <script>
    const data = {rows_json};

    const tbody = document.querySelector("#tbl tbody");
    const q = document.querySelector("#q");
    const resetBtn = document.querySelector("#reset");
    const pill = document.querySelector("#countPill");

    let sortKey = "count";
    let sortDir = "desc";

    function render(rows) {{
      tbody.innerHTML = "";
      for (const r of rows) {{
        const tr = document.createElement("tr");
        const td1 = document.createElement("td");
        td1.textContent = r.player;
        const td2 = document.createElement("td");
        td2.textContent = r.count;
        td2.className = "num";
        tr.appendChild(td1);
        tr.appendChild(td2);
        tbody.appendChild(tr);
      }}
      pill.textContent = `${{rows.length}} rows`;
    }}

    function applySort(rows) {{
      const s = [...rows];
      s.sort((a,b) => {{
        let va = a[sortKey], vb = b[sortKey];
        if (sortKey === "count") {{
          va = Number(va); vb = Number(vb);
        }}
        if (va < vb) return sortDir === "asc" ? -1 : 1;
        if (va > vb) return sortDir === "asc" ? 1 : -1;
        return (a.player || "").localeCompare(b.player || "");
      }});
      return s;
    }}

    function applyFilter(rows) {{
      const kw = q.value.trim().toLowerCase();
      if (!kw) return rows;
      return rows.filter(r => (r.player || "").toLowerCase().includes(kw));
    }}

    function updateSortIndicators() {{
      document.querySelector("#s_player").textContent = "";
      document.querySelector("#s_count").textContent = "";
      const el = document.querySelector(`#s_${{sortKey}}`);
      el.textContent = sortDir === "asc" ? "▲" : "▼";
    }}

    function refresh() {{
      const filtered = applyFilter(data);
      const sorted = applySort(filtered);
      updateSortIndicators();
      render(sorted);
    }}

    document.querySelectorAll("thead th[data-k]").forEach(th => {{
      th.addEventListener("click", () => {{
        const k = th.dataset.k;
        if (sortKey === k) {{
          sortDir = (sortDir === "asc") ? "desc" : "asc";
        }} else {{
          sortKey = k;
          sortDir = (k === "count") ? "desc" : "asc";
        }}
        refresh();
      }});
    }});

    q.addEventListener("input", refresh);
    resetBtn.addEventListener("click", () => {{
      q.value = "";
      sortKey = "count";
      sortDir = "desc";
      refresh();
    }});

    refresh();
  </script>
</body>
</html>
"""
    out_html.write_text(html, encoding="utf-8")


def serve_and_open(html_file: Path, port: int = 8765):
    class QuietHandler(SimpleHTTPRequestHandler):
        def log_message(self, format, *args):
            pass

    httpd = ThreadingHTTPServer(("127.0.0.1", port), QuietHandler)

    def _run():
        httpd.serve_forever()

    t = threading.Thread(target=_run, daemon=True)
    t.start()

    url = f"http://127.0.0.1:{port}/{quote(html_file.name)}"
    webbrowser.open(url)
    print(f"[ok] opened: {url}")
    print("[hint] close this terminal to stop the server.")


def main():
    # 选“最新修改”的 main 统计文件
    mains = sorted(Path(".").glob("player_count_*_main.csv"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not mains:
        print("No file matching: player_count_*_main.csv")
        sys.exit(1)

    csv_path = mains[0]
    title = f"Main Picks Count ({csv_path.name})"
    out_html = Path("main_table.html")

    print(f"[info] using: {csv_path.name}")
    build_html(csv_path, out_html, title)

    serve_and_open(out_html, port=8765)

    # 保持进程不退出，让本地服务一直在
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n[info] stopped.")


if __name__ == "__main__":
    main()
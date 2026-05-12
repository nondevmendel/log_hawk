#!/usr/bin/env python3
"""
Screen Log Admin — http://localhost:8765
Local-only web UI to manage screenshots, tombstones, and the ignore list.
"""

import json
import subprocess
import sys
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from threading import Timer
from urllib.parse import urlparse, unquote

REPO   = Path.home() / ".screenlog"
SHOTS  = REPO / "docs" / "screenshots"
CONFIG = REPO / "config.json"
VISITS = REPO / "visits.json"
META   = SHOTS / "metadata.json"
INDEX  = REPO / "docs" / "index.json"
STATS  = REPO / "docs" / "stats.json"
PORT   = 8765

# ─────────────────────────────────────────────────────────────────────────────
ADMIN_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Log Hawk — Admin</title>
<style>
:root{--bg:#0f0f0f;--surface:#1a1a1a;--surface2:#222;--border:#2a2a2a;--text:#e0e0e0;--muted:#777;--accent:#4a9eff;--red:#e05555;--green:#4caf50;--font:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--text);font-family:var(--font);min-height:100vh}
header{position:sticky;top:0;z-index:100;background:rgba(15,15,15,.94);backdrop-filter:blur(12px);border-bottom:1px solid var(--border);padding:13px 24px;display:flex;align-items:center;gap:16px}
header h1{font-size:15px;font-weight:700;display:flex;align-items:center;gap:10px}header h1 span{color:var(--accent)}
.hawk{font-family:monospace;font-size:8px;line-height:1.2;color:var(--accent);white-space:pre;opacity:.8}
.tabs{display:flex;gap:4px;margin-left:8px}
.tab{background:none;border:1px solid var(--border);color:var(--muted);padding:6px 14px;border-radius:6px;cursor:pointer;font-size:13px;transition:.15s}
.tab.active{background:var(--accent);border-color:var(--accent);color:#fff;font-weight:500}
.pub-link{margin-left:auto;font-size:12px;color:var(--accent);text-decoration:none;opacity:.8}
.pub-link:hover{opacity:1}
#status-bar{font-size:12px;color:var(--green);padding:10px 24px;border-bottom:1px solid var(--border);min-height:36px;display:flex;align-items:center;gap:8px}
#status-bar.error{color:var(--red)}
main{padding:24px;max-width:1600px;margin:0 auto}
.panel{display:none}.panel.active{display:block}

/* Segmented toggle */
.toggle-bar{display:inline-flex;border:1px solid var(--border);border-radius:8px;overflow:hidden;margin-bottom:14px}
.tgl-btn{background:none;border:none;border-right:1px solid var(--border);color:var(--muted);padding:7px 20px;cursor:pointer;font-size:13px;font-weight:500;transition:.12s}
.tgl-btn:last-child{border-right:none}
.tgl-btn.active{background:var(--accent);color:#fff}
.tgl-btn:hover:not(.active){background:rgba(74,158,255,.09);color:var(--text)}

/* Browser */
.browser-body{display:grid;grid-template-columns:220px 1fr;border:1px solid var(--border);border-radius:10px;overflow:hidden;min-height:420px}
.group-list{border-right:1px solid var(--border);overflow-y:auto;max-height:65vh}
.group-date{font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.8px;color:var(--muted);padding:9px 14px 4px;background:rgba(0,0,0,.25);position:sticky;top:0;z-index:1}
.group-item{padding:9px 14px 9px 12px;cursor:pointer;display:flex;justify-content:space-between;align-items:center;border-bottom:1px solid var(--border);border-left:2px solid transparent;transition:background .08s,border-color .08s}
.group-item:last-child{border-bottom:none}
.group-item:hover,.group-item.active{background:rgba(74,158,255,.07);border-left-color:var(--accent)}
.g-label{font-size:13px;font-weight:500;transition:color .08s}
.group-item.active .g-label{color:var(--accent)}
.g-count{font-size:10px;color:var(--muted);background:rgba(255,255,255,.05);padding:2px 6px;border-radius:9px}

/* Shot panel */
.shot-panel{padding:14px;display:flex;flex-wrap:wrap;align-content:flex-start;gap:10px;overflow-y:auto;max-height:65vh}
.shot-card{width:172px;flex-shrink:0}
.shot-card img{width:172px;height:113px;object-fit:cover;border-radius:6px 6px 0 0;cursor:zoom-in;display:block;border:2px solid transparent;transition:border-color .12s}
.shot-card img:hover{border-color:var(--accent)}
.shot-meta{background:var(--surface2);padding:5px 8px;border-radius:0 0 6px 6px;display:flex;justify-content:space-between;align-items:center}
.shot-time{font-size:10px;color:var(--muted)}
.shot-del{background:none;border:none;color:var(--muted);cursor:pointer;font-size:12px;padding:2px 4px;border-radius:3px;transition:.1s;line-height:1}
.shot-del:hover{color:var(--red);background:rgba(224,85,85,.12)}
.empty-panel{display:flex;align-items:center;justify-content:center;width:100%;color:var(--muted);font-size:13px}

/* Ignore list */
.ignore-wrap{max-width:640px}
.ignore-list{border:1px solid var(--border);border-radius:8px;overflow:hidden;margin-bottom:16px}
.ignore-item{display:flex;align-items:center;justify-content:space-between;padding:12px 16px;border-bottom:1px solid var(--border);font-size:13px}
.ignore-item:last-child{border-bottom:none}
.ignore-item code{background:#111;padding:2px 8px;border-radius:4px;font-size:12px;color:var(--accent)}
.btn-remove{background:none;border:1px solid var(--border);color:var(--muted);padding:4px 10px;border-radius:5px;cursor:pointer;font-size:12px;transition:.15s}
.btn-remove:hover{border-color:var(--red);color:var(--red)}
.add-row{display:flex;gap:8px}
.add-row input{flex:1;background:var(--surface2);border:1px solid var(--border);color:var(--text);padding:9px 12px;border-radius:6px;font-size:13px;outline:none}
.add-row input:focus{border-color:var(--accent)}
.btn-add{background:var(--accent);border:none;color:#fff;padding:9px 18px;border-radius:6px;cursor:pointer;font-size:13px;font-weight:500;transition:.15s;white-space:nowrap}
.btn-add:hover{opacity:.85}
.empty{color:var(--muted);font-size:13px;padding:24px 0;text-align:center}

/* Stats */
.stats-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:12px;margin-top:4px}
.stat-card{background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:16px}
.stat-card .num{font-size:28px;font-weight:700;color:var(--accent)}
.stat-card .label{font-size:12px;color:var(--muted);margin-top:4px}
.stat-card .sub{font-size:11px;color:var(--muted);margin-top:6px}

/* Lightbox */
#lb{display:none;position:fixed;inset:0;z-index:200;background:rgba(0,0,0,.92);align-items:center;justify-content:center}
#lb.open{display:flex}
#lb img{max-width:95vw;max-height:92vh;border-radius:6px}
#lb-close{position:absolute;top:20px;right:24px;background:none;border:none;color:var(--muted);font-size:30px;cursor:pointer}

.section-title{font-size:13px;font-weight:600;color:var(--muted);text-transform:uppercase;letter-spacing:.8px;margin-bottom:14px}
</style>
</head>
<body>
<header>
  <h1><pre class="hawk">   _,_
  (o,o)
__/)X(\__
\_/ | \_/
    |</pre> Log <span>Hawk</span> <span style="font-size:11px;color:var(--muted);font-weight:400">admin</span></h1>
  <div class="tabs">
    <button class="tab active" onclick="switchTab('shots')">Screenshots</button>
    <button class="tab" onclick="switchTab('ignore')">Ignore List</button>
    <button class="tab" onclick="switchTab('stats')">Visit Stats</button>
  </div>
  <a class="pub-link" href="https://nondevmendel.github.io/log_hawk/" target="_blank">View Public Gallery ↗</a>
</header>
<div id="status-bar">Ready</div>

<main>
  <div id="panel-shots" class="panel active">
    <div id="shots-content">Loading…</div>
  </div>
  <div id="panel-ignore" class="panel">
    <div class="ignore-wrap">
      <div class="section-title">Ignored URL Patterns</div>
      <p style="font-size:13px;color:var(--muted);margin-bottom:16px">Substrings matched against the full URL. Changes take effect on the next 30-second poll.</p>
      <div id="ignore-list"></div>
      <div class="add-row">
        <input id="new-pattern" type="text" placeholder="e.g. messenger.facebook.com or facebook.com/reels">
        <button class="btn-add" onclick="addIgnore()">Add Pattern</button>
      </div>
    </div>
  </div>
  <div id="panel-stats" class="panel">
    <div class="section-title">Social Media Visits</div>
    <div id="stats-grid" class="stats-grid"></div>
  </div>
</main>

<div id="lb"><button id="lb-close" onclick="closeLb()">×</button><img id="lb-img" src="" alt=""></div>

<script>
let currentIgnored = [], allEntries = [], currentMode = 'hour', currentGroups = [], activeIdx = -1;

function status(msg, isErr) {
  const el = document.getElementById('status-bar');
  el.textContent = msg; el.className = isErr ? 'error' : '';
}

async function api(path, method='GET', body=null) {
  const opts = { method, headers: {'Content-Type':'application/json'} };
  if (body) opts.body = JSON.stringify(body);
  const r = await fetch(path, opts);
  return r.json();
}

function switchTab(name) {
  document.querySelectorAll('.panel').forEach(p => p.classList.remove('active'));
  document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
  document.getElementById('panel-' + name).classList.add('active');
  event.target.classList.add('active');
  if (name === 'ignore') renderIgnore();
  if (name === 'stats') renderStats();
}

// ── Grouping ──────────────────────────────────────────────────────────────────
function getGroupKey(e, mode) {
  if (mode === 'hour') {
    const d = new Date(e.iso);
    const ds = d.toLocaleDateString('en-US', {weekday:'short', month:'short', day:'numeric'});
    const h = d.getHours(), ampm = h >= 12 ? 'PM' : 'AM';
    return `${ds} · ${h % 12 || 12}:00 ${ampm}`;
  }
  return e.domain || 'unknown';
}

function buildGroups(mode) {
  const m = new Map();
  allEntries.filter(e => !e.deleted).forEach(e => {
    const k = getGroupKey(e, mode);
    if (!m.has(k)) m.set(k, { key: k, entries: [], sortIso: e.iso });
    m.get(k).entries.push(e);
  });
  const arr = [...m.values()];
  if (mode === 'hour') arr.sort((a, b) => b.sortIso.localeCompare(a.sortIso));
  else arr.sort((a, b) => b.entries.length - a.entries.length);
  return arr;
}

function setMode(mode) { currentMode = mode; activeIdx = 0; renderShots(); }

function hoverGroup(idx) {
  if (idx === activeIdx) return;
  activeIdx = idx;
  document.querySelectorAll('.group-item').forEach(el => {
    el.classList.toggle('active', +el.dataset.gi === idx);
  });
  const entries = (currentGroups[idx] || {}).entries || [];
  document.getElementById('shot-panel').innerHTML = entries.length
    ? entries.map(e => shotCardHtml(e)).join('')
    : '<div class="empty-panel">No screenshots in this period</div>';
}

// ── Screenshots ───────────────────────────────────────────────────────────────
async function loadShots() {
  const data = await api('/api/data');
  allEntries = data.screenshots;
  renderShots();
}

function renderShots() {
  const container = document.getElementById('shots-content');
  const live = allEntries.filter(e => !e.deleted).length;

  const toggle = `<div class="toggle-bar">
    <button class="tgl-btn${currentMode==='hour'?' active':''}" onclick="setMode('hour')">By Hour</button>
    <button class="tgl-btn${currentMode==='domain'?' active':''}" onclick="setMode('domain')">By Domain</button>
  </div>`;

  if (!live) { container.innerHTML = toggle + '<p class="empty">No screenshots yet.</p>'; return; }

  currentGroups = buildGroups(currentMode);
  if (activeIdx < 0 || activeIdx >= currentGroups.length) activeIdx = 0;

  let listHtml = '', lastDate = '';
  currentGroups.forEach((g, i) => {
    if (currentMode === 'hour') {
      const [date, hour] = g.key.split(' · ');
      if (date !== lastDate) { listHtml += `<div class="group-date">${date}</div>`; lastDate = date; }
      listHtml += `<div class="group-item${i===activeIdx?' active':''}" data-gi="${i}" onmouseenter="hoverGroup(${i})">
        <span class="g-label">${hour}</span><span class="g-count">${g.entries.length}</span></div>`;
    } else {
      listHtml += `<div class="group-item${i===activeIdx?' active':''}" data-gi="${i}" onmouseenter="hoverGroup(${i})">
        <span class="g-label">${g.key}</span><span class="g-count">${g.entries.length}</span></div>`;
    }
  });

  const activeEntries = (currentGroups[activeIdx] || {}).entries || [];
  const panelHtml = activeEntries.length
    ? activeEntries.map(e => shotCardHtml(e)).join('')
    : '<div class="empty-panel">No screenshots in this period</div>';

  container.innerHTML = toggle + `<div class="browser-body">
    <div class="group-list">${listHtml}</div>
    <div class="shot-panel" id="shot-panel">${panelHtml}</div>
  </div>`;
}

function shotCardHtml(e) {
  const t = e.display.match(/\d+:\d+:\d+ [AP]M/)?.[0] || e.display;
  return `<div class="shot-card">
    <img src="/screenshots/${e.file}" loading="lazy" onclick="openLb(this.src)" alt="" title="${e.display}">
    <div class="shot-meta">
      <span class="shot-time">${t}</span>
      <button class="shot-del" onclick="deleteShot('${e.stem}','${e.domain||''}')">✕</button>
    </div>
  </div>`;
}

async function deleteShot(stem, domain) {
  if (!confirm(`Delete this screenshot?\n\nA tombstone showing "${domain || 'this site'}" will appear in its place on the public gallery.`)) return;
  status('Deleting and pushing…');
  try {
    await api('/api/delete', 'POST', { stem, domain });
    status('Deleted. Gallery updated.');
    loadShots();
  } catch(e) { status('Error: ' + e, true); }
}


// ── Ignore list ──────────────────────────────────────────────────────────────
async function renderIgnore() {
  const data = await api('/api/data');
  currentIgnored = data.config.ignored_urls || [];
  const el = document.getElementById('ignore-list');
  if (!currentIgnored.length) {
    el.innerHTML = '<div class="ignore-list"><div class="empty">No patterns yet.</div></div>';
    return;
  }
  let html = '<div class="ignore-list">';
  currentIgnored.forEach((p, i) => {
    html += `<div class="ignore-item"><code>${p}</code><button class="btn-remove" onclick="removeIgnore(${i})">Remove</button></div>`;
  });
  el.innerHTML = html + '</div>';
}

async function addIgnore() {
  const input = document.getElementById('new-pattern');
  const val = input.value.trim().toLowerCase();
  if (!val) return;
  if (currentIgnored.includes(val)) { status('Already in list.', true); return; }
  currentIgnored.push(val);
  await saveIgnore();
  input.value = '';
}

async function removeIgnore(idx) {
  currentIgnored.splice(idx, 1);
  await saveIgnore();
}

async function saveIgnore() {
  status('Saving…');
  try {
    await api('/api/config', 'POST', { ignored_urls: currentIgnored });
    status('Ignore list saved.');
    renderIgnore();
  } catch(e) { status('Error: ' + e, true); }
}

// ── Visit stats ──────────────────────────────────────────────────────────────
function weekKey() {
  const d=new Date(),yr=d.getFullYear();
  const wk=Math.ceil((d-new Date(yr,0,1))/604800000);
  return `${yr}-W${String(wk).padStart(2,'0')}`;
}
function fmtTime(s) {
  if(!s) return '—';
  const h=Math.floor(s/3600),m=Math.floor((s%3600)/60);
  return h?`${h}h ${m}m`:`${m}m`;
}

async function renderStats() {
  const data = await api('/api/data');
  const visits = data.visits || {};
  const el = document.getElementById('stats-grid');
  const wk = weekKey();
  const today = new Date().toISOString().slice(0,10);

  const sorted = Object.entries(visits)
    .sort((a,b)=>(b[1].total_visits||0)-(a[1].total_visits||0));

  if (!sorted.length) { el.innerHTML = '<p class="empty">No visits recorded yet.</p>'; return; }

  el.innerHTML = sorted.map(([domain, v]) => {
    const wd = (v.weekly||{})[wk]||{};
    const dd = (v.daily||{})[today]||{};
    return `<div class="stat-card" style="grid-column:1/-1;display:grid;grid-template-columns:180px repeat(4,1fr);align-items:center;gap:12px">
      <div><div class="num" style="font-size:20px">${domain}</div>
           <div class="sub">Last: ${v.last_visit?new Date(v.last_visit).toLocaleTimeString():'—'}</div></div>
      <div><div class="num">${v.total_visits||0}</div><div class="label">Total visits</div></div>
      <div><div class="num">${wd.visits||0}</div><div class="label">This week</div>
           <div class="sub">${fmtTime(wd.time_seconds)}</div></div>
      <div><div class="num">${dd.visits||0}</div><div class="label">Today</div>
           <div class="sub">${fmtTime(dd.time_seconds)}</div></div>
      <div><div class="num">${fmtTime(v.total_time_seconds)}</div><div class="label">Total time</div></div>
    </div>`;
  }).join('');
}

// ── Lightbox ─────────────────────────────────────────────────────────────────
function openLb(src) { document.getElementById('lb-img').src=src; document.getElementById('lb').classList.add('open'); }
function closeLb() { document.getElementById('lb').classList.remove('open'); }
document.addEventListener('keydown', e => { if(e.key==='Escape') closeLb(); });

// Init
loadShots();
</script>
</body>
</html>"""

# ─────────────────────────────────────────────────────────────────────────────

def load_meta():
    try: return json.loads(META.read_text())
    except: return {}

def save_meta(data):
    SHOTS.mkdir(parents=True, exist_ok=True)
    META.write_text(json.dumps(data, indent=2))

def load_config():
    try: return json.loads(CONFIG.read_text())
    except: return {"ignored_urls": []}

def save_config(data):
    CONFIG.write_text(json.dumps(data, indent=2))

def load_visits():
    try: return json.loads(VISITS.read_text())
    except: return {}


def read_screenshots():
    meta = load_meta()
    entries = []
    stems_with_jpg = set()

    for jpg in sorted(SHOTS.glob("*.jpg"), reverse=True):
        try:
            dt = datetime.strptime(jpg.stem, "%Y%m%d_%H%M%S")
            stems_with_jpg.add(jpg.stem)
            m = meta.get(jpg.stem, {})
            entries.append({
                "file":    jpg.name,
                "stem":    jpg.stem,
                "iso":     dt.isoformat(),
                "display": dt.strftime("%b %-d, %Y  %-I:%M:%S %p"),
                "date":    dt.strftime("%A, %B %-d"),
                "domain":  m.get("domain"),
                "deleted": False,
            })
        except ValueError:
            pass

    for stem, m in sorted(meta.items(), reverse=True):
        if m.get("deleted") and stem not in stems_with_jpg:
            try:
                dt = datetime.strptime(stem, "%Y%m%d_%H%M%S")
                entries.append({
                    "file":       None,
                    "stem":       stem,
                    "iso":        dt.isoformat(),
                    "display":    dt.strftime("%b %-d, %Y  %-I:%M:%S %p"),
                    "date":       dt.strftime("%A, %B %-d"),
                    "domain":     m.get("domain"),
                    "deleted":    True,
                    "deleted_at": m.get("deleted_at"),
                })
            except ValueError:
                pass

    entries.sort(key=lambda e: e["iso"], reverse=True)
    return entries


def rebuild_index():
    entries = read_screenshots()
    INDEX.parent.mkdir(parents=True, exist_ok=True)
    INDEX.write_text(json.dumps(entries))


def rebuild_stats():
    cfg   = load_config()
    visits = load_visits()
    STATS.write_text(json.dumps({
        "ignored_urls": cfg.get("ignored_urls", []),
        "visits":       visits,
    }))


def git_push(msg):
    try:
        subprocess.run(["git", "-C", str(REPO), "add", "-A"],
                       check=True, capture_output=True)
        diff = subprocess.run(
            ["git", "-C", str(REPO), "diff", "--cached", "--name-only"],
            capture_output=True, text=True
        )
        if not diff.stdout.strip():
            return "nothing to push"
        subprocess.run(["git", "-C", str(REPO), "commit", "-m", msg],
                       check=True, capture_output=True)
        r = subprocess.run(["git", "-C", str(REPO), "push"],
                           capture_output=True, text=True)
        return "ok" if r.returncode == 0 else r.stderr.strip()
    except subprocess.CalledProcessError as e:
        return str(e)


# ─────────────────────────────────────────────────────────────────────────────

class Handler(BaseHTTPRequestHandler):

    def log_message(self, fmt, *args):
        pass  # silence access log

    def send_json(self, data, code=200):
        body = json.dumps(data).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def read_body(self):
        length = int(self.headers.get("Content-Length", 0))
        return json.loads(self.rfile.read(length)) if length else {}

    def do_GET(self):
        path = urlparse(self.path).path

        if path in ("/", "/index.html"):
            body = ADMIN_HTML.encode()  # re-read from module string (restart server to pick up changes)
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        elif path == "/api/data":
            self.send_json({
                "screenshots": read_screenshots(),
                "config":      load_config(),
                "visits":      load_visits(),
            })

        elif path.startswith("/screenshots/"):
            fname = unquote(path.split("/screenshots/", 1)[-1])
            fpath = SHOTS / fname
            if fpath.exists() and fpath.suffix in (".jpg", ".jpeg", ".png"):
                body = fpath.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "image/jpeg")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            else:
                self.send_response(404); self.end_headers()

        else:
            self.send_response(404); self.end_headers()

    def do_POST(self):
        path = urlparse(self.path).path
        body = self.read_body()

        if path == "/api/delete":
            stem   = body.get("stem", "")
            domain = body.get("domain", "") or "social media"
            jpg = SHOTS / f"{stem}.jpg"
            if jpg.exists():
                jpg.unlink()
            meta = load_meta()
            meta[stem] = {
                "domain":     domain,
                "deleted":    True,
                "deleted_at": datetime.now().isoformat(),
            }
            save_meta(meta)
            rebuild_index()
            rebuild_stats()
            result = git_push(f"delete screenshot {stem}")
            self.send_json({"ok": True, "push": result})

        elif path == "/api/forget":
            stem = body.get("stem", "")
            meta = load_meta()
            if stem in meta:
                del meta[stem]
                save_meta(meta)
            rebuild_index()
            rebuild_stats()
            result = git_push(f"forget tombstone {stem}")
            self.send_json({"ok": True, "push": result})

        elif path == "/api/config":
            cfg = load_config()
            cfg["ignored_urls"] = body.get("ignored_urls", [])
            save_config(cfg)
            rebuild_stats()
            result = git_push("update ignore list")
            self.send_json({"ok": True, "push": result})

        else:
            self.send_response(404); self.end_headers()


# ─────────────────────────────────────────────────────────────────────────────

def main():
    # Ensure stats file exists on first run
    rebuild_stats()

    server = HTTPServer(("127.0.0.1", PORT), Handler)
    print(f"Screen Log Admin → http://localhost:{PORT}")

    # Open browser after short delay so server is ready
    Timer(0.8, lambda: webbrowser.open(f"http://localhost:{PORT}")).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Мини-Асана: простой реал-тайм таск-трекер. Чистый stdlib + SQLite.

Запуск:  python server.py
Открой:  http://localhost:8777  (или http://<твой-VPN-IP>:8777 с других устройств)

Реал-тайм — через поллинг клиента раз в 1.5с. Хранилище — tasks.db рядом со скриптом.
"""
import json
import sqlite3
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

DB_PATH = Path(__import__("os").environ.get("DB_PATH", str(Path(__file__).with_name("tasks.db"))))
PORT = int(__import__("os").environ.get("PORT", 8777))
_lock = threading.Lock()


def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def seed_db_if_empty():
    seed = Path(__file__).with_name("seed_data.json")
    if not seed.exists():
        return
    with db() as conn:
        count = conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
        if count > 0:
            return
    import json
    tasks = json.loads(seed.read_text(encoding="utf-8"))
    with _lock, db() as conn:
        for t in tasks:
            conn.execute(
                "INSERT INTO tasks (id,title,assignee,done,position,created_at,completed_at,urgent) VALUES (?,?,?,?,?,?,?,?)",
                (t["id"], t["title"], t["assignee"], t["done"], t["position"],
                 t["created_at"], t["completed_at"], t["urgent"]),
            )
        conn.commit()


def init_db():
    with db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL DEFAULT '',
                assignee TEXT NOT NULL DEFAULT '',
                done INTEGER NOT NULL DEFAULT 0,
                position INTEGER NOT NULL DEFAULT 0,
                created_at REAL NOT NULL DEFAULT 0,
                completed_at REAL NOT NULL DEFAULT 0,
                urgent INTEGER NOT NULL DEFAULT 0
            )
        """)
        # Миграция старых БД: добавить новые столбцы, если их нет
        cols = [r[1] for r in conn.execute("PRAGMA table_info(tasks)").fetchall()]
        if "completed_at" not in cols:
            conn.execute("ALTER TABLE tasks ADD COLUMN completed_at REAL NOT NULL DEFAULT 0")
        if "urgent" not in cols:
            conn.execute("ALTER TABLE tasks ADD COLUMN urgent INTEGER NOT NULL DEFAULT 0")
            # существующие активные задачи кладём в «Срочно»…
            conn.execute("UPDATE tasks SET urgent = 1 WHERE done = 0")
            # …кроме отложенных (светлую тему делаем в последнюю очередь)
            conn.execute("UPDATE tasks SET urgent = 0 WHERE done = 0 AND title LIKE 'Светлая тема%'")
        # Бэкфилл: уже выполненным задачам без даты завершения ставим дату создания
        conn.execute(
            "UPDATE tasks SET completed_at = created_at WHERE done = 1 AND (completed_at IS NULL OR completed_at = 0)"
        )
        conn.commit()


def list_tasks():
    with db() as conn:
        rows = conn.execute(
            "SELECT * FROM tasks ORDER BY done ASC, position ASC, id ASC"
        ).fetchall()
        return [dict(r) for r in rows]


def create_task(title, assignee, urgent=1):
    with _lock, db() as conn:
        pos = conn.execute("SELECT COALESCE(MAX(position),0)+1 AS p FROM tasks").fetchone()["p"]
        cur = conn.execute(
            "INSERT INTO tasks (title, assignee, done, position, created_at, urgent) VALUES (?,?,0,?,?,?)",
            (title.strip(), assignee.strip(), pos, time.time(), 1 if urgent else 0),
        )
        conn.commit()
        return conn.execute("SELECT * FROM tasks WHERE id=?", (cur.lastrowid,)).fetchone()


def update_task(task_id, fields):
    allowed = {"title", "assignee", "done", "urgent"}
    sets, vals = [], []
    for k, v in fields.items():
        if k in allowed:
            sets.append(f"{k}=?")
            vals.append(int(v) if k in ("done", "urgent") else str(v))
            # При смене статуса фиксируем/сбрасываем дату завершения
            if k == "done":
                sets.append("completed_at=?")
                vals.append(time.time() if int(v) == 1 else 0)
    if not sets:
        return None
    vals.append(task_id)
    with _lock, db() as conn:
        conn.execute(f"UPDATE tasks SET {', '.join(sets)} WHERE id=?", vals)
        conn.commit()
        row = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        return dict(row) if row else None


def delete_task(task_id):
    with _lock, db() as conn:
        conn.execute("DELETE FROM tasks WHERE id=?", (task_id,))
        conn.commit()


def reorder_tasks(ids):
    """Переупорядочивание: position = индекс в присланном списке id."""
    with _lock, db() as conn:
        for i, tid in enumerate(ids):
            conn.execute("UPDATE tasks SET position=? WHERE id=?", (i, int(tid)))
        conn.commit()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass  # тихо

    def _send_json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self):
        body = HTML.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body_json(self):
        length = int(self.headers.get("Content-Length", 0))
        if not length:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except Exception:
            return {}

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,PATCH,DELETE,OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/" or path == "/index.html":
            return self._send_html()
        if path == "/api/tasks":
            return self._send_json(list_tasks())
        self._send_json({"error": "not found"}, 404)

    def do_POST(self):
        path = urlparse(self.path).path
        if path == "/api/tasks/reorder":
            data = self._body_json()
            ids = data.get("ids") or []
            reorder_tasks(ids)
            return self._send_json({"ok": True})
        if path == "/api/tasks":
            data = self._body_json()
            title = (data.get("title") or "").strip()
            if not title:
                return self._send_json({"error": "empty title"}, 400)
            row = create_task(title, data.get("assignee") or "")
            return self._send_json(dict(row), 201)
        self._send_json({"error": "not found"}, 404)

    def do_PATCH(self):
        path = urlparse(self.path).path
        if path.startswith("/api/tasks/"):
            try:
                task_id = int(path.rsplit("/", 1)[1])
            except ValueError:
                return self._send_json({"error": "bad id"}, 400)
            row = update_task(task_id, self._body_json())
            if not row:
                return self._send_json({"error": "not found"}, 404)
            return self._send_json(row)
        self._send_json({"error": "not found"}, 404)

    def do_DELETE(self):
        path = urlparse(self.path).path
        if path.startswith("/api/tasks/"):
            try:
                task_id = int(path.rsplit("/", 1)[1])
            except ValueError:
                return self._send_json({"error": "bad id"}, 400)
            delete_task(task_id)
            return self._send_json({"ok": True})
        self._send_json({"error": "not found"}, 404)


HTML = r"""<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Задачи — Fish App</title>
<style>
  :root {
    --bg:#f6f7f8; --panel:#fff; --line:#ecedef; --line2:#e3e4e7;
    --txt:#1e1f21; --txt2:#6f7782; --accent:#4573d2; --green:#16a34a;
  }
  * { box-sizing:border-box; margin:0; padding:0; font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif; }
  body { background:var(--bg); color:var(--txt); padding:24px 12px 80px; }
  .wrap { max-width:880px; margin:0 auto; }
  h1 { font-size:22px; font-weight:700; margin-bottom:2px; display:flex; align-items:center; gap:10px; }
  .sub { color:var(--txt2); font-size:13px; margin-bottom:18px; }
  .dot { width:8px; height:8px; border-radius:50%; background:var(--green); display:inline-block; }
  .card { background:var(--panel); border:1px solid var(--line); border-radius:12px; overflow:hidden; }
  .head { display:flex; align-items:center; padding:9px 16px; border-bottom:1px solid var(--line); color:var(--txt2); font-size:12px; font-weight:600; text-transform:uppercase; letter-spacing:.4px; }
  .head .h-task { flex:1; }
  .head .h-as { width:160px; }
  .head .h-del { width:32px; }
  .row { display:flex; align-items:center; padding:0 16px; min-height:46px; border-bottom:1px solid var(--line); transition:background .12s; }
  .row:last-child { border-bottom:none; }
  .row:hover { background:#fafbfc; }
  .row.done .title { color:var(--txt2); text-decoration:line-through; }
  .check { width:22px; height:22px; border-radius:50%; border:1.5px solid #c4c7cc; cursor:pointer; flex-shrink:0; display:flex; align-items:center; justify-content:center; transition:all .12s; }
  .check:hover { border-color:var(--green); }
  .check.on { background:var(--green); border-color:var(--green); }
  .check svg { width:13px; height:13px; stroke:#fff; stroke-width:3; fill:none; opacity:0; }
  .check.on svg { opacity:1; }
  .title { flex:1; padding:8px 12px; font-size:14px; outline:none; border-radius:6px; min-height:20px; }
  .title:focus { background:#eef3fd; box-shadow:inset 0 0 0 1px var(--accent); }
  .title.empty:before { content:'Без названия'; color:#b9bcc2; }
  .assignee { width:160px; flex-shrink:0; }
  .as-chip { display:inline-flex; align-items:center; gap:7px; padding:3px 9px 3px 3px; border-radius:20px; cursor:pointer; border:1px dashed transparent; max-width:100%; }
  .as-chip:hover { background:#eef0f2; }
  .as-chip.empty { border-color:#c9ccd1; padding-left:9px; color:var(--txt2); }
  .av { width:24px; height:24px; border-radius:50%; color:#fff; font-size:11px; font-weight:700; display:flex; align-items:center; justify-content:center; flex-shrink:0; }
  .as-name { font-size:13px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
  .as-plus { width:24px; height:24px; border-radius:50%; border:1.5px dashed #c4c7cc; display:flex; align-items:center; justify-content:center; color:var(--txt2); font-size:14px; }
  .del { width:32px; flex-shrink:0; text-align:center; color:#c4c7cc; cursor:pointer; opacity:0; font-size:16px; transition:opacity .12s; }
  .row:hover .del { opacity:1; }
  .del:hover { color:#e5484d; }
  .add-row { display:flex; align-items:center; gap:8px; padding:0 16px; min-height:46px; }
  .add-row input { flex:1; border:none; outline:none; font-size:14px; padding:12px; background:transparent; color:var(--txt); }
  .add-row input::placeholder { color:#b9bcc2; }
  .add-circle { width:22px; height:22px; border-radius:50%; border:1.5px solid #d6d9dd; display:flex; align-items:center; justify-content:center; color:#b9bcc2; font-size:15px; flex-shrink:0; }
  .add-btn { flex-shrink:0; background:var(--accent); color:#fff; border:none; border-radius:8px; padding:8px 16px; font-size:13px; font-weight:700; cursor:pointer; display:flex; align-items:center; gap:6px; transition:background .12s; }
  .add-btn:hover { background:#3a63b8; }
  .add-btn:disabled { background:#c4cddd; cursor:default; }
  /* dropdown */
  .menu { position:absolute; background:#fff; border:1px solid var(--line2); border-radius:10px; box-shadow:0 8px 28px rgba(0,0,0,.14); padding:6px; z-index:50; min-width:200px; }
  .menu .mi { display:flex; align-items:center; gap:9px; padding:7px 9px; border-radius:7px; cursor:pointer; font-size:13px; }
  .menu .mi:hover { background:#f0f2f4; }
  .menu input { width:100%; border:1px solid var(--line2); border-radius:7px; padding:7px 9px; font-size:13px; outline:none; margin-bottom:4px; }
  .menu input:focus { border-color:var(--accent); }
  .muted { color:var(--txt2); font-size:11px; padding:4px 9px; }
  .footer { text-align:center; color:#b9bcc2; font-size:11px; margin-top:14px; }
  /* секции завершённых по дням */
  .day { margin-top:14px; }
  .day-head { display:flex; align-items:center; gap:8px; padding:8px 6px; cursor:pointer; color:var(--txt2); font-size:12.5px; font-weight:700; user-select:none; }
  .day-head:hover { color:var(--txt); }
  .day-head .chev { font-size:10px; transition:transform .15s; }
  .day-head.collapsed .chev { transform:rotate(-90deg); }
  .day-head .cnt { color:#b9bcc2; font-weight:600; }
  .day-card { background:var(--panel); border:1px solid var(--line); border-radius:12px; overflow:hidden; }
  /* флаг срочности на активной задаче */
  .flag { width:26px; flex-shrink:0; text-align:center; cursor:pointer; color:#c8ccd2; font-size:16px; transition:color .12s; }
  .flag:hover { color:#e8a33d; }
  .flag.on { color:#e8a33d; }
  /* заголовки разделов «Срочно» / «На будущее» */
  .sec-head { display:flex; align-items:center; gap:8px; padding:11px 16px 6px; font-size:12px; font-weight:800; text-transform:uppercase; letter-spacing:.4px; }
  .sec-head.urgent { color:#e5484d; }
  .sec-head.later { color:var(--txt2); }
  .sec-head .cnt { color:#b9bcc2; font-weight:700; }
  .sec-empty { padding:4px 16px 10px; color:#c0c3c8; font-size:12.5px; }
  /* drag-and-drop */
  .handle { width:22px; flex-shrink:0; text-align:center; color:#cfd2d7; cursor:grab; font-size:15px; user-select:none; line-height:1; }
  .handle:hover { color:var(--txt2); }
  .handle:active { cursor:grabbing; }
  .row.dragging { opacity:.4; }
  .row.drop-before { box-shadow: inset 0 2px 0 0 var(--accent); }
  .row.drop-after { box-shadow: inset 0 -2px 0 0 var(--accent); }
</style>
</head>
<body>
<div class="wrap">
  <h1><span class="dot"></span> Задачи Fish App</h1>
  <div class="sub" id="sub">Загрузка…</div>
  <div class="card">
    <div class="head">
      <div style="width:22px"></div>
      <div class="h-task" style="padding-left:12px">Задача</div>
      <div class="h-as">Исполнитель</div>
      <div class="h-del"></div>
    </div>
    <div id="list"></div>
    <div class="add-row">
      <div class="add-circle">+</div>
      <input id="addInput" placeholder="Добавить задачу…" autocomplete="off">
      <button class="add-btn" id="addBtn" disabled>＋ Добавить</button>
    </div>
  </div>
  <div id="doneList"></div>
  <div class="footer">Реал-тайм обновление каждые 1.5с · данные в tasks.db</div>
</div>

<script>
const PALETTE = ['#e8a33d','#8b8f96','#4573d2','#16a34a','#d6409f','#e5484d','#0ea5e9','#7c3aed','#0d9488','#b45309'];
function colorFor(name){ if(!name) return '#c4c7cc'; let h=0; for(const c of name) h=(h*31+c.charCodeAt(0))>>>0; return PALETTE[h%PALETTE.length]; }
function initials(name){ const p=name.trim().split(/\s+/); return ((p[0]?.[0]||'')+(p[1]?.[0]||'')).toUpperCase() || '?'; }
function esc(s){ return (s||'').replace(/[&<>"]/g, c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c])); }

let tasks = [];
let lastJSON = '';
let editingId = null;       // id задачи, чей title сейчас редактируется
let menuOpen = null;        // id задачи с открытым меню исполнителя
let dragId = null;          // id перетаскиваемой задачи
let dragUrgent = null;      // секция перетаскиваемой задачи (urgent)
let dropBefore = true;      // вставить выше/ниже целевой строки

async function api(method, path, body){
  const r = await fetch('/api'+path, {method, headers:{'Content-Type':'application/json'}, body: body?JSON.stringify(body):undefined});
  return r.ok ? r.json() : null;
}

async function poll(){
  try {
    const data = await (await fetch('/api/tasks')).json();
    const j = JSON.stringify(data);
    if (j !== lastJSON && editingId===null && menuOpen===null){
      lastJSON = j; tasks = data; render();
    } else if (j !== lastJSON) {
      // есть локальное редактирование — обновим данные, но не перерисуем грубо
      tasks = data; lastJSON = j;
    }
  } catch(e){}
}

function knownAssignees(){
  const set = new Set();
  tasks.forEach(t=>{ if(t.assignee) set.add(t.assignee); });
  ['Aza','Aleksandr'].forEach(n=>set.add(n));
  return [...set];
}

let collapsedDays = {};
try { collapsedDays = JSON.parse(localStorage.getItem('collapsedDays')||'{}'); } catch(e){}

function pad(n){ return n<10?'0'+n:''+n; }
function dayKeyOf(ts){ const d=new Date(ts*1000); return d.getFullYear()+'-'+pad(d.getMonth()+1)+'-'+pad(d.getDate()); }
function dayLabel(ts){
  const k=dayKeyOf(ts), now=new Date(), today=dayKeyOf(now.getTime()/1000);
  const yd=new Date(now); yd.setDate(now.getDate()-1);
  const yda=new Date(now); yda.setDate(now.getDate()-2);
  if(k===today) return 'Сегодня';
  if(k===dayKeyOf(yd.getTime()/1000)) return 'Вчера';
  if(k===dayKeyOf(yda.getTime()/1000)) return 'Позавчера';
  const d=new Date(ts*1000);
  const m=['янв','фев','мар','апр','мая','июн','июл','авг','сен','окт','ноя','дек'];
  return d.getDate()+' '+m[d.getMonth()]+(d.getFullYear()!==now.getFullYear()?(' '+d.getFullYear()):'');
}

function makeRow(t){
  const row = document.createElement('div');
  row.className = 'row'+(t.done?' done':'');
  row.dataset.id = t.id;
  // ручка перетаскивания (только активные) + строка как drop-зона своей секции
  if(!t.done){
    const handle = document.createElement('div');
    handle.className = 'handle';
    handle.innerHTML = '⠿';
    handle.title = 'Перетащить';
    handle.draggable = true;
    handle.ondragstart = (e)=>{
      dragId = t.id; dragUrgent = t.urgent;
      e.dataTransfer.effectAllowed = 'move';
      try { e.dataTransfer.setDragImage(row, 12, 12); } catch(_){}
      row.classList.add('dragging');
    };
    handle.ondragend = ()=>{
      document.querySelectorAll('.row.dragging,.row.drop-before,.row.drop-after')
        .forEach(r=>r.classList.remove('dragging','drop-before','drop-after'));
      dragId = null; dragUrgent = null;
    };
    row.appendChild(handle);
    // drop только в пределах своей секции (та же urgent)
    row.ondragover = (e)=>{
      if(dragId===null || t.id===dragId || t.urgent!==dragUrgent) return;
      e.preventDefault();
      const r = row.getBoundingClientRect();
      dropBefore = e.clientY < r.top + r.height/2;
      row.classList.toggle('drop-before', dropBefore);
      row.classList.toggle('drop-after', !dropBefore);
    };
    row.ondragleave = ()=>{ row.classList.remove('drop-before','drop-after'); };
    row.ondrop = (e)=>{
      e.preventDefault();
      row.classList.remove('drop-before','drop-after');
      if(dragId!==null && t.id!==dragId && t.urgent===dragUrgent) doReorder(dragId, t.id, dropBefore);
    };
  }
  // check
  const check = document.createElement('div');
  check.className = 'check'+(t.done?' on':'');
  check.innerHTML = '<svg viewBox="0 0 24 24"><polyline points="4 12 10 18 20 6"/></svg>';
  check.onclick = ()=>toggleDone(t);
  row.appendChild(check);
  // title (editable)
  const title = document.createElement('div');
  title.className = 'title'+(t.title?'':' empty');
  title.contentEditable = 'true';
  title.spellcheck = false;
  title.textContent = t.title;
  title.onfocus = ()=>{ editingId = t.id; };
  title.onblur = ()=>{ editingId=null; saveTitle(t, title.textContent); };
  title.onkeydown = (e)=>{ if(e.key==='Enter'){ e.preventDefault(); title.blur(); } };
  row.appendChild(title);
  // assignee
  const as = document.createElement('div');
  as.className = 'assignee';
  as.appendChild(assigneeChip(t));
  row.appendChild(as);
  // флаг срочности (только у активных) — перекинуть между «Срочно» / «На будущее»
  if(!t.done){
    const flag = document.createElement('div');
    flag.className = 'flag'+(t.urgent?' on':'');
    flag.innerHTML = t.urgent ? '★' : '☆';
    flag.title = t.urgent ? 'Перенести в «На будущее»' : 'Сделать срочной';
    flag.onclick = (e)=>{ e.stopPropagation(); toggleUrgent(t); };
    row.appendChild(flag);
  }
  // delete
  const del = document.createElement('div');
  del.className = 'del';
  del.innerHTML = '🗑';
  del.title = 'Удалить';
  del.onclick = ()=>removeTask(t);
  row.appendChild(del);
  return row;
}

async function toggleUrgent(t){
  t.urgent = t.urgent?0:1;
  lastJSON=''; render();
  await api('PATCH', '/tasks/'+t.id, {urgent:t.urgent});
  poll();
}

async function doReorder(srcId, targetId, before){
  const src = tasks.find(t=>t.id===srcId);
  if(!src) return;
  let active = tasks.filter(t=>!t.done && t.id!==srcId);
  const ti = active.findIndex(t=>t.id===targetId);
  if(ti<0) return;
  active.splice(before ? ti : ti+1, 0, src);
  active.forEach((t,i)=> t.position = i);
  tasks = active.concat(tasks.filter(t=>t.done));
  lastJSON=''; render();
  await api('POST', '/tasks/reorder', {ids: active.map(t=>t.id)});
  poll();
}

function render(){
  const list = document.getElementById('list');
  const doneList = document.getElementById('doneList');
  const doneCnt = tasks.filter(t=>t.done).length;
  document.getElementById('sub').textContent = `${tasks.length} задач · ${doneCnt} готово`;

  // Активные — два раздела: «Срочно» и «Не срочно — на будущее»
  list.innerHTML = '';
  const active = tasks.filter(t=>!t.done);
  function section(label, cls, items){
    const h = document.createElement('div');
    h.className = 'sec-head '+cls;
    h.innerHTML = `<span>${label}</span><span class="cnt">${items.length}</span>`;
    list.appendChild(h);
    if(items.length === 0){
      const e = document.createElement('div');
      e.className = 'sec-empty';
      e.textContent = '— пусто —';
      list.appendChild(e);
    } else {
      items.forEach(t=> list.appendChild(makeRow(t)));
    }
  }
  section('🔥 Срочно', 'urgent', active.filter(t=>t.urgent));
  section('🗓 Не срочно — на будущее', 'later', active.filter(t=>!t.urgent));

  // Завершённые — сгруппированы по дню завершения, сворачиваемые секции
  doneList.innerHTML = '';
  const groups = {};
  tasks.filter(t=>t.done).forEach(t=>{
    const ts = (t.completed_at && t.completed_at>0) ? t.completed_at : t.created_at;
    const k = dayKeyOf(ts);
    (groups[k] = groups[k] || {ts, items:[]}).items.push(t);
  });
  const today = dayKeyOf(new Date().getTime()/1000);
  Object.keys(groups).sort((a,b)=> groups[b].ts - groups[a].ts).forEach(k=>{
    const g = groups[k];
    // прошлые дни свёрнуты по умолчанию, сегодня раскрыт; выбор юзера запоминаем
    const collapsed = (k in collapsedDays) ? collapsedDays[k] : (k !== today);
    const sec = document.createElement('div');
    sec.className = 'day';
    const head = document.createElement('div');
    head.className = 'day-head'+(collapsed?' collapsed':'');
    head.innerHTML = `<span class="chev">▼</span><span>✓ ${dayLabel(g.ts)}</span><span class="cnt">${g.items.length}</span>`;
    head.onclick = ()=>{ collapsedDays[k] = !collapsed; localStorage.setItem('collapsedDays', JSON.stringify(collapsedDays)); render(); };
    sec.appendChild(head);
    if(!collapsed){
      const card = document.createElement('div');
      card.className = 'day-card';
      g.items.forEach(t=> card.appendChild(makeRow(t)));
      sec.appendChild(card);
    }
    doneList.appendChild(sec);
  });
}

function assigneeChip(t){
  const wrap = document.createElement('div');
  if (t.assignee){
    const chip = document.createElement('div');
    chip.className = 'as-chip';
    chip.innerHTML = `<span class="av" style="background:${colorFor(t.assignee)}">${esc(initials(t.assignee))}</span><span class="as-name">${esc(t.assignee)}</span>`;
    chip.onclick = (e)=>openAssigneeMenu(e, t);
    wrap.appendChild(chip);
  } else {
    const chip = document.createElement('div');
    chip.className = 'as-chip empty';
    chip.innerHTML = `<span class="as-plus">+</span>`;
    chip.onclick = (e)=>openAssigneeMenu(e, t);
    wrap.appendChild(chip);
  }
  return wrap;
}

function closeMenu(){
  document.querySelectorAll('.menu').forEach(m=>m.remove());
  menuOpen = null;
}

function openAssigneeMenu(e, t){
  e.stopPropagation();
  closeMenu();
  menuOpen = t.id;
  const m = document.createElement('div');
  m.className = 'menu';
  const rect = e.currentTarget.getBoundingClientRect();
  m.style.left = Math.min(rect.left, window.innerWidth-220)+'px';
  m.style.top = (rect.bottom+window.scrollY+4)+'px';

  const inp = document.createElement('input');
  inp.placeholder = 'Имя исполнителя…';
  inp.onkeydown = (ev)=>{ if(ev.key==='Enter' && inp.value.trim()){ setAssignee(t, inp.value.trim()); } };
  m.appendChild(inp);

  knownAssignees().forEach(name=>{
    const mi = document.createElement('div');
    mi.className = 'mi';
    mi.innerHTML = `<span class="av" style="background:${colorFor(name)}">${esc(initials(name))}</span> ${esc(name)}`;
    mi.onclick = ()=>setAssignee(t, name);
    m.appendChild(mi);
  });
  if (t.assignee){
    const mi = document.createElement('div');
    mi.className = 'mi'; mi.style.color='#e5484d';
    mi.innerHTML = '✕ Убрать исполнителя';
    mi.onclick = ()=>setAssignee(t, '');
    m.appendChild(mi);
  }
  document.body.appendChild(m);
  inp.focus();
}

document.addEventListener('click', closeMenu);

async function toggleDone(t){
  t.done = t.done?0:1;
  t.completed_at = t.done ? (Date.now()/1000) : 0;  // сразу в группу «Сегодня»
  lastJSON=''; render();
  await api('PATCH', '/tasks/'+t.id, {done:t.done});
  poll();
}
async function saveTitle(t, val){
  val = (val||'').trim();
  if (val === t.title) return;
  t.title = val; lastJSON='';
  await api('PATCH', '/tasks/'+t.id, {title:val});
  poll();
}
async function setAssignee(t, name){
  closeMenu();
  t.assignee = name; lastJSON=''; render();
  await api('PATCH', '/tasks/'+t.id, {assignee:name});
  poll();
}
async function removeTask(t){
  t._gone = true;
  tasks = tasks.filter(x=>x.id!==t.id); lastJSON=''; render();
  await api('DELETE', '/tasks/'+t.id);
  poll();
}

const addInput = document.getElementById('addInput');
const addBtn = document.getElementById('addBtn');

async function addTask(){
  const v = addInput.value.trim();
  if (!v) return;
  addInput.value=''; addBtn.disabled = true;
  const row = await api('POST', '/tasks', {title:v, assignee:''});
  if (row){ tasks.push(row); lastJSON=''; render(); }
  addInput.focus();
}

addInput.addEventListener('input', ()=>{ addBtn.disabled = !addInput.value.trim(); });
addInput.addEventListener('keydown', (e)=>{ if (e.key==='Enter') addTask(); });
addBtn.addEventListener('click', addTask);

render();
poll();
setInterval(poll, 1500);
</script>
</body>
</html>
"""


def main():
    init_db()
    seed_db_if_empty()
    srv = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    # Под pythonw (без консоли) sys.stdout = None → print() упал бы. Защищаемся.
    try:
        if __import__("sys").stdout is not None:
            print(f"Task tracker -> http://localhost:{PORT}  (LAN/VPN: http://<ip>:{PORT})")
    except Exception:
        pass
    srv.serve_forever()


if __name__ == "__main__":
    main()

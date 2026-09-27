/* 候诊玩具消毒与轮换系统 —— 前端（原生 JS） */
"use strict";

let S = null;                 // 服务器状态
let role = getCookie("role") || "staff";
let areaFilter = "all";
let traceFilter = {};
const nowOffset = { v: 0 };    // 服务器时间 - 本地时间（毫秒）

/* ---------------- 小图标（线性 SVG，非卡通背景） ---------------- */
const PATHS = {
  blocks: '<rect x="3" y="3" width="8" height="8" rx="1.5"/><rect x="13" y="3" width="8" height="8" rx="1.5"/><rect x="3" y="13" width="8" height="8" rx="1.5"/><rect x="13" y="13" width="8" height="8" rx="1.5"/>',
  rattle: '<circle cx="9" cy="14" r="5"/><path d="M12.5 10.5 18 5"/><circle cx="18.5" cy="5.5" r="2.2"/>',
  cube: '<path d="M12 3l8 4.5v9L12 21l-8-4.5v-9z"/><path d="M4 7.5l8 4.5 8-4.5"/><path d="M12 12v9"/>',
  car: '<path d="M3 13l2-5h11l3 5"/><rect x="3" y="13" width="18" height="5" rx="1.5"/><circle cx="7.5" cy="18.5" r="2"/><circle cx="16.5" cy="18.5" r="2"/>',
  ball: '<circle cx="12" cy="12" r="9"/><path d="M5 7c3 3 4 8 4 12M19 7c-3 3-4 8-4 12M3.5 11h17M3.5 15h17"/>',
  ring: '<circle cx="12" cy="12" r="8"/><circle cx="12" cy="12" r="3"/>',
  book: '<path d="M4 5c3-1.5 5.5-1.5 8 0 2.5-1.5 5-1.5 8 0v13c-3-1.5-5.5-1.5-8 0-2.5-1.5-5-1.5-8 0z"/><path d="M12 5v13"/>',
  puzzle: '<path d="M9 3h6v3a2 2 0 110 4v3H9v-3a2 2 0 100-4z"/><path d="M3 9h6v3a2 2 0 11-4 0H3z"/><path d="M15 9h6v3a2 2 0 11-4 0h-2z"/><path d="M3 15h6v6H3z"/><path d="M15 15h6v6h-6z"/>',
};
const ICON = (k, size = 22) =>
  `<svg viewBox="0 0 24 24" width="${size}" height="${size}" fill="none" stroke="currentColor"
     stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">${PATHS[k] || PATHS.cube}</svg>`;
const LOCK =
  `<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor"
     stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
     <rect x="5" y="11" width="14" height="9" rx="2"/><path d="M8 11V8a4 4 0 018 0v3"/></svg>`;

const STATUS_LABEL = {
  clean: "清洁库可用", deployed: "候诊区", pending_dirty: "待清洁", washing: "清洁消毒中",
  drying: "干燥中", quarantined: "隔离中", repair: "维修中", retired: "已报废",
};
const MAT_HEX = { plastic: "#5b97b5", silicone: "#d99a4e", fabric: "#8f73b8", wood: "#b07d4c", metal: "#7d8c96" };

/* ---------------- 工具 ---------------- */
function $ (sel, root = document) { return root.querySelector(sel); }
function $$ (sel, root = document) { return [...root.querySelectorAll(sel)]; }
function esc (s) { return String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])); }
function getCookie (n) { return document.cookie.split("; ").reduce((a, p) => p.startsWith(n + "=") ? p.slice(n.length + 1) : a, ""); }
function serverNow () { return new Date(Date.now() + nowOffset.v); }
function parseTs (s) { return s ? new Date(s.replace(" ", "T")) : null; }
function fmtLeft (ms) {
  if (ms <= 0) return "已到时";
  const s = Math.ceil(ms / 1000);
  return `${Math.floor(s / 60)}分${s % 60}秒`;
}
function basename (p) { return p ? p.split(/[\\/]/).pop() : ""; }
function photoUrl (p) { return p ? "/photos/" + encodeURIComponent(basename(p)) : ""; }

async function api (path, body) {
  const opt = { method: "GET", headers: {} };
  if (body) { opt.method = "POST"; opt.headers["Content-Type"] = "application/json"; opt.body = JSON.stringify(body); }
  const res = await fetch(path, opt);
  const json = await res.json();
  if (!json.ok) throw Object.assign(new Error(json.error.message), { code: json.error.code });
  return json.data;
}
let toastTimer;
function toast (msg, kind = "") {
  const el = $("#toast");
  el.textContent = msg;
  el.className = "toast show " + kind;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.className = "toast", 3200);
}

/* ---------------- 角色 ---------------- */
function renderRole () {
  $$(".role-btn").forEach(b => b.classList.toggle("active", b.dataset.role === role));
  const front = role === "front_desk";
  $$(".tab").forEach(t => {
    t.style.display = front && t.dataset.tab !== "waiting" ? "none" : "";
  });
  if (front && !$("#tab-waiting").classList.contains("active")) switchTab("waiting");
}
$$(".role-btn").forEach(b => b.addEventListener("click", async () => {
  await api("/api/role", { role: b.dataset.role });
  role = b.dataset.role;
  document.cookie = `role=${role}; path=/`;
  renderRole();
  await refresh();
  toast(`已切换为${b.textContent}视图`, "ok");
}));

/* ---------------- 标签页 ---------------- */
function switchTab (name) {
  $$(".tab").forEach(t => t.classList.toggle("active", t.dataset.tab === name));
  $$(".panel").forEach(p => p.classList.toggle("active", p.id === "tab-" + name));
}
$$(".tab").forEach(t => t.addEventListener("click", () => switchTab(t.dataset.tab)));

/* ---------------- 渲染：候诊区 ---------------- */
function renderWaiting () {
  const w = S.waiting;
  $("#clock").textContent = "系统时间 " + w.checked_at;
  $("#zoneCards").innerHTML = w.zones.map(z => {
    const ratio = Math.round(z.available / z.capacity * 100);
    const full = z.available >= z.capacity;
    return `<div class="zone-card">
      <h3>${esc(z.name)}</h3>
      <div class="zone-meta">容量 ${z.capacity} 件 · 占用 ${ratio}%</div>
      <div class="zone-num">${z.available} <small>/ ${z.capacity} 件可用</small></div>
      <div class="progress" style="margin-top:10px"><span style="width:${ratio}%"></span></div>
      <div class="zone-foot">
        <span class="pill ${z.rotation_overdue ? "pill-coral" : "pill-sea"}">
          ${z.rotation_overdue ? `${z.rotation_overdue} 件到轮换时点` : "轮换正常"}</span>
        <span class="muted">下次轮换：${z.next_rotation_at ? esc(z.next_rotation_at.slice(11)) : "—"}</span>
      </div>
      ${full ? `<div class="pill pill-warn" style="margin-top:8px">已满员</div>` : ""}
    </div>`;
  }).join("");
}

/* ---------------- 渲染：区域投放表 ---------------- */
function renderDeploy () {
  const clean = S.toys.filter(t => t.status === "clean");
  $("#deployToy").innerHTML =
    `<option value="">选择清洁库玩具（${clean.length} 件可用）</option>` +
    clean.map(t => `<option value="${t.id}">${t.id} ${esc(t.name)} · ${S.constants.materials[t.material]}</option>`).join("");
  $("#deployZone").innerHTML =
    S.zones.map(z => `<option value="${z.id}">${esc(z.name)}</option>`).join("");

  const rows = S.toys.filter(t => t.status === "deployed");
  const byZone = Object.fromEntries(S.zones.map(z => [z.id, []]));
  rows.forEach(t => byZone[t.zone_id]?.push(t));
  $("#deployTable").innerHTML = S.zones.map(z => {
    const list = byZone[z.id] || [];
    return `<h3>${esc(z.name)}（${list.length}/${z.capacity}）</h3>
      <div class="stack" style="margin-bottom:14px">
      ${list.length ? list.map(t => `<div class="card-row">
          <div class="row-main">
            <span class="mini-toy">${ICON(t.icon, 16)} ${t.id}</span>
            <span class="row-sub">投放 ${esc(t.deployed_at?.slice(11))} · 轮换时点 ${esc(t.rotation_due_at?.slice(11))}</span>
          </div>
          <button class="btn btn-sm" data-act="quick-recycle" data-id="${t.id}">回收扫描</button>
        </div>`).join("") : '<div class="empty">该区域暂无在区玩具</div>'}
      </div>`;
  }).join("");
}

/* ---------------- 渲染：玩具库 ---------------- */
function renderLibrary () {
  const areas = [["all", "全部"], ...Object.entries(S.constants.areas)];
  $("#areaFilter").innerHTML = areas.map(([k, v]) =>
    `<button class="chip ${areaFilter === k ? "active" : ""}" data-area="${k}">${v}</button>`).join("");
  const toys = areaFilter === "all" ? S.toys : S.toys.filter(t => t.current_area === areaFilter);
  $("#toyGrid").innerHTML = toys.map(t => {
    const q = t.status === "quarantined" || t.status === "repair";
    const cls = q ? "is-quarantine" : t.status === "drying" ? "is-drying" : t.status === "deployed" ? "is-deployed" : "";
    return `<div class="toy-card ${cls}" title="${esc(t.name)}｜${S.constants.materials[t.material]}｜${esc(STATUS_LABEL[t.status])}｜${S.constants.clean_methods[t.clean_method]}｜零件 ${t.parts_present}/${t.parts_total}">
      ${q ? `<span class="lock-badge">${LOCK} 锁</span>` : ""}
      <div class="icon">${ICON(t.icon, 24)}</div>
      <div class="toy-id">${t.id}</div>
      <div class="toy-name">${esc(t.name)}</div>
      <div class="toy-tags">
        <span class="tag tag-mat-${t.material}">${S.constants.materials[t.material]}</span>
        <span class="tag">${t.age_min}-${t.age_max}岁</span>
      </div>
      <div class="toy-tags"><span class="tag">${esc(STATUS_LABEL[t.status])}</span></div>
    </div>`;
  }).join("") || '<div class="empty">该区域暂无玩具</div>';
}

/* ---------------- 渲染：回收 ---------------- */
function renderRecycle () {
  $("#recycleZone").innerHTML =
    `<option value="">— 与登记一致 —</option>` +
    S.zones.map(z => `<option value="${z.id}">实际扫于 ${esc(z.name)}</option>`).join("");
  $("#toyDatalist").innerHTML = S.toys.map(t => `<option value="${t.id}">${esc(t.name)}</option>`).join("");
  const bin = S.toys.filter(t => t.status === "pending_dirty");
  $("#binList").innerHTML = bin.map(t =>
    `<div class="card-row"><div class="row-main">
       <span class="mini-toy">${ICON(t.icon, 16)} ${t.id}</span>
       <span class="row-sub">${esc(t.name)} · ${S.constants.materials[t.material]} · 规定${S.constants.clean_methods[t.clean_method]}</span>
     </div></div>`).join("") || '<div class="empty">回收箱为空</div>';
}

/* ---------------- 渲染：消毒组批（突出材质分组） ---------------- */
const BATCH_STATUS = { open: "待消毒", running: "设备运行", disinfected: "已消毒待干燥", drying: "干燥中", done: "已完成", quarantined: "已批量隔离" };
function renderBatches () {
  $("#batchMaterial").innerHTML =
    Object.entries(S.constants.materials).map(([k, v]) =>
      `<option value="${k}">${v}（${S.constants.clean_methods[defaultMethod(k)]} · 干燥 ${S.constants.dry_minutes[k]} 分钟）</option>`).join("");
  const groups = {};
  S.batches.forEach(b => (groups[b.material] ||= []).push(b));
  $("#batchList").innerHTML = Object.entries(groups).map(([mat, bs]) => `
    <div class="material-group">
      <h3 style="display:flex;align-items:center;gap:8px">
        <span class="mat-swatch" style="background:${MAT_HEX[mat]}"></span>
        ${S.constants.materials[mat]} 材质组 · ${S.constants.clean_methods[defaultMethod(mat)]}
        <span class="pill pill-gray">${bs.length} 批</span></h3>
      ${bs.map(batchCard).join("")}
    </div>`).join("") || '<div class="empty">还没有消毒批次</div>';
}
function defaultMethod (mat) {
  return { plastic: "wipe", silicone: "soak", fabric: "machine", wood: "wipe", metal: "machine" }[mat];
}
function batchCard (b) {
  const candidates = S.toys.filter(t => t.status === "pending_dirty" && t.material === b.material);
  const receipt = b.receipt ? JSON.parse(b.receipt) : null;
  const q = b.status === "quarantined";
  return `<div class="batch-card st-${b.status}">
    <div class="batch-head">
      <div class="batch-title">
        <span class="mat-swatch" style="background:${MAT_HEX[b.material]}"></span>
        ${b.id}
        <span class="pill ${q ? "pill-coral" : "pill-sea"}">${BATCH_STATUS[b.status] || b.status}</span>
      </div>
      <div class="batch-actions">
        ${["open", "running"].includes(b.status) ? `<button class="btn btn-sm btn-primary" data-act="disinfect" data-id="${b.id}">运行消毒</button>` : ""}
        ${b.status === "disinfected" ? `<button class="btn btn-sm btn-primary" data-act="dry-start" data-id="${b.id}">进入干燥</button>` : ""}
        ${["open", "running", "disinfected"].includes(b.status) ? `<button class="btn btn-sm btn-coral" data-act="batch-quarantine" data-id="${b.id}">整批隔离</button>` : ""}
      </div>
    </div>
    <div class="batch-body">
      <div class="sub">${S.constants.materials[b.material]} · ${S.constants.clean_methods[b.clean_method]} · 规定干燥 ${b.dry_minutes} 分钟 · 建于 ${esc(b.created_at)}</div>
      <div class="batch-items">
        ${b.items.map(i => `<span class="mini-toy">${i.id}
          ${i.status === "quarantined" ? `<span class="q-lock" title="已隔离">${LOCK}</span>` : ""}</span>`).join("") || '<span class="muted">空批次</span>'}
      </div>
      ${["open", "running"].includes(b.status) ? `<div class="add-toy-inline">
        <select data-add-sel="${b.id}">
          <option value="">加入回收箱中的${S.constants.materials[b.material]}玩具…</option>
          ${candidates.map(t => `<option value="${t.id}">${t.id} ${esc(t.name)}</option>`).join("")}
        </select>
        <button class="btn btn-sm" data-act="batch-add" data-id="${b.id}">加入批次</button>
      </div>` : ""}
      ${receipt ? `<div class="receipt">设备回执 <b>${esc(b.device_batch_no)}</b>：
        ${receipt.ok ? `<span class="pill pill-ok">达标 ${receipt.temperature_c}℃ / ${receipt.duration_min}分钟</span>`
                     : `<span class="pill pill-coral">失败 ${receipt.temperature_c}℃ / ${receipt.duration_min}分钟 — ${esc(receipt.message)}</span>`}</div>` : ""}
    </div>
  </div>`;
}

/* ---------------- 渲染：干燥计时 ---------------- */
function renderDrying () {
  if (!S.toys) { $("#dryingList").innerHTML = ""; return; }
  const drying = S.toys.filter(t => t.status === "drying");
  const batches = {};
  drying.forEach(t => (batches[t.batch_id] ||= []).push(t));
  const byId = Object.fromEntries(S.batches.map(b => [b.id, b]));
  const html = Object.entries(batches).map(([bid, toys]) => {
    const b = byId[bid] || { dry_minutes: 0 };
    const readyAt = parseTs(toys[0].dry_ready_at);
    const totalMs = b.dry_minutes * 60 * 1000;
    const remain = readyAt - serverNow();
    const pct = Math.max(0, Math.min(100, 100 - remain / totalMs * 100));
    const ready = remain <= 0;
    return `<div class="dry-card ${ready ? "ready" : ""}">
      <div class="dry-top">
        <div><b>${bid}</b> <span class="pill pill-gray">${S.constants.materials[toys[0].material]}</span>
          <span class="muted">${toys.length} 件 · 规定干燥 ${b.dry_minutes} 分钟</span></div>
        <div><span class="countdown">${ready ? "干燥完成，可回库投放" : "剩余 " + fmtLeft(remain)}</span>
          <button class="btn btn-sm ${ready ? "btn-primary" : ""}" data-act="dry-complete" data-id="${bid}" data-force="0">${ready ? "确认回库" : "提前取用（应拒绝）"}</button>
        </div>
      </div>
      <div class="progress"><span style="width:${pct.toFixed(1)}%"></span></div>
      <div class="dry-meta"><span>干燥到时：${esc(toys[0].dry_ready_at)}</span>
        <span>${toys.map(t => t.id).join("、")}</span></div>
    </div>`;
  }).join("");
  $("#dryingList").innerHTML = html || '<div class="empty">当前没有干燥中的玩具；消毒完成后请进入干燥。</div>';
}

/* ---------------- 渲染：破损处理 ---------------- */
function renderDamage () {
  $("#damageKind").innerHTML =
    `<option value="sharp_edge">锐边</option>
     <option value="loose_part">掉件（零件缺失）</option>
     <option value="contamination">污染风险</option>`;
  $("#damageList").innerHTML = S.damage.map(d => `
    <div class="card-row">
      <div class="row-main">
        ${d.photo_path ? `<img class="damage-thumb" src="${photoUrl(d.photo_path)}" alt="破损照片">` : ""}
        <div><b>${d.toy_id}</b>
          <span class="pill pill-coral">${({ sharp_edge: "锐边", loose_part: "掉件", contamination: "污染风险" })[d.kind]}</span>
          <div class="row-sub">${esc(d.note || "—")} · ${esc(d.actor)} · ${esc(d.ts)}</div>
        </div>
      </div>
      <span class="pill ${S.toys.find(t => t.id === d.toy_id)?.status === "quarantined" ? "pill-coral" : "pill-gray"}">
        ${LOCK} 隔离锁定，待护士长复核</span>
    </div>`).join("") || '<div class="empty">暂无破损记录</div>';
}

/* ---------------- 渲染：隔离复核 ---------------- */
function renderQuarantine () {
  const list = S.toys.filter(t => ["quarantined", "repair"].includes(t.status));
  const isNurse = role === "nurse";
  $("#quarantineList").innerHTML = list.map(t => {
    const reason = S.constants.reasons[t.quarantine_reason] || (t.status === "repair" ? "维修中" : "隔离");
    return `<div class="q-card">
      <div class="q-photo">
        ${t.damaged_photo ? `<img src="${photoUrl(t.damaged_photo)}" alt="破损照片">`
                          : `<div class="no-photo">${LOCK}<br>无照片</div>`}
      </div>
      <div class="q-body">
        <div class="q-title">${ICON(t.icon, 20)} ${t.id} ${esc(t.name)}
          <span class="q-lock">${LOCK} 隔离中（锁定）</span>
          <span class="pill pill-coral right">${esc(reason)}</span></div>
        <div class="q-meta">${S.constants.materials[t.material]} · 适用 ${t.age_min}-${t.age_max} 岁 ·
          零件 ${t.parts_present}/${t.parts_total} · 隔离时间 ${esc(t.quarantined_at)}</div>
        ${t.quarantine_note ? `<div class="q-note">${esc(t.quarantine_note)}</div>` : ""}
        <div class="q-actions">
          ${t.status === "quarantined" ? `<button class="btn btn-sm" data-act="repair-start" data-id="${t.id}" ${role === "front_desk" ? "disabled" : ""}>送修</button>` : ""}
          ${t.status === "repair" ? `<button class="btn btn-sm" data-act="repair-complete" data-id="${t.id}" ${role === "front_desk" ? "disabled" : ""}>维修完成（回到隔离待复核）</button>` : ""}
          ${isNurse ? `
            <button class="btn btn-sm btn-primary" data-act="release" data-id="${t.id}" data-disp="store">复核合格·解除隔离</button>
            <button class="btn btn-sm" data-act="release" data-id="${t.id}" data-disp="reprocess">重新清洁消毒</button>
            <button class="btn btn-sm btn-coral" data-act="release" data-id="${t.id}" data-disp="retire">报废</button>`
            : `<span class="nurse-note">🔒 普通清洁不能解除隔离；仅护士长可复核解除（当前：${S.constants.roles[role]}）</span>`}
        </div>
      </div>
    </div>`;
  }).join("") || '<div class="empty">当前没有隔离中的玩具</div>';
}

/* ---------------- 渲染：追溯 ---------------- */
function renderIncidents () {
  $("#incidentList").innerHTML = S.incidents.map(i => `
    <div class="inc ${i.resolved ? "resolved" : ""}">
      <span class="ik">${S.constants.incidents[i.kind] || i.kind}</span>
      ${i.toy_id ? ` · ${i.toy_id}` : ""}${i.batch_id ? ` · ${i.batch_id}` : ""}
      ${i.resolved ? '<span class="pill pill-ok" style="margin-left:6px">已复核</span>' : '<span class="pill pill-coral" style="margin-left:6px">待处理</span>'}
      <div class="im">${esc(i.note || "")} · ${esc(i.actor)} · ${esc(i.ts)}</div>
    </div>`).join("") || '<div class="empty">暂无异常登记</div>';
}
async function renderEvents () {
  const qs = new URLSearchParams();
  if (traceFilter.toy_id) qs.set("toy_id", traceFilter.toy_id);
  if (traceFilter.batch_id) qs.set("batch_id", traceFilter.batch_id);
  const data = await api("/api/trace?" + qs.toString());
  $("#eventList").innerHTML = data.events.map(e => `
    <div class="ev">
      <span class="ts">${esc(e.ts.slice(5))}</span>
      <span class="et">${S.constants.events[e.event_type] || e.event_type}</span>
      <span>${e.toy_id || ""}${e.batch_id ? " / " + e.batch_id : ""}</span>
      <span class="ed">${esc(e.actor)}${e.detail ? " · " + e.detail : ""}</span>
    </div>`).join("") || '<div class="empty">没有周转事件</div>';
}

/* ---------------- 事件绑定 ---------------- */
$("#deployForm").addEventListener("submit", async e => {
  e.preventDefault();
  const toy = $("#deployToy").value, zone = $("#deployZone").value;
  if (!toy) return toast("请选择清洁库中的玩具", "err");
  try {
    await api("/api/deploy", { toy_id: toy, zone_id: zone });
    toast(`${toy} 投放成功`, "ok"); await refresh();
  } catch (e) { toast("投放失败：" + e.message, "err"); }
});

$("#deployConcurrentBtn").addEventListener("click", async () => {
  const toy = $("#deployToy").value;
  if (!toy) return toast("请选择一件清洁库玩具，再模拟双人并发", "err");
  const zone = $("#deployZone").value;
  const [a, b] = await Promise.allSettled([
    api("/api/deploy", { toy_id: toy, zone_id: zone }),
    api("/api/deploy", { toy_id: toy, zone_id: zone }),
  ]);
  const msg = (r, who) => r.status === "fulfilled"
    ? `${who}：投放成功 ✔` : `${who}：被拒绝（${r.reason.message}）✘`;
  toast(msg(a, "员工甲") + "　" + msg(b, "员工乙"), a.status !== b.status ? "ok" : "err");
  await refresh();
});

$("#recycleForm").addEventListener("submit", async e => {
  e.preventDefault();
  const toy = $("#recycleToy").value;
  const z = $("#recycleZone").value;
  try {
    const r = await api("/api/recycle", { toy_id: toy, found_zone_id: z || null });
    toast(r.already_in_bin ? `${toy} 已在回收箱（幂等）` : `${toy} 回收扫描成功`, "ok");
    e.target.reset(); await refresh();
  } catch (e) { toast("回收失败：" + e.message, "err"); }
});

$("#missedForm").addEventListener("submit", async e => {
  e.preventDefault();
  const toy = e.target.toy_id.value;
  try { await api("/api/missed-scan", { toy_id: toy, note: "人工登记回收漏扫" });
    toast(`已登记 ${toy} 回收漏扫`, "ok"); e.target.reset(); await refresh();
  } catch (e) { toast("登记失败：" + e.message, "err"); }
});

$("#batchForm").addEventListener("submit", async e => {
  e.preventDefault();
  try { const id = await api("/api/batches", { material: $("#batchMaterial").value });
    toast(`已新建 ${id}`, "ok"); await refresh();
  } catch (e) { toast("建批失败：" + e.message, "err"); }
});

$("#damageForm").addEventListener("submit", async e => {
  e.preventDefault();
  const f = e.target;
  const file = $("#damagePhoto").files[0];
  const send = (dataUrl) => api("/api/damage", {
    toy_id: f.toy_id.value, kind: f.kind.value, note: f.note.value,
    photo_data_url: dataUrl || null,
  });
  try {
    if (file) {
      const dataUrl = await new Promise((res, rej) => {
        const r = new FileReader(); r.onload = () => res(r.result); r.onerror = rej; r.readAsDataURL(file);
      });
      await send(dataUrl);
    } else await send(null);
    toast(`${f.toy_id.value} 已立即隔离并登记破损`, "ok");
    f.reset(); await refresh(); switchTab("quarantine");
  } catch (e) { toast("登记失败：" + e.message, "err"); }
});

$("#traceForm").addEventListener("submit", async e => {
  e.preventDefault();
  traceFilter = { toy_id: e.target.toy_id.value, batch_id: e.target.batch_id.value };
  await renderEvents();
});
$("#traceReset").addEventListener("click", async () => {
  traceFilter = {}; $("#traceForm").reset(); await renderEvents();
});

/* 事件委托：列表按钮 */
document.body.addEventListener("click", async e => {
  const btn = e.target.closest("button[data-act]");
  if (!btn) return;
  const act = btn.dataset.act, id = btn.dataset.id;
  try {
    if (act === "quick-recycle") { await api("/api/recycle", { toy_id: id }); toast(`${id} 已回收`, "ok"); }
    else if (act === "batch-add") {
      const sel = $(`[data-add-sel="${id}"]`);
      const toy = sel.value;
      if (!toy) return toast("请选择要加入批次的玩具", "err");
      await api(`/api/batches/${id}/add`, { toy_id: toy });
      toast(`${toy} 已加入 ${id}`, "ok");
    }
    else if (act === "disinfect") {
      const r = await api(`/api/batches/${id}/disinfect`, {});
      toast(r.ok ? `${id} 消毒达标（设备 ${r.device_batch_no}）` : `${id} 消毒失败：${r.message}`, r.ok ? "ok" : "err");
    }
    else if (act === "dry-start") { await api(`/api/batches/${id}/dry-start`, {}); toast(`${id} 开始干燥计时`, "ok"); }
    else if (act === "dry-complete") {
      await api(`/api/batches/${id}/dry-complete`, { force: btn.dataset.force === "1" });
      toast(`${id} 干燥完成，已回清洁库`, "ok");
    }
    else if (act === "batch-quarantine") {
      await api(`/api/batches/${id}/quarantine`, { reason: "batch_risk", note: "人工整批隔离" });
      toast(`${id} 已整批隔离`, "ok"); switchTab("quarantine");
    }
    else if (act === "repair-start") { await api(`/api/toys/${id}/repair-start`, {}); toast(`${id} 已送修`, "ok"); }
    else if (act === "repair-complete") {
      await api(`/api/toys/${id}/repair-complete`, { parts_restored: 1, note: "维修完成待复核" });
      toast(`${id} 维修完成，回到隔离待护士长复核`, "ok");
    }
    else if (act === "release") {
      const disp = btn.dataset.disp;
      await api(`/api/toys/${id}/release`, { disposition: disp, note: "护士长复核" });
      toast(`${id} 隔离解除（${{ store: "回清洁库", reprocess: "重新清洁", retire: "报废" }[disp]}）`, "ok");
    }
    await refresh();
  } catch (err) {
    toast("操作被拒绝：" + err.message, "err");
    await refresh();
  }
});
$("#areaFilter").addEventListener("click", e => {
  const c = e.target.closest(".chip");
  if (!c) return;
  areaFilter = c.dataset.area; renderLibrary();
});

/* ---------------- 轮询刷新 ---------------- */
async function refresh () {
  const data = await api("/api/state");
  S = data;
  const st = parseTs(S.now);
  if (st) nowOffset.v = st.getTime() - Date.now();
  if (S.zones) {
    renderWaiting(); renderDeploy(); renderLibrary(); renderRecycle();
    renderBatches(); renderDamage(); renderQuarantine(); renderIncidents();
  } else {
    renderWaiting();
  }
  renderDrying();
  if ($("#tab-trace").classList.contains("active")) renderEvents();
}
setInterval(renderDrying, 1000);
setInterval(refresh, 4000);

/* ---------------- 启动 ---------------- */
renderRole();
refresh().catch(e => toast("加载失败：" + e.message, "err"));

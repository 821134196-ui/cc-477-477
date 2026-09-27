/* 候诊玩具消毒与轮换系统 —— 前端逻辑（无框架，fetch 直连本地 API） */
"use strict";

const state = {
  staff: null, staffList: [], meta: null,
  tab: "zones",
  toyFilter: { status: "", material: "" },
  excFilter: "",
  batchSel: new Set(),
};

/* ---------------- 基础 ---------------- */
function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g,
    c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function fmtTime(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  return d.toLocaleString("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" });
}

async function api(path, opts = {}) {
  const res = await fetch(path, {
    method: opts.method || "GET",
    headers: {
      "Content-Type": "application/json",
      "X-Staff-Id": state.staff ? String(state.staff.id) : "",
    },
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    toast(data.error || `请求失败(${res.status})`, "err");
    throw new Error(data.error || res.status);
  }
  return data;
}

let toastTimer = null;
function toast(msg, kind = "ok") {
  const el = document.getElementById("toast");
  el.textContent = msg;
  el.className = kind;
  el.style.display = "block";
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.style.display = "none"; }, 3600);
}

/* ---------------- 图标 ---------------- */
function materialIcon(material, size = 26) {
  const c = "#4e8fbf";
  const shapes = {
    plastic: `<rect x="4" y="4" width="18" height="18" rx="3" stroke="${c}" fill="none" stroke-width="2"/><rect x="9" y="9" width="8" height="8" rx="1.5" fill="${c}" opacity=".35"/>`,
    plush: `<circle cx="13" cy="14" r="8" stroke="${c}" fill="none" stroke-width="2"/><circle cx="7" cy="7" r="3" fill="${c}" opacity=".35"/><circle cx="19" cy="7" r="3" fill="${c}" opacity=".35"/>`,
    wood: `<rect x="3" y="8" width="20" height="10" rx="2" stroke="${c}" fill="none" stroke-width="2"/><path d="M6 11h14M6 15h9" stroke="${c}" stroke-width="1.4" opacity=".6"/>`,
    silicone: `<ellipse cx="13" cy="13" rx="9" ry="7" stroke="${c}" fill="none" stroke-width="2"/><ellipse cx="10" cy="11" rx="2.5" ry="1.6" fill="${c}" opacity=".35"/>`,
    metal: `<polygon points="13,3 22,8 22,18 13,23 4,18 4,8" stroke="${c}" fill="none" stroke-width="2"/><circle cx="13" cy="13" r="3" fill="${c}" opacity=".35"/>`,
    electronic: `<rect x="5" y="5" width="16" height="16" rx="2" stroke="${c}" fill="none" stroke-width="2"/><path d="M9 2v3M17 2v3M9 21v3M17 21v3" stroke="${c}" stroke-width="1.6"/><rect x="10" y="10" width="6" height="6" fill="${c}" opacity=".35"/>`,
  };
  return `<svg width="${size}" height="${size}" viewBox="0 0 26 26">${shapes[material] || shapes.plastic}</svg>`;
}

function lockIcon(size = 14) {
  return `<svg width="${size}" height="${size}" viewBox="0 0 24 24" fill="none">
    <rect x="5" y="10" width="14" height="10" rx="2" fill="#d95f57"/>
    <path d="M8 10V7a4 4 0 0 1 8 0v3" stroke="#d95f57" stroke-width="2.2"/>
    <circle cx="12" cy="15" r="1.6" fill="#fff"/></svg>`;
}

function statusBadge(status) {
  const label = state.meta.status_labels[status] || status;
  if (status === "isolated")
    return `<span class="st st-isolated">${lockIcon(11)} ${label}</span>`;
  return `<span class="st st-${status}">${label}</span>`;
}

/* ---------------- 初始化 ---------------- */
async function init() {
  // 先用临时身份拉员工表（staff 接口对前台也开放）
  state.staff = null;
  const res = await fetch("/api/staff", { headers: { "X-Staff-Id": "1" } });
  const data = await res.json();
  state.staffList = data.staff || [];
  const saved = Number(localStorage.getItem("staffId")) || (state.staffList[0] || {}).id;
  state.staff = state.staffList.find(s => s.id === saved) || state.staffList[0];

  const sel = document.getElementById("staffSelect");
  sel.innerHTML = state.staffList.map(s =>
    `<option value="${s.id}" ${s.id === state.staff.id ? "selected" : ""}>${esc(s.name)}</option>`).join("");
  sel.onchange = () => {
    state.staff = state.staffList.find(s => s.id === Number(sel.value));
    localStorage.setItem("staffId", sel.value);
    if (state.staff.role === "front_desk") state.tab = "zones";
    renderChrome(); render();
  };

  state.meta = await api("/api/meta");
  renderChrome();
  render();
  setInterval(() => { if (["zones", "drying"].includes(state.tab)) render(); }, 10000);
  setInterval(tickCountdowns, 1000);
}

const TABS = [
  ["zones", "候诊区"],
  ["toys", "玩具库"],
  ["ops", "回收清洁"],
  ["batches", "消毒批次"],
  ["drying", "干燥计时"],
  ["damage", "破损维修"],
  ["isolation", "隔离复核"],
  ["exceptions", "异常记录"],
  ["turnover", "周转流水"],
];

function renderChrome() {
  const role = state.staff.role;
  const badge = document.getElementById("roleBadge");
  badge.textContent = state.meta.role_labels[role];
  badge.className = "role-badge" + (role === "front_desk" ? " front" : "");
  const tabs = role === "front_desk" ? TABS.filter(t => t[0] === "zones") : TABS;
  document.getElementById("tabs").innerHTML = tabs.map(([key, label]) =>
    `<button data-tab="${key}" class="${state.tab === key ? "active" : ""}">${label}</button>`).join("");
  document.querySelectorAll("#tabs button").forEach(b =>
    b.onclick = () => { state.tab = b.dataset.tab; renderChrome(); render(); });
}

function render() {
  const fn = {
    zones: renderZones, toys: renderToys, ops: renderOps, batches: renderBatches,
    drying: renderDrying, damage: renderDamage, isolation: renderIsolation,
    exceptions: renderExceptions, turnover: renderTurnover,
  }[state.tab];
  if (fn) fn().catch(() => {});
}

/* ---------------- 候诊区（只显示可用数量与轮换时间） ---------------- */
async function renderZones() {
  const { zones } = await api("/api/zones/summary");
  document.getElementById("main").innerHTML = `
    <div class="zone-grid">${zones.map(z => `
      <div class="zone-card">
        <h3>${esc(z.name)}</h3>
        <div class="ages">适用 ${z.age_min}–${z.age_max} 岁</div>
        <div class="count">${z.available}<small> / ${z.capacity} 件可用</small></div>
        <div class="rotation">下一轮换时间：<b>${fmtTime(z.next_rotation_at)}</b></div>
        <div class="bar"><i style="width:${Math.min(100, z.available / z.capacity * 100)}%"></i></div>
      </div>`).join("")}
    </div>
    <p class="empty mt8">候诊区视图仅显示可用数量与轮换时间 · 每 10 秒自动刷新</p>`;
}

/* ---------------- 玩具库（小图标 + 编号密集网格） ---------------- */
async function renderToys() {
  const p = new URLSearchParams();
  if (state.toyFilter.status) p.set("status", state.toyFilter.status);
  if (state.toyFilter.material) p.set("material", state.toyFilter.material);
  const { toys } = await api("/api/toys?" + p);
  const m = state.meta;
  document.getElementById("main").innerHTML = `
    <div class="toolbar">
      <select id="fStatus"><option value="">全部状态</option>${Object.entries(m.status_labels)
        .map(([k, v]) => `<option value="${k}" ${state.toyFilter.status === k ? "selected" : ""}>${v}</option>`).join("")}</select>
      <select id="fMaterial"><option value="">全部材质</option>${Object.entries(m.material_labels)
        .map(([k, v]) => `<option value="${k}" ${state.toyFilter.material === k ? "selected" : ""}>${v}</option>`).join("")}</select>
      <span class="muted">共 ${toys.length} 件</span>
      <button class="btn ghost sm" onclick="renderToys()">刷新</button>
    </div>
    <div class="toy-grid">${toys.map(t => `
      <div class="toy-cell ${t.status === "isolated" ? "isolated" : ""}" onclick="toyDetail(${t.id})">
        ${t.active_isolations ? `<span class="lock">${lockIcon(13)}</span>` : ""}
        <div class="icon">${materialIcon(t.material)}</div>
        <div class="code">${esc(t.code)}</div>
        <div class="name">${esc(t.name)} · ${m.material_labels[t.material]}</div>
        <div>${statusBadge(t.status)}</div>
      </div>`).join("")}
    </div>
    <div id="toyDetail"></div>`;
  document.getElementById("fStatus").onchange = e => { state.toyFilter.status = e.target.value; renderToys(); };
  document.getElementById("fMaterial").onchange = e => { state.toyFilter.material = e.target.value; renderToys(); };
}

async function toyDetail(id) {
  const [{ turnover }, { zones }] = await Promise.all([
    api("/api/turnover?toy_id=" + id), api("/api/zones")]);
  const { toys } = await api("/api/toys");
  const t = toys.find(x => x.id === id);
  const m = state.meta;
  const canDeploy = t.status === "in_storage";
  const canDamage = !["isolated", "repair", "retired"].includes(t.status);
  document.getElementById("toyDetail").innerHTML = `
    <div class="panel mt16">
      <h2>${materialIcon(t.material, 20)} ${esc(t.code)} ${esc(t.name)} ${statusBadge(t.status)}</h2>
      <div class="muted">材质 ${m.material_labels[t.material]} · 适用 ${t.age_min}–${t.age_max} 岁 ·
        零件 ${t.parts_count} 件 · 规定干燥 ${t.drying_minutes} 分钟
        ${t.zone_name ? " · 当前位于 " + esc(t.zone_name) : ""}</div>
      <div class="form-row mt8">
        ${canDeploy ? `<div class="field"><label>投放到候诊区</label>
          <span><select id="deployZone">${zones.map(z =>
            `<option value="${z.id}">${esc(z.name)}（${z.age_min}-${z.age_max}岁）</option>`).join("")}</select>
          <button class="btn sm" onclick="doDeploy(${t.id})">投放</button></span></div>` : ""}
        ${canDamage ? `<div class="field"><label>发现破损/污染</label>
          <span><select id="dmgType">${Object.entries(m.damage_labels)
            .map(([k, v]) => `<option value="${k}">${v}</option>`).join("")}</select>
          <button class="btn coral sm" onclick="quickDamage(${t.id})">报损并隔离</button></span></div>` : ""}
      </div>
      <div class="detail-box">
        <b>周转记录</b>${turnover.length ? turnover.map(v => `
          <div>${fmtTime(v.created_at)} · ${m.status_labels[v.from_status] || "初始"} →
            ${m.status_labels[v.to_status]} · ${esc(v.actor_name || "系统")} · ${esc(v.details || "")}</div>`
        ).join("") : '<div class="muted">暂无记录</div>'}
      </div>
    </div>`;
  document.getElementById("toyDetail").scrollIntoView({ behavior: "smooth" });
}

async function doDeploy(toyId) {
  const zoneId = document.getElementById("deployZone").value;
  await api("/api/deploy", { method: "POST", body: { toy_id: toyId, zone_id: Number(zoneId) } });
  toast("投放成功"); renderToys();
}

async function quickDamage(toyId) {
  const type = document.getElementById("dmgType").value;
  await api("/api/damage", { method: "POST",
    body: { toy_id: toyId, damage_type: type, description: "玩具库快捷报损" } });
  toast("已报损并立即隔离"); renderToys();
}

/* ---------------- 回收清洁 ---------------- */
async function renderOps() {
  const [{ toys }, { zones }] = await Promise.all([api("/api/toys"), api("/api/zones")]);
  const m = state.meta;
  const pending = toys.filter(t => t.status === "pending_clean");
  const cleaned = toys.filter(t => t.status === "cleaned");
  const bins = zones.flatMap(z => z.bins.map(b => ({ ...b, zone_name: z.name })));
  document.getElementById("main").innerHTML = `
    <div class="panel">
      <h2>回收扫描</h2>
      <div class="form-row">
        <div class="field"><label>玩具编号</label><input id="scanToy" placeholder="如 TOY-004"></div>
        <div class="field"><label>回收箱</label><select id="scanBin">${bins.map(b =>
          `<option value="${esc(b.code)}">${esc(b.code)} · ${esc(b.zone_name)}</option>`).join("")}</select></div>
        <button class="btn" onclick="doRecover()">扫码回收</button>
      </div>
    </div>
    <div class="panel">
      <h2>待清洁 <span class="tag">${pending.length} 件 · 按材质选择擦拭/浸泡/机洗</span></h2>
      ${pending.length ? `<table><tr><th>编号</th><th>名称</th><th>材质</th><th>清洁方式</th><th></th></tr>
        ${pending.map(t => `<tr>
          <td>${esc(t.code)}</td><td>${esc(t.name)}</td><td>${m.material_labels[t.material]}</td>
          <td><select id="m${t.id}">${m.clean_matrix[t.material].map(mm =>
            `<option value="${mm}" ${m.default_method[t.material] === mm ? "selected" : ""}>${m.method_labels[mm]}</option>`).join("")}</select></td>
          <td><button class="btn sm" onclick="doClean(${t.id})">执行清洁</button></td>
        </tr>`).join("")}</table>` : '<div class="empty">暂无待清洁玩具</div>'}
    </div>
    <div class="panel">
      <h2>已清洁 · 待组批 <span class="tag">勾选同材质玩具组成消毒批次</span></h2>
      ${cleaned.length ? `<table><tr><th></th><th>编号</th><th>名称</th><th>材质</th></tr>
        ${cleaned.map(t => `<tr>
          <td><input type="checkbox" class="batchCk" value="${t.id}" data-mat="${t.material}"></td>
          <td>${esc(t.code)}</td><td>${esc(t.name)}</td><td>${m.material_labels[t.material]}</td>
        </tr>`).join("")}</table>
        <div class="form-row mt8">
          <button class="btn" onclick="doCreateBatch()">组成消毒批次</button>
          <span class="muted" id="batchHint"></span>
        </div>` : '<div class="empty">暂无已清洁玩具</div>'}
    </div>`;
}

async function doRecover() {
  const toy = document.getElementById("scanToy").value.trim();
  const bin = document.getElementById("scanBin").value;
  if (!toy) return toast("请填写玩具编号", "err");
  await api("/api/recover", { method: "POST", body: { toy_code: toy, bin_code: bin } });
  toast(`已回收 ${toy}，进入待清洁`); renderOps();
}

async function doClean(toyId) {
  const method = document.getElementById("m" + toyId).value;
  await api("/api/clean", { method: "POST", body: { toy_id: toyId, method } });
  toast("清洁完成"); renderOps();
}

async function doCreateBatch() {
  const cks = [...document.querySelectorAll(".batchCk:checked")];
  if (!cks.length) return toast("请先勾选玩具", "err");
  const mats = new Set(cks.map(c => c.dataset.mat));
  if (mats.size > 1) return toast("消毒批次必须同材质分组", "err");
  const material = [...mats][0];
  const method = state.meta.default_method[material];
  const r = await api("/api/batches", { method: "POST",
    body: { material, method, toy_ids: cks.map(c => Number(c.value)) } });
  toast(`批次 ${r.code} 已组成，请到“消毒批次”提交设备`);
  state.tab = "batches"; renderChrome(); render();
}

/* ---------------- 消毒批次（材质分组） ---------------- */
async function renderBatches() {
  const { batches } = await api("/api/batches");
  const m = state.meta;
  const groups = {};
  for (const b of batches) (groups[b.material] ||= []).push(b);
  const statusLabel = { open: "待提交", submitted: "已提交", completed: "消毒合格",
    failed: "消毒失败", isolated: "批量隔离" };
  document.getElementById("main").innerHTML = Object.keys(groups).length
    ? Object.entries(groups).map(([mat, list]) => `
      <div class="material-group">
        <h3>${materialIcon(mat, 16)} ${m.material_labels[mat]}批次（${list.length}）</h3>
        ${list.map(b => `
          <div class="batch-card ${b.status === "isolated" ? "isolated" : ""}">
            <div class="head">
              <span class="code">${esc(b.code)}</span>
              ${b.status === "isolated"
                ? `<span class="lock-flag">${lockIcon(13)} 批量隔离</span>`
                : `<span class="st st-${b.status === "completed" ? "in_storage" : b.status === "open" ? "cleaned" : "pending_clean"}">${statusLabel[b.status]}</span>`}
              <span class="muted">${m.method_labels[b.method]} · ${b.items.length} 件 · ${esc(b.creator)} · ${fmtTime(b.created_at)}</span>
              ${b.status === "open" ? `<button class="btn sm" onclick="doSubmitBatch(${b.id})">提交消毒设备</button>` : ""}
              ${b.status === "isolated" ? `<button class="btn coral sm" onclick="doReleaseBatch(${b.id})">护士长批量解除</button>` : ""}
              <button class="btn ghost sm" onclick="toggleTrace(${b.id})">追溯</button>
            </div>
            <div class="items">${b.items.map(i => `${esc(i.code)}（${m.status_labels[i.status]}）`).join("、")}</div>
            ${b.receipt ? `<div class="receipt">设备回执：${esc(b.receipt.device_id)} · 周期 ${esc(b.receipt.cycle_id)} · ${esc(b.receipt.message)}</div>` : ""}
            <div class="detail-box" id="trace-${b.id}" style="display:none"></div>
          </div>`).join("")}
      </div>`).join("")
    : '<div class="empty">暂无消毒批次</div>';
}

async function doSubmitBatch(id) {
  const r = await api(`/api/batches/${id}/submit`, { method: "POST", body: {} });
  toast(r.receipt.result === "success" ? "消毒合格，进入干燥" : `消毒失败：${r.receipt.message}`,
    r.receipt.result === "success" ? "ok" : "err");
  renderBatches();
}

async function doReleaseBatch(id) {
  await api(`/api/batches/${id}/release-isolation`, { method: "POST", body: {} });
  toast("批次隔离已解除，玩具回待清洁"); renderBatches();
}

async function toggleTrace(id) {
  const box = document.getElementById("trace-" + id);
  if (box.style.display !== "none") { box.style.display = "none"; return; }
  const t = await api(`/api/batches/${id}/trace`);
  const m = state.meta;
  box.innerHTML = `
    <b>批次追溯</b>
    <div>玩具：${t.toys.map(x => `${esc(x.code)}（${m.status_labels[x.status]}）`).join("、") || "无"}</div>
    <div>关联隔离：${t.isolations.map(i => `#${i.id} ${esc(i.toy_id)} ${i.status === "active" ? "隔离中" : "已解除"}`).join("、") || "无"}</div>
    <div>关联异常：${t.exceptions.map(e => `${m.exception_labels[e.type]}（${fmtTime(e.created_at)}）`).join("、") || "无"}</div>
    <div>干燥记录：${t.drying_records.map(d => `#${d.id} 需${d.required_minutes}分钟 ${d.status === "done" ? "已完成" : "干燥中"}`).join("、") || "无"}</div>`;
  box.style.display = "block";
}

/* ---------------- 干燥计时 ---------------- */
async function renderDrying() {
  const { drying } = await api("/api/drying");
  const m = state.meta;
  document.getElementById("main").innerHTML = `
    <div class="panel">
      <h2>干燥计时 <span class="tag">消毒合格后须满规定干燥时长才能回库 · 每 10 秒自动刷新</span></h2>
      ${drying.length ? `<table>
        <tr><th>编号</th><th>名称</th><th>材质</th><th>规定时长</th><th>开始</th><th>到期</th><th>剩余</th><th></th></tr>
        ${drying.map(d => `<tr>
          <td>${esc(d.toy_code)}</td><td>${esc(d.toy_name)}</td>
          <td>${m.material_labels[d.material]}</td><td>${d.required_minutes} 分钟</td>
          <td>${fmtTime(d.started_at)}</td><td>${fmtTime(d.due_at)}</td>
          <td><span class="countdown" data-due="${esc(d.due_at)}"></span></td>
          <td><button class="btn sm" onclick="doReleaseDrying(${d.id})">上架回库</button></td>
        </tr>`).join("")}</table>` : '<div class="empty">暂无干燥中的玩具</div>'}
    </div>`;
  tickCountdowns();
}

function tickCountdowns() {
  document.querySelectorAll(".countdown").forEach(el => {
    const left = Math.floor((new Date(el.dataset.due) - Date.now()) / 1000);
    if (left <= 0) { el.textContent = "已到时可回库"; el.classList.add("over"); }
    else {
      el.classList.remove("over");
      el.textContent = `${Math.floor(left / 60)}分${String(left % 60).padStart(2, "0")}秒`;
    }
  });
}

async function doReleaseDrying(id) {
  await api(`/api/drying/${id}/release`, { method: "POST", body: {} });
  toast("已上架回清洁库"); renderDrying();
}

/* ---------------- 破损维修 ---------------- */
async function renderDamage() {
  const [{ damage }, { toys }] = await Promise.all([api("/api/damage"), api("/api/toys")]);
  const m = state.meta;
  const candidates = toys.filter(t => !["isolated", "repair", "retired"].includes(t.status));
  const repLabel = { pending: "待维修", in_repair: "维修中", repaired: "已修复待复核", scrapped: "已报废" };
  document.getElementById("main").innerHTML = `
    <div class="panel">
      <h2>破损上报 <span class="tag">锐边 / 掉件 / 污染风险 → 立即隔离，普通清洁不能解除</span></h2>
      <div class="form-row">
        <div class="field"><label>玩具</label><select id="dToy">${candidates.map(t =>
          `<option value="${t.id}">${esc(t.code)} ${esc(t.name)}</option>`).join("")}</select></div>
        <div class="field"><label>类型</label><select id="dType">${Object.entries(m.damage_labels)
          .map(([k, v]) => `<option value="${k}">${v}</option>`).join("")}</select></div>
        <div class="field"><label>描述</label><input id="dDesc" placeholder="破损情况"></div>
        <div class="field"><label>照片</label><input type="file" id="dPhoto" accept="image/*"></div>
        <button class="btn coral" onclick="doReportDamage()">报损并隔离</button>
      </div>
    </div>
    <div class="panel">
      <h2>维修处理</h2>
      ${damage.length ? `<table>
        <tr><th>#</th><th>玩具</th><th>类型</th><th>描述</th><th>照片</th><th>状态</th><th>操作</th></tr>
        ${damage.map(d => `<tr>
          <td>${d.id}</td><td>${esc(d.toy_code)} ${esc(d.toy_name)}</td>
          <td>${m.damage_labels[d.damage_type]}</td><td>${esc(d.description || "")}</td>
          <td>${d.photo_path ? `<a href="/photos/${esc(d.photo_path.split("/").pop())}" target="_blank">查看</a>` : "—"}</td>
          <td>${repLabel[d.repair_status]}</td>
          <td>
            ${d.repair_status === "pending" ? `<button class="btn sm" onclick="doRepairStart(${d.id})">开始维修</button>` : ""}
            ${d.repair_status === "in_repair" ? `<button class="btn sm" onclick="doRepairDone(${d.id})">维修完成</button>` : ""}
            ${["pending", "in_repair"].includes(d.repair_status) ? `<button class="btn coral sm" onclick="doScrap(${d.id})">报废</button>` : ""}
          </td>
        </tr>`).join("")}</table>` : '<div class="empty">暂无报损记录</div>'}
    </div>`;
}

async function doReportDamage() {
  const file = document.getElementById("dPhoto").files[0];
  let photo = {};
  if (file) {
    const b64 = await new Promise(res => {
      const r = new FileReader();
      r.onload = () => res(r.result.split(",")[1]);
      r.readAsDataURL(file);
    });
    photo = { photo_name: file.name, photo_data: b64 };
  }
  await api("/api/damage", { method: "POST", body: {
    toy_id: Number(document.getElementById("dToy").value),
    damage_type: document.getElementById("dType").value,
    description: document.getElementById("dDesc").value,
    ...photo } });
  toast("已报损，玩具立即隔离"); renderDamage();
}

async function doRepairStart(id) {
  await api(`/api/damage/${id}/repair-start`, { method: "POST", body: {} });
  toast("已开始维修"); renderDamage();
}
async function doRepairDone(id) {
  const note = prompt("维修说明", "已修复并复检");
  if (note === null) return;
  await api(`/api/damage/${id}/repair-done`, { method: "POST", body: { note } });
  toast("维修完成，待护士长复核解除隔离"); renderDamage();
}
async function doScrap(id) {
  if (!confirm("确认报废？仅护士长可执行，玩具将退役")) return;
  await api(`/api/damage/${id}/scrap`, { method: "POST", body: {} });
  toast("已报废退役"); renderDamage();
}

/* ---------------- 隔离复核（锁图标 + 文字） ---------------- */
async function renderIsolation() {
  const { isolations } = await api("/api/isolations");
  const m = state.meta;
  document.getElementById("main").innerHTML = `
    <div class="panel">
      <h2>${lockIcon(16)} 隔离复核 <span class="tag">仅护士长可解除隔离 · 解除后回待清洁重新走工序</span></h2>
      ${isolations.length ? `<table>
        <tr><th>#</th><th>玩具</th><th>原因</th><th>来源批次</th><th>隔离时间</th><th>状态</th><th>操作</th></tr>
        ${isolations.map(i => `<tr>
          <td>${i.id}</td>
          <td>${esc(i.toy_code)} ${esc(i.toy_name)}</td>
          <td>${m.damage_labels[i.reason] || esc(i.reason)}</td>
          <td>${i.batch_code ? esc(i.batch_code) : "—"}</td>
          <td>${fmtTime(i.isolated_at)}</td>
          <td>${i.status === "active"
            ? `<span class="lock-flag">${lockIcon(13)} 隔离中</span>`
            : `<span class="muted">已解除 · ${esc(i.released_by_name || "")}</span>`}</td>
          <td>${i.status === "active"
            ? `<button class="btn coral sm" onclick="doReleaseIso(${i.id})">解除隔离</button>` : ""}</td>
        </tr>`).join("")}</table>` : '<div class="empty">暂无隔离记录</div>'}
    </div>`;
}

async function doReleaseIso(id) {
  const note = prompt("复核说明", "复核通过，解除隔离");
  if (note === null) return;
  await api(`/api/isolations/${id}/release`, { method: "POST", body: { note } });
  toast("隔离已解除，玩具回待清洁"); renderIsolation();
}

/* ---------------- 异常记录 ---------------- */
async function renderExceptions() {
  const p = state.excFilter ? "?type=" + state.excFilter : "";
  const { exceptions } = await api("/api/exceptions" + p);
  const m = state.meta;
  document.getElementById("main").innerHTML = `
    <div class="toolbar">
      <select id="excType"><option value="">全部类型</option>${Object.entries(m.exception_labels)
        .map(([k, v]) => `<option value="${k}" ${state.excFilter === k ? "selected" : ""}>${v}</option>`).join("")}</select>
      <span class="muted">共 ${exceptions.length} 条</span>
    </div>
    <div class="panel">
      ${exceptions.length ? `<table>
        <tr><th>时间</th><th>类型</th><th>玩具</th><th>批次</th><th>操作人</th><th>详情</th></tr>
        ${exceptions.map(e => `<tr>
          <td>${fmtTime(e.created_at)}</td>
          <td><span class="st st-pending_clean">${m.exception_labels[e.type]}</span></td>
          <td>${esc(e.toy_code || "—")}</td><td>${esc(e.batch_code || "—")}</td>
          <td>${esc(e.actor_name || "系统")}</td><td>${esc(e.details || "")}</td>
        </tr>`).join("")}</table>` : '<div class="empty">暂无异常记录</div>'}
    </div>`;
  document.getElementById("excType").onchange = e => { state.excFilter = e.target.value; renderExceptions(); };
}

/* ---------------- 周转流水 ---------------- */
async function renderTurnover() {
  const { turnover } = await api("/api/turnover");
  const m = state.meta;
  document.getElementById("main").innerHTML = `
    <div class="panel">
      <h2>周转事件流水 <span class="tag">最近 ${turnover.length} 条</span></h2>
      ${turnover.length ? `<table>
        <tr><th>时间</th><th>玩具</th><th>从</th><th>到</th><th>操作人</th><th>详情</th></tr>
        ${turnover.map(v => `<tr>
          <td>${fmtTime(v.created_at)}</td><td>${esc(v.toy_code)}</td>
          <td>${m.status_labels[v.from_status] || "初始"}</td>
          <td>${statusBadge(v.to_status)}</td>
          <td>${esc(v.actor_name || "系统")}</td><td>${esc(v.details || "")}</td>
        </tr>`).join("")}</table>` : '<div class="empty">暂无周转事件</div>'}
    </div>`;
}

init().catch(e => toast("初始化失败：" + e.message, "err"));

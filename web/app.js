// Jev 面板 —— 无构建步骤，原生 ES module。
// 所有对 TypeSafe 的调用都经由本地服务端；这里永远看不到 API key。

const $ = (sel) => document.querySelector(sel);
const el = (tag, cls, text) => {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text != null) n.textContent = text;
  return n;
};

const fmtUSD = (v) => "$" + v.toFixed(6);
const fmtSec = (v) => v.toFixed(2) + "s";
const pct = (v) => (v * 100).toFixed(0) + "%";

let samples = { en: "", zh: "" };
let dims = null;          // weights 视图的原始判断
let weights = {};         // 当前权重

// ── 主题切换：跟随系统 → 亮 → 暗 → 跟随系统 ──────────────────
const THEMES = [
  { key: null, label: "跟随系统" },
  { key: "light", label: "亮色" },
  { key: "dark", label: "暗色" },
];
let themeIdx = 0;
$("#theme-toggle").addEventListener("click", () => {
  themeIdx = (themeIdx + 1) % THEMES.length;
  const { key, label } = THEMES[themeIdx];
  if (key) document.documentElement.dataset.theme = key;
  else delete document.documentElement.dataset.theme;
  $("#theme-label").textContent = label;
});

// ── 标签页 ──────────────────────────────────────────────────
for (const tab of document.querySelectorAll('[role="tab"]')) {
  tab.addEventListener("click", () => {
    for (const t of document.querySelectorAll('[role="tab"]')) {
      const on = t === tab;
      t.setAttribute("aria-selected", String(on));
      $("#" + t.getAttribute("aria-controls")).hidden = !on;
    }
  });
}

// ── 网络 ────────────────────────────────────────────────────
async function post(route, body = {}) {
  $("#error").hidden = true;
  const res = await fetch(`/api/${route}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({ error: `HTTP ${res.status}` }));
  if (!res.ok || data.error) throw new Error(data.error || `HTTP ${res.status}`);
  return data;
}

function fail(err) {
  const box = $("#error");
  box.textContent = String(err.message || err);
  box.hidden = false;
}

async function withBusy(btn, fn) {
  const label = btn.textContent;
  btn.disabled = true;
  btn.textContent = "请求中…";
  try { await fn(); } catch (err) { fail(err); }
  finally { btn.disabled = false; btn.textContent = label; }
}

// ── state 输入 ──────────────────────────────────────────────
const stateBox = $("#state");

function updateStateMeta() {
  const n = stateBox.value.length;
  $("#state-meta").textContent = n ? `${n} 字符` : "";
}
stateBox.addEventListener("input", updateStateMeta);

for (const btn of document.querySelectorAll("[data-sample]")) {
  btn.addEventListener("click", () => {
    stateBox.value = samples[btn.dataset.sample] || "";
    updateStateMeta();
  });
}

// ── 通用部件 ────────────────────────────────────────────────
function tile(k, v, u) {
  const n = el("div", "tile");
  n.append(el("span", "k", k));
  const val = el("div", "v", v);
  if (u) val.append(el("span", "u", " " + u));
  n.append(val);
  return n;
}

// 概率条：单一色相，长度编码量值；最高项全色，其余压淡
function bars(rows, topLabel) {
  const wrap = el("div", "bars");
  for (const r of rows) {
    const row = el("div", "bar-row");
    if (r.label !== topLabel && !String(r.label).startsWith(String(topLabel))) {
      row.classList.add("dim");
    }
    const track = el("div", "bar-track");
    track.title = `${r.label} — ${r.p.toFixed(3)}`;
    const fill = el("div", "bar-fill");
    fill.style.inlineSize = Math.max(r.p * 100, 0.5) + "%";
    track.append(fill, el("span", "bar-label", r.label));
    row.append(track, el("span", "bar-val", r.p.toFixed(2)));
    wrap.append(row);
  }
  return wrap;
}

// 置信度三档：颜色 + 图标 + 文字，三重编码
function confBand(c) {
  if (c >= 0.8) return { cls: "chip-good", colour: "var(--good)", icon: "●", text: "可自动执行" };
  if (c >= 0.5) return { cls: "chip-warning", colour: "var(--warning)", icon: "▲", text: "建议确认" };
  return { cls: "chip-critical", colour: "var(--critical)", icon: "■", text: "转人工" };
}

function confMeter(c) {
  const band = confBand(c);
  const row = el("div", "conf");
  row.append(el("span", "conf-text", "conf"));
  const meter = el("div", "conf-meter");
  const bar = el("i");
  bar.style.inlineSize = c * 100 + "%";
  bar.style.background = band.colour;
  meter.append(bar);
  row.append(meter, el("span", "conf-text", c.toFixed(2)));
  row.append(el("span", `chip ${band.cls}`, `${band.icon} ${band.text}`));
  return row;
}

// ── 视图 1：投机扇出 ────────────────────────────────────────
$("#run-fanout").addEventListener("click", (e) =>
  withBusy(e.currentTarget, async () => {
    const data = await post("evaluate", { state: stateBox.value });
    renderFanout(data);
  }));

function renderFanout(data) {
  const stats = $("#fanout-stats");
  stats.replaceChildren(
    tile("问题数", String(data.answers.length)),
    tile("API 调用", String(data.calls)),
    tile("输入 token", String(data.usage.input_tokens)),
    tile("成本", fmtUSD(data.cost)),
    tile("耗时", fmtSec(data.latency)),
    tile("模型", data.model),
  );
  stats.hidden = false;
  $("#fanout-legend").hidden = false;

  // 代码侧路由：判定每个投机问题这次是否被采纳
  const by = Object.fromEntries(data.answers.map((a) => [a.id, a]));
  const kept = {
    "category == config_error": by.category?.value === "config_error",
    "severity >= 2.0": (by.severity?.value ?? 0) >= 2.0,
    "data_loss > 0.5": (by.data_loss?.value ?? 0) > 0.5,
  };

  const out = $("#fanout-out");
  out.replaceChildren();
  for (const a of data.answers) {
    const card = el("article", "card");
    const used = a.speculative ? kept[a.speculative] : true;
    if (!used) card.classList.add("dropped");

    const head = el("div", "card-head");
    head.append(el("span", "qid", a.id), el("span", "qtype", a.type));
    card.append(head);

    if (a.speculative) {
      const chip = el("span", `chip ${used ? "chip-good" : "chip-drop"}`,
        used ? `✓ 已采纳 · ${a.speculative}` : `○ 已丢弃 · 需要 ${a.speculative}`);
      card.append(chip);
    }

    card.append(el("p", "qinstr", a.instructions));

    const headline = a.type === "noul"
      ? a.value.toFixed(2)
      : (a.type === "score" ? a.value.toFixed(2) : a.value);
    card.append(el("div", "headline", String(headline)));

    card.append(bars(a.bars, a.type === "score" ? String(a.top) : a.top));
    if (a.confidence != null) card.append(confMeter(a.confidence));
    else card.append(el("p", "muted", "Noul 不返回 confidence —— 概率本身就是信号"));

    out.append(card);
  }

  renderFanoutTable(data.answers);
  $("#fanout-table-wrap").hidden = false;
}

function renderFanoutTable(answers) {
  const t = $("#fanout-table");
  t.replaceChildren();
  const head = t.insertRow();
  for (const h of ["问题", "类型", "答案", "置信度", "分布"]) {
    const th = document.createElement("th");
    th.textContent = h;
    head.append(th);
  }
  for (const a of answers) {
    const r = t.insertRow();
    r.insertCell().textContent = a.id;
    r.insertCell().textContent = a.type;
    const v = r.insertCell();
    v.className = "num";
    v.textContent = typeof a.value === "number" ? a.value.toFixed(3) : a.value;
    const c = r.insertCell();
    c.className = "num";
    c.textContent = a.confidence == null ? "—" : a.confidence.toFixed(3);
    r.insertCell().textContent =
      a.bars.map((b) => `${b.label}=${b.p.toFixed(2)}`).join("  ");
  }
}

// ── 视图 2：权重实验 ────────────────────────────────────────
$("#run-weights").addEventListener("click", (e) =>
  withBusy(e.currentTarget, async () => {
    const data = await post("dimensions", { state: stateBox.value });
    dims = data;
    weights = Object.fromEntries(data.dimensions.map((d) => [d.id, 1 / data.dimensions.length]));
    renderDims(data);
    renderSliders();
    recompute();                       // 之后拖滑块只走这一条，不再发请求
    $("#weights-body").hidden = false;
    $("#call-count").textContent = String(data.calls);
  }));

function renderDims(data) {
  const out = $("#dims-out");
  out.replaceChildren();
  for (const d of data.dimensions) {
    const card = el("article", "card");
    const head = el("div", "card-head");
    head.append(el("span", "qid", d.id), el("span", "qtype", "score"));
    card.append(head, el("p", "qinstr", d.instructions));
    card.append(el("div", "headline",
      `${d.score.toFixed(2)} / ${d.levels - 1}  →  ${d.normalised.toFixed(3)}`));
    card.append(confMeter(d.confidence));
    out.append(card);
  }
  out.style.gridTemplateColumns = "1fr";
}

function renderSliders() {
  const box = $("#sliders");
  box.replaceChildren();
  for (const d of dims.dimensions) {
    const row = el("div", "slider-row");
    const lab = el("label", "lab");
    lab.htmlFor = "w-" + d.id;
    lab.append(el("span", null, d.id.replace("q_", "")));
    const b = el("b", null, pct(weights[d.id]));
    lab.append(b);

    const input = document.createElement("input");
    input.type = "range";
    input.id = "w-" + d.id;
    input.min = "0"; input.max = "100"; input.step = "1";
    input.value = String(Math.round(weights[d.id] * 100));
    input.addEventListener("input", () => {
      weights[d.id] = Number(input.value) / 100;
      b.textContent = pct(weights[d.id]);
      recompute();                     // 纯前端，零请求
    });

    row.append(lab, input);
    box.append(row);
  }
}

const PRESETS = {
  "工程视角": { q_rootcause: 0.50, q_timeline: 0.20, q_actions: 0.30 },
  "管理视角": { q_rootcause: 0.20, q_timeline: 0.20, q_actions: 0.60 },
  "审计视角": { q_rootcause: 0.30, q_timeline: 0.50, q_actions: 0.20 },
};

function recompute() {
  const sum = Object.values(weights).reduce((a, b) => a + b, 0);
  $("#weight-sum").textContent = sum > 0
    ? `权重合计 ${pct(sum)}（不必等于 100%，下面按合计归一化）`
    : "权重全为 0 —— 把任意一个拉起来";

  const norm = Object.fromEntries(dims.dimensions.map((d) => [d.id, d.normalised]));
  const rows = [];

  const live = sum > 0
    ? dims.dimensions.reduce((acc, d) => acc + (weights[d.id] / sum) * norm[d.id], 0)
    : 0;
  rows.push({ name: "当前滑块", value: live, series: "s1" });

  for (const [name, w] of Object.entries(PRESETS)) {
    const s = Object.values(w).reduce((a, b) => a + b, 0);
    rows.push({
      name,
      value: Object.entries(w).reduce((acc, [k, v]) => acc + (v / s) * (norm[k] ?? 0), 0),
      series: "s2",
    });
  }

  const box = $("#composite");
  box.replaceChildren();
  for (const r of rows) {
    const row = el("div", "comp-row");
    row.append(el("span", "name", r.name));
    const track = el("div", "bar-track");
    track.title = `${r.name} — ${r.value.toFixed(3)}`;
    const fill = el("div", "bar-fill");
    fill.style.inlineSize = Math.max(r.value * 100, 0.5) + "%";
    fill.style.background = `var(--${r.series})`;
    track.append(fill);
    row.append(track, el("span", "val", r.value.toFixed(3)));
    box.append(row);
  }
}

// ── 视图 3：打包 vs 拆开 ────────────────────────────────────
const repeatsInput = $("#repeats");
repeatsInput.addEventListener("input", () => {
  $("#repeats-out").value = repeatsInput.value;
});

$("#run-bench").addEventListener("click", (e) =>
  withBusy(e.currentTarget, async () => {
    const data = await post("bench", {
      state: stateBox.value,
      repeats: Number(repeatsInput.value),
    });
    renderBench(data);
  }));

function barChart(target, rows, format) {
  const max = Math.max(...rows.map((r) => r.value)) || 1;
  const box = $(target);
  box.replaceChildren();
  for (const r of rows) {
    const wrap = el("div", "bc-row");
    const head = el("div", "bc-head");
    const sw = el("span", "bc-swatch");
    sw.style.background = `var(--${r.series})`;
    head.append(sw, el("span", "bc-name", r.name), el("span", "bc-value", format(r.value)));
    const track = el("div", "bc-track");
    track.title = `${r.name} — ${format(r.value)}`;
    const fill = el("div", "bc-fill");
    fill.style.inlineSize = Math.max((r.value / max) * 100, 0.5) + "%";
    fill.style.background = `var(--${r.series})`;
    track.append(fill);
    wrap.append(head, track);
    box.append(wrap);
  }
}

function renderBench(d) {
  const n = d.n_questions;
  const costRatio = d.serial.cost / d.batched.cost;
  const latRatio = d.serial.latency / d.batched.latency;
  $("#bench-out").hidden = false;

  $("#bench-tiles").replaceChildren(
    tile("题数", String(n)),
    tile("重复轮数", String(d.repeats)),
    tile("成本比", costRatio.toFixed(1) + "×", "打包更便宜"),
    tile("速度比", latRatio.toFixed(1) + "×", "打包更快"),
  );

  const label1 = `打包 · 1 次调用`;
  const label2 = `逐题 · ${n} 次调用`;
  barChart("#chart-cost", [
    { name: label1, value: d.batched.cost, series: "s1" },
    { name: label2, value: d.serial.cost, series: "s2" },
  ], fmtUSD);
  barChart("#chart-latency", [
    { name: label1, value: d.batched.latency, series: "s1" },
    { name: label2, value: d.serial.latency, series: "s2" },
  ], fmtSec);

  // 噪声 vs 组间差异 —— 只有前者小于后者，才谈得上「打包改变了答案」
  const box = $("#bench-noise");
  box.replaceChildren();
  const rows = Object.keys(d.between)
    .map((q) => ({ q, between: d.between[q], noise: d.noise[q] }))
    .filter((r) => r.between > 0.001 || r.noise > 0.001)
    .sort((a, b) => b.between - a.between);

  if (!rows.length) {
    box.append(el("p", "notice",
      `${n} 道题在两种方式下答案完全一致。每题都是独立对 state 评分的，` +
      `所以同一请求里有什么别的题，不影响它。`));
    return;
  }

  const note = el("p", "notice");
  note.append(document.createTextNode(
    d.repeats < 2
      ? "只跑了 1 轮，组内噪声无法测量 —— 下面的差异分不清是打包造成的还是运行间波动。把重复轮数调到 2 以上再跑一次。"
      : "组间差异若不超过组内噪声，就不能归因于打包。"));
  box.append(note);

  const details = el("details");
  details.append(el("summary", null, "答案差异明细"));
  const scroll = el("div", "table-scroll");
  const t = document.createElement("table");
  const head = t.insertRow();
  for (const h of ["问题", "组间差异", "组内噪声", "判定"]) {
    const th = document.createElement("th");
    th.textContent = h;
    head.append(th);
  }
  for (const r of rows) {
    const tr = t.insertRow();
    tr.insertCell().textContent = r.q;
    const a = tr.insertCell(); a.className = "num"; a.textContent = r.between.toFixed(3);
    const b = tr.insertCell(); b.className = "num"; b.textContent = r.noise.toFixed(3);
    tr.insertCell().textContent = d.repeats < 2
      ? "无法判定"
      : (r.between > r.noise ? "超出噪声" : "在噪声内");
  }
  scroll.append(t);
  details.append(scroll);
  box.append(details);
}

// ── 启动 ────────────────────────────────────────────────────
(async () => {
  try {
    samples = await post("samples");
    stateBox.value = samples.en;
    updateStateMeta();
  } catch (err) {
    fail(err);
  }
})();

import * as THREE from "three";
import { STLLoader } from "three/addons/loaders/STLLoader.js";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";

const $ = (id) => document.getElementById(id);

// ---------- three.js viewer ----------
const view = $("view");
const scene = new THREE.Scene();
scene.background = new THREE.Color(0x0f151d);
const cam = new THREE.PerspectiveCamera(50, 1, 0.1, 5000);
cam.position.set(120, 120, 120);
const renderer = new THREE.WebGLRenderer({ antialias: true });
view.appendChild(renderer.domElement);
const controls = new OrbitControls(cam, renderer.domElement);
controls.enableDamping = true;
scene.add(new THREE.HemisphereLight(0xffffff, 0x30343c, 1.1));
const key = new THREE.DirectionalLight(0xffffff, 1.2);
key.position.set(1, 1.2, 0.8); scene.add(key);
const fill = new THREE.DirectionalLight(0x88aaff, 0.35);
fill.position.set(-1, 0.6, -1); scene.add(fill);
scene.add(new THREE.GridHelper(200, 20, 0x24303f, 0x18202a));
function size() {
  cam.aspect = view.clientWidth / view.clientHeight;
  cam.updateProjectionMatrix();
  renderer.setSize(view.clientWidth, view.clientHeight);
}
window.addEventListener("resize", size);
size();
(function loop() { requestAnimationFrame(loop); controls.update(); renderer.render(scene, cam); })();

let mesh = null;
const loader = new STLLoader();
function loadStl(url) {
  loader.load(url + "?t=" + Date.now(), (geo) => {
    clearModel();
    geo.center();
    geo.rotateX(-Math.PI / 2); // FreeCAD 是 Z-up,three.js 是 Y-up：讓零件躺在工作平面
    geo.computeBoundingBox();
    const bb = geo.boundingBox;
    mesh = new THREE.Mesh(geo, new THREE.MeshStandardMaterial({
      color: 0x9fb6d4, metalness: 0.15, roughness: 0.5 }));
    mesh.position.y = -bb.min.y; // 貼地
    scene.add(mesh);
    const radius = bb.getSize(new THREE.Vector3()).length() / 2;
    const dist = Math.max(60, radius * 2.6);
    cam.position.normalize().multiplyScalar(dist); // 保留視角方向，依零件大小取景
    controls.target.set(0, -bb.min.y, 0);
  }, undefined, () => {});
}
function clearModel() {
  if (mesh) { scene.remove(mesh); mesh.geometry.dispose(); mesh = null; }
}

// ---------- panel helpers ----------
const log = $("log");
function line(cls, text) {
  const div = document.createElement("div");
  div.className = "entry " + cls;
  div.textContent = text;
  log.appendChild(div);
  log.scrollTop = log.scrollHeight;
  $("examples").hidden = true;
}
function scriptBlock(script) {
  const d = document.createElement("details");
  d.className = "script";
  const s = document.createElement("summary");
  s.textContent = "AI 腳本（點開檢視）";
  const pre = document.createElement("pre");
  pre.textContent = script;
  d.append(s, pre);
  log.appendChild(d);
  log.scrollTop = log.scrollHeight;
}
function setStatus(cls, text) {
  $("status").className = cls;
  $("status").textContent = text;
}
function showActions(on) {
  for (const id of ["dl-stl", "dl-step", "to-render"]) $(id).hidden = !on;
}

// ---------- SSE ----------
const es = new EventSource("/events");
es.onmessage = (e) => {
  const ev = JSON.parse(e.data);
  if (ev.type === "status") {
    if (ev.stage === "thinking") { setStatus("busy", "AI 寫腳本中"); line("s", "AI 寫腳本中…"); }
    else if (ev.stage === "retry") { setStatus("busy", "自我修復中"); line("s", `建置失敗，AI 自我修復中（第 ${ev.attempt} 次）…`); }
    else if (ev.stage === "building") { setStatus("busy", "FreeCAD 建置中"); line("s", "FreeCAD 建置中…"); }
  } else if (ev.type === "script") {
    scriptBlock(ev.script);
  } else if (ev.type === "model") {
    loadStl(ev.stl);
    renderParams(ev.params || []);
    setStatus("ok", "完成");
    if (ev.label) line("s", "模型完成：" + ev.label);
    showActions(true);
    refreshHistory();
  } else if (ev.type === "error") {
    setStatus("err", "失敗");
    line("e", "建置失敗：\n" + (ev.stderr || "").slice(-800));
  }
};

// ---------- session / build ----------
let building = false;
let sessionHasPhoto = false;
function setBusyUI(on) {
  building = on;
  $("go").disabled = on;
  updateRebuildState();
}
async function refreshSession() {
  try {
    const s = await (await fetch("/session")).json();
    sessionHasPhoto = !!s.image;
    $("photoline").hidden = !s.image;
    if (s.image) $("photoline").textContent = "基準照片：" + s.image + "（重設可清除）";
    return s;
  } catch { return null; }
}
async function send() {
  if (building) return;
  const msg = $("msg").value.trim();
  const photo = $("photo").files[0];
  if (!msg && !photo && !sessionHasPhoto) { line("s", "先描述零件，或上傳一張照片。"); return; }
  setBusyUI(true);
  line("u", photo ? "[照片] " + msg : msg);
  $("msg").value = "";
  const fd = new FormData();
  fd.append("message", msg);
  if (photo) fd.append("image", photo);
  try {
    const r = await fetch("/build", { method: "POST", body: fd });
    if (r.status === 409) line("s", "上一個建置還在跑，等它結束再送。");
    else if (!r.ok) { setStatus("err", "失敗"); line("e", "請求失敗：HTTP " + r.status); }
    else { $("photo").value = ""; $("photo-btn").firstChild.textContent = "照片"; }
  } catch (err) {
    setStatus("err", "失敗"); line("e", "連線失敗：" + err);
  } finally {
    setBusyUI(false);
    refreshSession();
  }
}
$("go").addEventListener("click", send);
$("msg").addEventListener("keydown", (e) => { if (e.key === "Enter") send(); });

// ---------- params（尺寸表）----------
let baseline = {};
function renderParams(list) {
  const box = $("param-rows");
  box.textContent = "";
  baseline = {};
  for (const p of list) {
    baseline[p.name] = p.value;
    const row = document.createElement("label");
    row.className = "param";
    const name = document.createElement("span");
    name.textContent = p.name;
    name.title = p.name;
    const input = document.createElement("input");
    input.type = "number"; input.step = "any"; input.inputMode = "decimal";
    input.value = p.value;
    input.dataset.name = p.name;
    input.addEventListener("input", () => {
      const v = parseFloat(input.value);
      input.classList.toggle("dirty", Number.isFinite(v) && v !== baseline[p.name]);
      updateRebuildState();
    });
    const unit = document.createElement("span");
    unit.className = "unit";
    unit.textContent = "mm";
    row.append(name, input, unit);
    box.appendChild(row);
  }
  $("params").hidden = list.length === 0;
  updateRebuildState();
}
function changedParams() {
  const out = {};
  for (const input of $("param-rows").querySelectorAll("input")) {
    const v = parseFloat(input.value);
    if (Number.isFinite(v) && v !== baseline[input.dataset.name]) out[input.dataset.name] = v;
  }
  return out;
}
function updateRebuildState() {
  $("rebuild").disabled = building || Object.keys(changedParams()).length === 0;
}
$("rebuild").addEventListener("click", async () => {
  const changed = changedParams();
  if (building || !Object.keys(changed).length) return;
  setBusyUI(true);
  line("u", "調整參數：" + Object.entries(changed).map(([k, v]) => k + "=" + v).join(", "));
  try {
    const r = await fetch("/rebuild", { method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ params: changed }) });
    if (r.status === 409) line("s", "上一個建置還在跑。");
  } catch (err) { line("e", "連線失敗：" + err); }
  finally { setBusyUI(false); }
});

// ---------- history（版次表）----------
async function refreshHistory() {
  try {
    const list = await (await fetch("/history")).json();
    const box = $("history-rows");
    box.textContent = "";
    for (const e of [...list].reverse()) {
      const row = document.createElement("div");
      row.className = "hrow";
      const rev = document.createElement("span");
      rev.className = "rev";
      rev.textContent = "R" + e.id;
      const label = document.createElement("span");
      label.textContent = e.label;
      label.title = e.label;
      const btn = document.createElement("button");
      btn.className = "btn small";
      btn.textContent = "回復";
      btn.addEventListener("click", () => fetch("/history/" + e.id + "/restore", { method: "POST" }));
      row.append(rev, label, btn);
      box.appendChild(row);
    }
    $("history").hidden = list.length === 0;
  } catch {}
}

// ---------- misc ----------
for (const chip of document.querySelectorAll("#examples .chip")) {
  chip.addEventListener("click", () => { $("msg").value = chip.textContent; $("msg").focus(); });
}
$("photo").addEventListener("change", () => {
  const f = $("photo").files[0];
  $("photo-btn").firstChild.textContent = f ? "照片：" + f.name.slice(0, 12) : "照片";
});
$("reset").addEventListener("click", async () => {
  await fetch("/reset", { method: "POST" });
  $("photo").value = ""; $("msg").value = "";
  $("photo-btn").firstChild.textContent = "照片";
  clearModel();
  renderParams([]);
  showActions(false);
  setStatus("idle", "待命");
  line("s", "已重設，從頭開始。");
  refreshSession();
});
$("to-render").addEventListener("click", () =>
  window.open("http://127.0.0.1:8098/?from=cad-agent", "_blank"));

(async function init() {
  const s = await refreshSession();
  if (s && s.has_model) {
    loadStl("/stl");
    renderParams(s.params || []);
    showActions(true);
  }
  refreshHistory();
})();

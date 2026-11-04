/* Remote Control — operator UI (vanilla JS, no external dependencies). */

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));

/* sessionStorage can throw inside a sandboxed iframe — never break the UI. */
function storageGet(key) {
  try { return sessionStorage.getItem(key); } catch (error) { return null; }
}
function storageSet(key, value) {
  try { sessionStorage.setItem(key, value); } catch (error) { /* ignore */ }
}
function storageRemove(key) {
  try { sessionStorage.removeItem(key); } catch (error) { /* ignore */ }
}

const state = {
  targets: [],
  selectedId: storageGet("rc.selectedId"),
  commands: [],
  stats: { ok: 0, failed: 0, total: 0 },
  events: [],
  server: null,
  tab: "terminal",
  files: { path: "", entries: [] },
  pathInitialized: false,
  quickPlatform: null,
  screenshots: [],
  confirmResolver: null,
  targetKind: "winrm",
  polling: false,
};

/* ------------------------------------------------------------------ utils */
function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (ch) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[ch]));
}

function formatBytes(bytes) {
  if (bytes === null || bytes === undefined) return "—";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

function formatTime(value) {
  if (!value) return "—";
  try {
    return new Date(value).toLocaleString("fa-IR", { hour12: false });
  } catch (error) {
    return String(value);
  }
}

function formatMtime(value) {
  if (!value) return "—";
  if (typeof value === "number") {
    try {
      return new Date(value * 1000).toLocaleString("fa-IR", { hour12: false });
    } catch (error) {
      return "—";
    }
  }
  return formatTime(value);
}

function agoFromIso(value) {
  if (!value) return null;
  const then = new Date(value).getTime();
  if (Number.isNaN(then)) return null;
  return Math.max(0, Math.floor((Date.now() - then) / 1000));
}

function timeAgo(seconds) {
  if (seconds === null || seconds === undefined) return "بدون سابقه";
  if (seconds < 5) return "همین حالا";
  if (seconds < 60) return `${seconds} ثانیه پیش`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)} دقیقه پیش`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)} ساعت پیش`;
  return `${Math.floor(seconds / 86400)} روز پیش`;
}

function platformLabel(platform) {
  return {
    windows: "ویندوز",
    android: "اندروید",
    linux: "لینوکس",
    darwin: "مک",
  }[String(platform || "").toLowerCase()] || String(platform || "نامشخص");
}

function targetKindLabel(kind) {
  return { winrm: "WinRM", ssh: "SSH", adb: "ADB" }[kind] || String(kind || "").toUpperCase();
}

function targetKindIcon(kind) {
  return { winrm: "🪟", ssh: "🔐", adb: "🤖" }[kind] || "💻";
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function toast(message, type = "info") {
  const container = $("#toasts");
  if (!container) return;
  const element = document.createElement("div");
  element.className = `toast ${type}`;
  element.textContent = message;
  container.appendChild(element);
  setTimeout(() => {
    element.style.opacity = "0";
    element.style.transition = "opacity .25s";
    setTimeout(() => element.remove(), 260);
  }, 4600);
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    cache: "no-store",
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...(options.headers || {}),
    },
  });
  let data = null;
  try {
    data = await response.json();
  } catch (error) {
    data = null;
  }
  if (!response.ok) {
    throw new Error((data && data.error) || `خطای HTTP ${response.status}`);
  }
  return data;
}

function selectedTarget() {
  return state.targets.find((target) => target.id === state.selectedId) || null;
}

function currentPlatform() {
  const target = selectedTarget();
  return (target && target.platform) || "windows";
}

function defaultPath(platform) {
  const paths = (state.server && state.server.default_paths) || {};
  return paths[platform] || (platform === "android" ? "/sdcard" : platform === "windows" ? "C:\\" : "~");
}

/* ------------------------------------------------------------------ modals */
function openModal(id) {
  const modal = document.getElementById(id);
  if (modal) modal.classList.remove("hidden");
}

function closeModal(id) {
  const modal = document.getElementById(id);
  if (modal) modal.classList.add("hidden");
}

function confirmAction(message, command) {
  return new Promise((resolve) => {
    state.confirmResolver = resolve;
    $("#confirm-message").textContent = message;
    $("#confirm-command").textContent = command || "";
    openModal("confirm-modal");
  });
}

function resolveConfirm(value) {
  closeModal("confirm-modal");
  const resolver = state.confirmResolver;
  state.confirmResolver = null;
  if (resolver) resolver(value);
}

/* ------------------------------------------------------------------ polling */
async function refreshAll() {
  if (state.polling) return;
  state.polling = true;
  try {
    const [targetsData, serverData] = await Promise.all([
      api("/api/targets"),
      api("/api/server"),
    ]);
    state.server = serverData;
    state.targets = targetsData.targets || [];
    renderServerPill();
    renderTargets();

    if (!state.selectedId || !state.targets.some((target) => target.id === state.selectedId)) {
      state.selectedId = state.targets[0] ? state.targets[0].id : null;
      storageSet("rc.selectedId", state.selectedId || "");
      state.commands = [];
      state.files = { path: "", entries: [] };
      state.pathInitialized = false;
      state.quickPlatform = null;
    }

    renderTarget();
    if (state.selectedId) {
      await refreshCommands();
      if (state.tab === "logs") await refreshLogs("panel");
    }
  } catch (error) {
    $("#server-text").textContent = "سرور در دسترس نیست";
    $("#server-dot").className = "dot off";
  } finally {
    state.polling = false;
  }
}

function renderServerPill() {
  const version = (state.server && state.server.version) || "2.0";
  const adb = state.server && state.server.adb_available;
  $("#server-text").textContent = `سرور محلی • v${version}${adb ? "" : " • adb نیست"}`;
  $("#server-dot").className = "dot on";
}

/* ------------------------------------------------------------------ targets */
function renderTargets() {
  const list = $("#target-list");
  $("#target-count").textContent = String(state.targets.length);

  if (!state.targets.length) {
    list.innerHTML = '<div class="empty-side">هنوز دستگاهی اضافه نشده است.<br>روی «افزودن دستگاه» بزنید.</div>';
    return;
  }

  list.innerHTML = state.targets
    .map((target) => `
      <button class="device ${target.id === state.selectedId ? "active" : ""}" data-id="${escapeHtml(target.id)}" type="button">
        <span class="dev-icon">${targetKindIcon(target.kind)}</span>
        <span class="dev-info">
          <strong>${escapeHtml(target.name || target.host)}</strong>
          <small>${escapeHtml(targetKindLabel(target.kind))} · ${escapeHtml(target.host)}${target.port ? ":" + escapeHtml(target.port) : ""}</small>
        </span>
        <span class="dot ${target.status === "online" ? "on" : "off"}"></span>
      </button>
    `)
    .join("");
}

function renderTarget() {
  const target = selectedTarget();
  const empty = $("#empty-state");
  const view = $("#device-view");

  if (!target) {
    empty.classList.remove("hidden");
    view.classList.add("hidden");
    return;
  }

  empty.classList.add("hidden");
  view.classList.remove("hidden");

  $("#dev-hostname").textContent = target.name || target.host;
  $("#dev-kind").textContent = targetKindLabel(target.kind);
  $("#dev-platform").textContent = platformLabel(target.platform);
  $("#dev-status").textContent =
    target.status === "online" ? "آنلاین" : target.status === "offline" ? "آفلاین" : "تست نشده";
  $("#dev-status").className = `tag ${target.status === "online" ? "online" : target.status === "offline" ? "offline" : "muted"}`;
  $("#status-dot").className = `dot ${target.status === "online" ? "on" : "off"}`;

  const meta = [
    `<span>آدرس: <b>${escapeHtml(target.host)}${target.port ? ":" + escapeHtml(target.port) : ""}</b></span>`,
    `<span>سیستم: <b>${escapeHtml(target.os_name || "—")}</b></span>`,
    `<span>آخرین تست: <b>${escapeHtml(timeAgo(agoFromIso(target.last_test_at)))}</b></span>`,
    `<span>دستورها: <b>${state.stats.ok || 0}</b> موفق / <b>${state.stats.failed || 0}</b> خطا</span>`,
  ];
  $("#dev-meta").innerHTML = meta.join("");

  $("#term-prompt").textContent = currentPlatform() === "windows" ? "PS>" : "$";
  $("#tab-android").classList.toggle("hidden", currentPlatform() !== "android");

  if (state.quickPlatform !== currentPlatform()) {
    renderQuickCommands();
    state.quickPlatform = currentPlatform();
  }
  if (!state.pathInitialized) {
    $("#file-path").value = defaultPath(currentPlatform());
    state.pathInitialized = true;
  }
  $("#clip-note").textContent =
    currentPlatform() === "android"
      ? "خواندن/نوشتن کلیپ‌بورد روی اندروید بدون اپلیکیشن کمکی ممکن نیست؛ از تب اندروید برای تایپ متن استفاده کنید."
      : "";
}

function renderQuickCommands() {
  const commands = (state.server && state.server.quick_commands && state.server.quick_commands[currentPlatform()]) || [];
  $("#quick-row").innerHTML = commands
    .map((item) => `<button class="chip" type="button" data-command="${escapeHtml(item.command)}">${escapeHtml(item.label)}</button>`)
    .join("");
}

/* ------------------------------------------------------------------ tabs */
function switchTab(name) {
  state.tab = name;
  $$("#tabs .tab").forEach((tab) => tab.classList.toggle("active", tab.dataset.tab === name));
  $$(".tab-panel").forEach((panel) => panel.classList.add("hidden"));
  const panel = document.getElementById(`panel-${name}`);
  if (panel) panel.classList.remove("hidden");

  if (name === "logs") refreshLogs("panel");
  if (name === "files" && !state.files.entries.length) {
    listDirectory($("#file-path").value.trim() || defaultPath(currentPlatform()));
  }
}

/* ------------------------------------------------------------------ commands */
async function refreshCommands() {
  if (!state.selectedId) return;
  try {
    const data = await api(`/api/targets/${state.selectedId}/commands?limit=30`);
    state.commands = data.commands || [];
    state.stats = data.stats || state.stats;
    renderCommands();
    renderTarget();
  } catch (error) {
    /* keep the previous list on transient errors */
  }
}

function renderCommands() {
  const output = $("#term-output");
  if (!output) return;
  const nearBottom = output.scrollHeight - output.scrollTop - output.clientHeight < 90;
  const commands = state.commands.filter((command) => command.kind === "shell");

  if (!commands.length) {
    output.innerHTML = `
      <div class="term-hint">
        <p>دستور مورد نظر را بنویسید و Enter بزنید.</p>
        <p class="muted">دستور مستقیماً روی دستگاه هدف اجرا می‌شود.</p>
      </div>`;
    return;
  }

  output.innerHTML = commands.map(commandCard).join("");
  if (nearBottom) output.scrollTop = output.scrollHeight;
}

function commandCard(command) {
  const failed = Boolean(command.error) || (command.exit_code !== null && command.exit_code !== 0);
  const duration = command.duration_ms ? `${(command.duration_ms / 1000).toFixed(1)}s` : "";
  const meta = [formatTime(command.created_at), duration].filter(Boolean).join(" • ");
  return `
    <div class="cmd">
      <div class="cmd-head">
        <code>${escapeHtml(command.input || "")}</code>
        <span class="status ${failed ? "bad" : "ok"}">${failed ? "خطا" : "انجام شد"}</span>
        <span class="cmd-meta">${escapeHtml(meta)}</span>
      </div>
      ${command.output ? `<pre class="cmd-result">${escapeHtml(command.output)}</pre>` : ""}
      ${command.error ? `<pre class="cmd-result err">${escapeHtml(command.error)}</pre>` : ""}
    </div>`;
}

async function runShell(command) {
  const target = selectedTarget();
  if (!target) throw new Error("دستگاهی انتخاب نشده است");
  const data = await api(`/api/targets/${target.id}/exec`, {
    method: "POST",
    body: JSON.stringify({ command, timeout: 30 }),
  });
  await refreshCommands();
  const output = $("#term-output");
  output.scrollTop = output.scrollHeight;
  return data;
}

/* ------------------------------------------------------------------ files */
async function listDirectory(path) {
  const target = selectedTarget();
  if (!target) return;
  try {
    const data = await api(`/api/targets/${target.id}/files`, {
      method: "POST",
      body: JSON.stringify({ action: "list", path }),
    });
    state.files = { path: data.path || path, entries: data.entries || [] };
    $("#file-path").value = state.files.path;
    renderFiles();
  } catch (error) {
    toast(error.message, "error");
  }
}

function renderFiles() {
  const container = $("#file-list");
  const entries = state.files.entries || [];
  if (!entries.length) {
    container.innerHTML = '<div class="placeholder">پوشه خالی است یا خطایی رخ داده است.</div>';
    return;
  }

  const rows = entries
    .map((entry) => {
      const action = entry.is_dir
        ? `<button class="mini" type="button" data-dir="${escapeHtml(entry.path)}">باز کردن</button>`
        : `<button class="mini" type="button" data-download="${escapeHtml(entry.path)}">دانلود</button>`;
      return `
        <div class="file-row">
          <span class="file-ico">${entry.is_dir ? "📁" : "📄"}</span>
          <span class="file-name ${entry.is_dir ? "dir" : ""}" ${entry.is_dir ? `data-dir="${escapeHtml(entry.path)}" role="button"` : ""}>${escapeHtml(entry.name)}</span>
          <span class="file-size">${entry.is_dir ? "—" : formatBytes(entry.size)}</span>
          <span class="file-time">${escapeHtml(formatMtime(entry.mtime))}</span>
          <span class="file-action">${action}</span>
        </div>`;
    })
    .join("");

  container.innerHTML = `
    <div class="file-row head">
      <span></span><span>نام</span><span>حجم</span><span>تغییر</span><span></span>
    </div>
    ${rows}`;
}

function saveBase64(base64, filename) {
  const binary = atob(base64);
  const bytes = new Uint8Array(binary.length);
  for (let index = 0; index < binary.length; index += 1) {
    bytes[index] = binary.charCodeAt(index);
  }
  const blob = new Blob([bytes], { type: "application/octet-stream" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename || "download.bin";
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 15000);
}

async function downloadFile(path) {
  const target = selectedTarget();
  if (!target) return;
  try {
    const data = await api(`/api/targets/${target.id}/files`, {
      method: "POST",
      body: JSON.stringify({ action: "download", path }),
    });
    saveBase64(data.content_base64, data.name || "download.bin");
    toast(`فایل «${data.name}» دانلود شد`, "success");
  } catch (error) {
    toast(error.message, "error");
  }
}

function readFileAsBase64(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      const result = String(reader.result || "");
      const comma = result.indexOf(",");
      resolve(comma >= 0 ? result.slice(comma + 1) : result);
    };
    reader.onerror = () => reject(new Error("خواندن فایل ممکن نشد"));
    reader.readAsDataURL(file);
  });
}

async function uploadFile(file) {
  const target = selectedTarget();
  if (!target) return;
  try {
    const base64 = await readFileAsBase64(file);
    let base = $("#file-path").value.trim();
    let targetPath = file.name;
    if (base) {
      const isDirectory =
        /[\\/]$/.test(base) ||
        (state.files.entries || []).some((entry) => entry.is_dir && entry.path === base);
      if (isDirectory) targetPath = `${base.replace(/[\\/]+$/, "")}/${file.name}`;
      else if (base.includes(".") && !base.endsWith(file.name)) targetPath = base;
    }
    await api(`/api/targets/${target.id}/files`, {
      method: "POST",
      body: JSON.stringify({ action: "upload", path: targetPath, content_b64: base64 }),
    });
    toast(`فایل در «${targetPath}» ذخیره شد`, "success");
    const parts = targetPath.split(/[\\/]/);
    parts.pop();
    await listDirectory(parts.join("/") || ".");
  } catch (error) {
    toast(error.message, "error");
  }
}

/* ------------------------------------------------------------------ screen */
function showScreenshot(base64) {
  const url = `data:image/png;base64,${base64}`;
  const time = new Date().toLocaleTimeString("fa-IR", { hour12: false });
  state.screenshots.unshift({ url, time });
  state.screenshots = state.screenshots.slice(0, 12);
  $("#screen-img").src = url;
  $("#screen-img").classList.remove("hidden");
  $("#screen-placeholder").classList.add("hidden");
  $("#shot-info").textContent = `آخرین تصویر: ${time}`;
  renderGallery();
}

function renderGallery() {
  const gallery = $("#gallery");
  if (!state.screenshots.length) {
    gallery.innerHTML = "";
    return;
  }
  gallery.innerHTML = state.screenshots
    .map(
      (shot, index) => `
      <button type="button" data-shot="${index}" title="${escapeHtml(shot.time)}">
        <img src="${shot.url}" alt="تصویر ${escapeHtml(shot.time)}">
      </button>`
    )
    .join("");
}

async function captureScreenshot() {
  const target = selectedTarget();
  if (!target) return;
  const button = $("#btn-shot");
  button.disabled = true;
  try {
    const data = await api(`/api/targets/${target.id}/screenshot`, {
      method: "POST",
      body: JSON.stringify({}),
    });
    showScreenshot(data.image_base64);
    switchTab("screen");
    toast("تصویر صفحه گرفته شد", "success");
  } catch (error) {
    toast(error.message, "error");
  } finally {
    button.disabled = false;
  }
}

/* ------------------------------------------------------------------ clipboard */
async function getClipboard() {
  const target = selectedTarget();
  if (!target) return;
  try {
    const data = await api(`/api/targets/${target.id}/clipboard`, {
      method: "POST",
      body: JSON.stringify({ action: "get" }),
    });
    $("#clip-out").textContent = data.text || "(خالی)";
    toast("کلیپ‌بورد خوانده شد", "success");
  } catch (error) {
    toast(error.message, "error");
  }
}

async function setClipboard() {
  const target = selectedTarget();
  if (!target) return;
  const text = $("#clip-in").value;
  if (!text) {
    toast("متنی برای نوشتن وجود ندارد", "error");
    return;
  }
  try {
    await api(`/api/targets/${target.id}/clipboard`, {
      method: "POST",
      body: JSON.stringify({ action: "set", text }),
    });
    toast("در کلیپ‌بورد دستگاه کپی شد", "success");
  } catch (error) {
    toast(error.message, "error");
  }
}

/* ------------------------------------------------------------------ android */
async function androidAction(action, payload) {
  const target = selectedTarget();
  if (!target) return;
  try {
    const data = await api(`/api/targets/${target.id}/android`, {
      method: "POST",
      body: JSON.stringify({ action, ...payload }),
    });
    toast(data.output || "انجام شد", "success");
  } catch (error) {
    toast(error.message, "error");
  }
}

async function androidShell(command) {
  try {
    const data = await runShell(command);
    const firstLine = (data.stdout || data.stderr || "")
      .split("\n")
      .filter(Boolean)
      .slice(0, 3)
      .join(" · ");
    toast(firstLine || "انجام شد", "success");
  } catch (error) {
    toast(error.message, "error");
  }
}

/* ------------------------------------------------------------------ power */
async function powerAction(action) {
  const target = selectedTarget();
  if (!target) return;
  const labels = {
    lock: "قفل کردن صفحه",
    sleep: "خواباندن دستگاه",
    restart: "ریست کردن دستگاه",
    shutdown: "خاموش کردن دستگاه",
  };
  const confirmed = await confirmAction(
    `آیا می‌خواهید دستگاه «${target.name || target.host}» را ${labels[action] || action} کنید؟ این دستور بلافاصله اجرا می‌شود.`,
    action.toUpperCase()
  );
  if (!confirmed) return;
  try {
    await api(`/api/targets/${target.id}/power`, {
      method: "POST",
      body: JSON.stringify({ action }),
    });
    toast("دستور به دستگاه ارسال شد", "info");
    await sleep(700);
    await refreshCommands();
  } catch (error) {
    toast(error.message, "error");
  }
}

/* ------------------------------------------------------------------ target form */
function openTargetModal() {
  $("#t-name").value = "";
  $("#t-host").value = "";
  $("#t-port").value = "";
  $("#t-username").value = "";
  $("#t-password").value = "";
  $("#t-domain").value = "";
  $("#t-key").value = "";
  $("#t-platform").value = "windows";
  $("#t-https").checked = false;
  $("#t-insecure").checked = false;
  $("#t-pair-address").value = "";
  $("#t-pair-code").value = "";
  const result = $("#test-result");
  result.className = "test-result hidden";
  result.textContent = "";
  $("#discover-results").classList.add("hidden");
  $("#discover-results").innerHTML = "";
  $("#discover-results").dataset.devices = "[]";
  $("#discover-status").textContent = "بدون نیاز به تایپ IP؛ دستگاه‌های دارای WinRM/SSH/ADB را پیدا می‌کند.";
  state.targetKind = "winrm";
  renderTargetForm();
  openModal("target-modal");
}

async function discoverNetwork() {
  const button = $("#btn-discover");
  const status = $("#discover-status");
  const results = $("#discover-results");
  button.disabled = true;
  button.innerHTML = '<span class="spinner"></span> در حال جستجو…';
  status.textContent = "در حال پویش شبکه محلی…";
  results.classList.add("hidden");
  try {
    const data = await api("/api/discover", { method: "POST", body: JSON.stringify({}) });
    renderDiscoverResults(data);
    status.textContent = `${data.devices.length} دستگاه پیدا شد • شبکه ${data.subnet || "نامشخص"}`;
  } catch (error) {
    status.textContent = error.message;
    toast(error.message, "error");
  } finally {
    button.disabled = false;
    button.textContent = "جستجوی خودکار در شبکه";
  }
}

function renderDiscoverResults(data) {
  const results = $("#discover-results");
  results.dataset.devices = JSON.stringify(data.devices || []);
  if (!data.devices || !data.devices.length) {
    results.innerHTML = '<div class="placeholder">دستگاهی با سرویس WinRM/SSH/ADB پیدا نشد. مطمئن شوید دستگاه هدف به همین شبکه وصل است و سرویس مربوطه فعال است.</div>';
    results.classList.remove("hidden");
    return;
  }
  results.innerHTML = data.devices
    .map(
      (device, index) => `
      <button class="discover-item" type="button" data-discover-index="${index}">
        <span class="dev-icon">${targetKindIcon(device.kind)}</span>
        <span class="dev-info">
          <strong>${escapeHtml(device.hostname || device.ip)}</strong>
          <small>${escapeHtml(device.ip)}${device.ports && device.ports.length ? " • پورت " + device.ports.map((port) => escapeHtml(port)).join(", ") : ""}</small>
        </span>
        <span class="tag">${escapeHtml(targetKindLabel(device.kind))}</span>
      </button>`
    )
    .join("");
  results.classList.remove("hidden");
}

function applyDiscoveredDevice(index) {
  const results = $("#discover-results");
  let devices = [];
  try {
    devices = JSON.parse(results.dataset.devices || "[]");
  } catch (error) {
    devices = [];
  }
  const device = devices[index];
  if (!device) return;
  state.targetKind = device.kind || "ssh";
  renderTargetForm();
  $("#t-host").value = device.ip;
  if (device.kind === "winrm") {
    $("#t-port").value = device.ports && device.ports.includes(5985) ? "5985" : "5986";
    $("#t-https").checked = $("#t-port").value === "5986";
  } else if (device.kind === "ssh") {
    $("#t-port").value = "22";
  }
  toast("دستگاه در فرم قرار گرفت؛ مشخصات ورود را وارد کنید.", "info");
}

function renderTargetForm() {
  $$("#type-grid .type-card").forEach((card) => {
    card.classList.toggle("active", card.dataset.kind === state.targetKind);
  });
  const kind = state.targetKind;
  const show = (selector, visible) => {
    const element = $(selector);
    if (element) element.classList.toggle("hidden", !visible);
  };

  show("#port-field", kind !== "adb");
  show("#username-field", kind !== "adb");
  show("#password-field", kind !== "adb");
  show("#domain-field", kind === "winrm");
  show("#key-field", kind === "ssh");
  show("#platform-field", kind === "ssh");
  show("#winrm-checks", kind === "winrm");
  show("#adb-fields", kind === "adb");

  $("#t-host-label").textContent = kind === "adb" ? "آدرس اتصال (IP:port)" : "آدرس / IP";
  $("#t-host").placeholder = kind === "adb" ? "192.168.1.50:40123" : "192.168.1.50";
  $("#t-port").placeholder = kind === "winrm" ? "5985" : "22";
  $("#t-port").value = kind === "winrm" ? "5985" : kind === "ssh" ? "22" : "";

  renderSetupHelp();
}

function renderSetupHelp() {
  const help = $("#setup-help");
  const adbMissing = state.server && state.server.adb_available === false;
  if (state.targetKind === "winrm") {
    help.innerHTML = `
      <h3>فعال‌سازی یک‌بار روی ویندوز</h3>
      <p>در یک PowerShell با دسترسی مدیر روی دستگاه هدف اجرا کنید:</p>
      <pre class="code-block">Enable-PSRemoting -Force -SkipNetworkProfileCheck
Set-NetConnectionProfile -InterfaceAlias "Wi-Fi" -NetworkCategory Private</pre>
      <ul>
        <li>با یک حساب مدیر وارد شوید؛ رمز همان حساب را در فرم بزنید.</li>
        <li>پورت پیش‌فرض 5985 است. برای HTTPS از 5986 و گواهی معتبر استفاده کنید.</li>
        <li>برای انتقال فایل، File and Printer Sharing (SMB) را فعال کنید.</li>
      </ul>`;
  } else if (state.targetKind === "ssh") {
    help.innerHTML = `
      <h3>فعال‌سازی OpenSSH Server روی ویندوز</h3>
      <p>در PowerShell مدیر:</p>
      <pre class="code-block">Add-WindowsCapability -Online -Name OpenSSH.Server~~~~0.0.1.0
Start-Service sshd
Set-Service -Name sshd -StartupType Automatic</pre>
      <p>برای پوسته‌ی پیش‌فرض PowerShell:</p>
      <pre class="code-block">New-ItemProperty -Path "HKLM:\\SOFTWARE\\OpenSSH" -Name DefaultShell -Value "C:\\Windows\\System32\\WindowsPowerShell\\v1.0\\powershell.exe" -PropertyType String -Force</pre>
      <ul>
        <li>اگر فایل کلید می‌دهید، مسیر آن روی همین کامپیوتر است.</li>
        <li>روی لینوکس معمولاً openssh-server از قبل نصب است.</li>
      </ul>`;
  } else {
    help.innerHTML = `
      <h3>فعال‌سازی Wireless debugging روی اندروید</h3>
      <ul>
        <li>Settings → About phone → Build number را ۷ بار بزنید.</li>
        <li>Developer options → Wireless debugging را فعال کنید.</li>
        <li>«Pair device with pairing code» را بزنید و آدرس و کد را بردارید.</li>
        <li>سپس در همین کامپیوتر:</li>
      </ul>
      <pre class="code-block">adb pair 192.168.1.50:37123
adb connect 192.168.1.50:40123</pre>
      <p class="muted small">adb باید روی همین کامپیوتر نصب باشد (Android Platform Tools).</p>
      ${adbMissing ? '<p class="test-result err">adb روی این کامپیوتر پیدا نشد. Platform Tools را نصب کنید و در PATH قرار دهید.</p>' : ""}`;
  }
}

function readTargetForm() {
  return {
    name: $("#t-name").value.trim(),
    kind: state.targetKind,
    host: $("#t-host").value.trim(),
    port: $("#t-port").value ? Number($("#t-port").value) : null,
    username: $("#t-username").value.trim(),
    password: $("#t-password").value,
    domain: $("#t-domain").value.trim(),
    key_path: $("#t-key").value.trim(),
    platform: $("#t-platform").value,
    use_https: $("#t-https").checked,
    insecure_tls: $("#t-insecure").checked,
    pair_address: $("#t-pair-address").value.trim(),
    pair_code: $("#t-pair-code").value.trim(),
  };
}

function showTestResult(ok, title, message) {
  const box = $("#test-result");
  box.className = `test-result ${ok ? "ok" : "err"}`;
  box.innerHTML = `<strong>${escapeHtml(title)}</strong>${escapeHtml(message)}`;
}

function validateForm(body) {
  if (!body.host) return "آدرس دستگاه لازم است";
  if (body.kind !== "adb" && !body.username) return "نام کاربری لازم است";
  if (body.kind !== "adb" && !body.password && !body.key_path) return "رمز عبور یا فایل کلید لازم است";
  return null;
}

async function testNewTarget() {
  const body = readTargetForm();
  const problem = validateForm(body);
  if (problem) return toast(problem, "error");

  const button = $("#btn-test-new-target");
  button.disabled = true;
  button.innerHTML = '<span class="spinner"></span> در حال تست…';
  try {
    const data = await api("/api/targets/test", {
      method: "POST",
      body: JSON.stringify(body),
    });
    const smb = data.smb === undefined ? "" : ` • SMB: ${data.smb ? "فعال" : "غیرفعال"}`;
    showTestResult(true, "اتصال برقرار شد", `${data.hostname || ""} • ${data.os_name || ""}${smb}`);
  } catch (error) {
    showTestResult(false, "اتصال ممکن نشد", error.message);
  } finally {
    button.disabled = false;
    button.textContent = "تست اتصال";
  }
}

async function saveTarget() {
  const body = readTargetForm();
  const problem = validateForm(body);
  if (problem) return toast(problem, "error");

  const button = $("#btn-save-target");
  button.disabled = true;
  try {
    const data = await api("/api/targets", {
      method: "POST",
      body: JSON.stringify(body),
    });
    closeModal("target-modal");
    toast("دستگاه ذخیره شد", "success");
    state.selectedId = data.target.id;
    storageSet("rc.selectedId", data.target.id);
    state.commands = [];
    state.files = { path: "", entries: [] };
    state.pathInitialized = false;
    state.quickPlatform = null;
    await refreshAll();
    await testTargetById(data.target.id);
  } catch (error) {
    toast(error.message, "error");
  } finally {
    button.disabled = false;
  }
}

async function pairAdb() {
  const address = $("#t-pair-address").value.trim();
  const code = $("#t-pair-code").value.trim();
  if (!address || !code) return toast("آدرس جفت‌سازی و کد لازم است", "error");
  try {
    const data = await api("/api/adb/pair", {
      method: "POST",
      body: JSON.stringify({ address, code }),
    });
    toast(data.output || "جفت‌سازی انجام شد", "success");
  } catch (error) {
    toast(error.message, "error");
  }
}

async function testTargetById(id) {
  const target = state.targets.find((item) => item.id === id) || selectedTarget();
  if (!target) return;
  const button = $("#btn-test-target");
  if (button) button.disabled = true;
  try {
    const data = await api(`/api/targets/${target.id}/test`, {
      method: "POST",
      body: JSON.stringify({}),
    });
    toast(`اتصال برقرار شد: ${data.os_name || data.hostname || ""}`, "success");
  } catch (error) {
    toast(error.message, "error");
  } finally {
    if (button) button.disabled = false;
    await refreshAll();
  }
}

async function removeTarget() {
  const target = selectedTarget();
  if (!target) return;
  const confirmed = await confirmAction(
    `دستگاه «${target.name || target.host}» حذف شود؟ اطلاعات اتصال و تاریخچه‌ی دستورهای آن پاک می‌شود.`,
    "REMOVE TARGET"
  );
  if (!confirmed) return;
  try {
    await api(`/api/targets/${target.id}`, { method: "DELETE" });
    state.selectedId = null;
    storageRemove("rc.selectedId");
    state.commands = [];
    toast("دستگاه حذف شد", "success");
    await refreshAll();
  } catch (error) {
    toast(error.message, "error");
  }
}

/* ------------------------------------------------------------------ logs */
function logItemsHtml(events) {
  if (!events.length) return '<div class="placeholder">گزارشی ثبت نشده است.</div>';
  return events
    .map(
      (event) => `
      <div class="log-item">
        <span class="log-time">${escapeHtml(formatTime(event.created_at))}</span>
        <span class="log-level ${escapeHtml(event.level)}">${escapeHtml(event.level)}</span>
        <span class="log-msg">${escapeHtml(event.message)}</span>
      </div>`
    )
    .join("");
}

async function refreshLogs(target = "panel") {
  try {
    const data = await api("/api/events?limit=120");
    state.events = data.events || [];
    const html = logItemsHtml(state.events);
    if (target === "panel") $("#log-list").innerHTML = html;
    if (target === "global") $("#global-log-list").innerHTML = html;
  } catch (error) {
    toast(error.message, "error");
  }
}

/* ------------------------------------------------------------------ wiring */
function bindEvents() {
  $("#btn-add-target").addEventListener("click", openTargetModal);
  $("#btn-add-target-empty").addEventListener("click", openTargetModal);
  $("#btn-test-new-target").addEventListener("click", testNewTarget);
  $("#btn-save-target").addEventListener("click", saveTarget);
  $("#btn-adb-pair").addEventListener("click", pairAdb);
  $("#btn-discover").addEventListener("click", discoverNetwork);
  $("#btn-discover-empty").addEventListener("click", async () => {
    openTargetModal();
    await discoverNetwork();
  });
  $("#discover-results").addEventListener("click", (event) => {
    const button = event.target.closest("[data-discover-index]");
    if (button) applyDiscoveredDevice(Number(button.dataset.discoverIndex));
  });

  $$("#type-grid .type-card").forEach((card) => {
    card.addEventListener("click", () => {
      state.targetKind = card.dataset.kind;
      renderTargetForm();
    });
  });

  $$("[data-close]").forEach((button) => {
    button.addEventListener("click", () => {
      const id = button.dataset.close;
      if (id === "confirm-modal") resolveConfirm(false);
      closeModal(id);
    });
  });

  $$(".modal").forEach((modal) => {
    modal.addEventListener("click", (event) => {
      if (event.target === modal) {
        if (modal.id === "confirm-modal") resolveConfirm(false);
        closeModal(modal.id);
      }
    });
  });

  $("#confirm-ok").addEventListener("click", () => resolveConfirm(true));

  $("#target-list").addEventListener("click", (event) => {
    const button = event.target.closest(".device");
    if (!button) return;
    state.selectedId = button.dataset.id;
    storageSet("rc.selectedId", state.selectedId);
    state.commands = [];
    state.files = { path: "", entries: [] };
    state.pathInitialized = false;
    state.quickPlatform = null;
    renderTargets();
    renderTarget();
    switchTab("terminal");
    refreshCommands();
  });

  $("#tabs").addEventListener("click", (event) => {
    const tab = event.target.closest(".tab");
    if (tab) switchTab(tab.dataset.tab);
  });

  $("#quick-row").addEventListener("click", (event) => {
    const chip = event.target.closest(".chip");
    if (!chip) return;
    $("#term-input").value = chip.dataset.command;
    $("#term-input").focus();
  });

  $("#term-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const input = $("#term-input");
    const command = input.value.trim();
    if (!command) return;
    input.value = "";
    try {
      await runShell(command);
    } catch (error) {
      toast(error.message, "error");
      await refreshCommands();
    }
  });

  $("#btn-shot").addEventListener("click", captureScreenshot);
  $("#gallery").addEventListener("click", (event) => {
    const button = event.target.closest("[data-shot]");
    if (!button) return;
    const shot = state.screenshots[Number(button.dataset.shot)];
    if (!shot) return;
    $("#screen-img").src = shot.url;
    $("#screen-img").classList.remove("hidden");
    $("#screen-placeholder").classList.add("hidden");
    $("#shot-info").textContent = `آخرین تصویر: ${shot.time}`;
  });

  $("#btn-list-dir").addEventListener("click", () => listDirectory($("#file-path").value.trim()));
  $("#file-path").addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      listDirectory($("#file-path").value.trim());
    }
  });
  $("#file-list").addEventListener("click", (event) => {
    const dir = event.target.closest("[data-dir]");
    if (dir) {
      listDirectory(dir.dataset.dir);
      return;
    }
    const download = event.target.closest("[data-download]");
    if (download) downloadFile(download.dataset.download);
  });
  $("#btn-upload").addEventListener("click", () => $("#file-input").click());
  $("#file-input").addEventListener("change", async (event) => {
    const file = event.target.files && event.target.files[0];
    event.target.value = "";
    if (file) await uploadFile(file);
  });

  $("#btn-clip-get").addEventListener("click", getClipboard);
  $("#btn-clip-set").addEventListener("click", setClipboard);

  $("#btn-tap").addEventListener("click", () => {
    const x = $("#tap-x").value;
    const y = $("#tap-y").value;
    if (x === "" || y === "") return toast("مقدار X و Y لازم است", "error");
    androidAction("tap", { x: Number(x), y: Number(y) });
  });
  $("#btn-swipe").addEventListener("click", () => {
    const values = ["sw-x1", "sw-y1", "sw-x2", "sw-y2"].map((id) => $("#" + id).value);
    if (values.some((value) => value === "")) return toast("هر چهار مقدار لازم است", "error");
    androidAction("swipe", {
      x1: Number(values[0]),
      y1: Number(values[1]),
      x2: Number(values[2]),
      y2: Number(values[3]),
    });
  });
  $("#btn-android-text").addEventListener("click", () => {
    const text = $("#android-text").value;
    if (!text) return toast("متنی وارد نشده است", "error");
    androidAction("text", { text });
  });
  $("#btn-list-apps").addEventListener("click", () => androidShell("pm list packages -3"));
  $("#btn-launch-app").addEventListener("click", () => {
    const pkg = $("#app-pkg").value.trim();
    if (!pkg) return toast("نام پکیج لازم است", "error");
    androidShell(`monkey -p ${pkg} -c android.intent.category.LAUNCHER 1`);
  });

  $$("[data-power]").forEach((button) => {
    button.addEventListener("click", () => powerAction(button.dataset.power));
  });

  $("#btn-remove-target").addEventListener("click", removeTarget);
  $("#btn-test-target").addEventListener("click", () => {
    if (state.selectedId) testTargetById(state.selectedId);
  });

  $("#btn-logs-top").addEventListener("click", async () => {
    openModal("logs-modal");
    await refreshLogs("global");
  });
  $("#btn-refresh-logs").addEventListener("click", () => refreshLogs("panel"));
  $("#btn-refresh-global-logs").addEventListener("click", () => refreshLogs("global"));

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      $$(".modal").forEach((modal) => {
        if (!modal.classList.contains("hidden")) {
          if (modal.id === "confirm-modal") resolveConfirm(false);
          closeModal(modal.id);
        }
      });
    }
  });

  setInterval(() => {
    if (document.hidden) return;
    refreshAll();
  }, 5000);

  setInterval(() => {
    const auto = $("#auto-shot");
    if (!auto || !auto.checked) return;
    if (state.tab !== "screen" || document.hidden) return;
    if (!state.screenshots.length) return;
    captureScreenshot();
  }, 10000);
}

function init() {
  bindEvents();
  renderTargetForm();
  refreshAll();
}

init();

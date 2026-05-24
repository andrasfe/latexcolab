// LaTeX Colab frontend.

const $ = (sel) => document.querySelector(sel);

const editorEl = $("#editor");
const editorBinaryEl = $("#editor-binary");
const statusEl = $("#status");
const currentPathEl = $("#current-path");
const pdfFrame = $("#pdf-frame");
const pdfEmpty = $("#pdf-empty");
const treeEl = $("#tree");
const msgLog = $("#msg-log");
const gitLog = $("#git-log");
const cliInput = $("#cli-input");
const cliForm = $("#cli-form");

// CodeMirror mode by extension. Fallback = null (plain text).
const MODES = {
  tex: "stex", bib: "stex", cls: "stex", sty: "stex",
  md: "markdown",
  py: "python",
  js: "javascript", json: { name: "javascript", json: true }, ts: "javascript",
  yml: "yaml", yaml: "yaml",
  html: "htmlmixed", css: "css",
};

const cm = CodeMirror.fromTextArea(editorEl, {
  mode: "stex",
  theme: "material-darker",
  lineNumbers: true,
  lineWrapping: true,
  indentUnit: 2,
  tabSize: 2,
  extraKeys: {
    "Cmd-S": () => saveFile(),
    "Ctrl-S": () => saveFile(),
    "Cmd-Enter": () => generate(),
    "Ctrl-Enter": () => generate(),
  },
});

let currentPath = "main.tex";
let isBinary = false;
let saveTimer = null;
let dirty = false;
let suppressChange = false;

function setStatus(msg, kind = "") {
  statusEl.textContent = msg;
  statusEl.className = "status " + kind;
}

function modeForPath(path) {
  const ext = path.toLowerCase().split(".").pop();
  return MODES[ext] || null;
}

// ---------- Logging ----------

function escapeHTML(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function logTo(panel, parts) {
  const div = document.createElement("div");
  div.className = "entry";
  div.innerHTML = parts
    .filter((p) => p && p.text)
    .map((p) => `<div class="${p.cls}">${escapeHTML(p.text)}</div>`)
    .join("");
  panel.appendChild(div);
  panel.scrollTop = panel.scrollHeight;
}

const msg = (kind, text) => logTo(msgLog, [{ cls: kind, text }]);

$("#msg-clear").addEventListener("click", () => (msgLog.innerHTML = ""));
$("#git-clear").addEventListener("click", () => (gitLog.innerHTML = ""));

// ---------- File tree ----------

async function loadTree() {
  const r = await fetch("/api/tree");
  const j = await r.json();
  treeEl.innerHTML = "";
  treeEl.appendChild(renderTree(j.children, 0));
  highlightSelected();
}

function renderTree(nodes, depth) {
  const ul = document.createElement("ul");
  for (const node of nodes) {
    const li = document.createElement("li");
    const row = document.createElement("div");
    row.className = "node";
    row.dataset.path = node.path;
    row.dataset.type = node.type;
    if (node.type === "dir") {
      row.innerHTML = `<span class="twist">▾</span><span class="icon">📁</span><span class="name">${escapeHTML(node.name)}</span>`;
      row.addEventListener("click", () => {
        li.classList.toggle("collapsed");
        row.querySelector(".twist").textContent = li.classList.contains("collapsed") ? "▸" : "▾";
      });
      li.appendChild(row);
      if (node.children && node.children.length) {
        li.appendChild(renderTree(node.children, depth + 1));
      }
    } else {
      const icon = node.name.endsWith(".pdf") ? "📄" : node.name.endsWith(".tex") ? "📜" : "📃";
      row.innerHTML = `<span class="twist"></span><span class="icon">${icon}</span><span class="name">${escapeHTML(node.name)}</span>`;
      row.addEventListener("click", () => openFile(node.path));
      li.appendChild(row);
    }
    ul.appendChild(li);
  }
  return ul;
}

function highlightSelected() {
  treeEl.querySelectorAll(".node").forEach((n) => n.classList.remove("selected"));
  const sel = treeEl.querySelector(`.node[data-path="${CSS.escape(currentPath)}"]`);
  if (sel) sel.classList.add("selected");
}

// ---------- Open / save ----------

async function openFile(path) {
  if (dirty && !confirm(`Discard unsaved changes to ${currentPath}?`)) return;
  clearTimeout(saveTimer);
  const r = await fetch("/api/file?path=" + encodeURIComponent(path));
  if (!r.ok) {
    if (r.status !== 404) msg("err", `open failed: ${path} (${r.status})`);
    throw new Error(`open ${path}: ${r.status}`);
  }
  try {
    const j = await r.json();
    currentPath = j.path;
    isBinary = !!j.binary;
    currentPathEl.textContent = currentPath;
    if (isBinary) {
      cm.getWrapperElement().style.display = "none";
      editorBinaryEl.classList.remove("hidden");
    } else {
      cm.getWrapperElement().style.display = "";
      editorBinaryEl.classList.add("hidden");
      const mode = modeForPath(currentPath);
      cm.setOption("mode", mode);
      suppressChange = true;
      cm.setValue(j.content || "");
      cm.markClean();
      suppressChange = false;
    }
    dirty = false;
    setStatus("opened", "ok");
    highlightSelected();
  } catch (e) {
    msg("err", `open error: ${e}`);
  }
}

async function saveFile() {
  if (isBinary) return false;
  const r = await fetch("/api/file", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path: currentPath, content: cm.getValue() }),
  });
  if (!r.ok) {
    setStatus("save failed", "err");
    msg("err", `save failed: ${currentPath}`);
    return false;
  }
  cm.markClean();
  dirty = false;
  setStatus("saved", "ok");
  return true;
}

cm.on("change", () => {
  if (suppressChange || isBinary) return;
  dirty = true;
  setStatus("editing…");
  clearTimeout(saveTimer);
  saveTimer = setTimeout(saveFile, 800);
});

// ---------- Compile ----------

async function generate() {
  const btn = $("#generate-btn");
  btn.disabled = true;
  setStatus("saving…");
  if (dirty && !isBinary) await saveFile();
  setStatus("compiling…");
  msg("meta", `$ compile (target: main.tex)`);
  try {
    const r = await fetch("/api/compile", { method: "POST" });
    const j = await r.json();
    if (j.ok) {
      setStatus("compiled ✓", "ok");
      pdfFrame.src = "/api/pdf?t=" + Date.now();
      pdfFrame.classList.remove("hidden");
      pdfEmpty.classList.add("hidden");
      msg("ok", "compiled successfully");
      // The build may have produced new files (main.pdf, etc.) — refresh tree.
      loadTree();
    } else {
      setStatus("compile error", "err");
      msg("err", j.log || "compile failed");
    }
  } catch (e) {
    setStatus("network error", "err");
    msg("err", String(e));
  } finally {
    btn.disabled = false;
  }
}

$("#save-btn").addEventListener("click", saveFile);
$("#generate-btn").addEventListener("click", generate);
$("#tree-refresh").addEventListener("click", loadTree);

// ---------- Project: open folder / download zip ----------

let projectPath = "";
const projectNameEl = $("#project-name");

async function refreshProjectName() {
  try {
    const r = await fetch("/api/project");
    const j = await r.json();
    projectPath = j.path;
    projectNameEl.textContent = j.name || "Files";
    projectNameEl.title = j.path;
  } catch (e) { /* ignore */ }
}

// Inline folder browser modal. Lists subdirectories of the path in the input.
// Single click selects a folder; double click descends into it; the Open button
// returns whatever is currently in the input.
const folderDlg = $("#folder-dialog");
const folderInput = $("#folder-input");
const folderList = $("#folder-list");
let folderListedPath = "";  // The path whose contents are currently shown.

async function browse(path) {
  folderList.innerHTML = '<div class="empty-row">Loading…</div>';
  try {
    const url = "/api/browse" + (path ? "?path=" + encodeURIComponent(path) : "");
    const r = await fetch(url);
    const j = await r.json();
    if (!r.ok) {
      folderList.innerHTML = `<div class="err-row">${escapeHTML(j.detail || "error")}</div>`;
      return;
    }
    folderListedPath = j.path;
    folderInput.value = j.path;
    folderList.innerHTML = "";
    if (!j.dirs.length) {
      folderList.innerHTML = '<div class="empty-row">(no subdirectories)</div>';
      return;
    }
    for (const d of j.dirs) {
      const row = document.createElement("div");
      row.className = "row";
      row.dataset.path = d.path;
      row.innerHTML = `<span class="icon">📁</span><span class="name">${escapeHTML(d.name)}</span>`;
      row.addEventListener("click", () => {
        folderList.querySelectorAll(".row.selected").forEach((r) => r.classList.remove("selected"));
        row.classList.add("selected");
        folderInput.value = d.path;
      });
      row.addEventListener("dblclick", () => browse(d.path));
      folderList.appendChild(row);
    }
  } catch (e) {
    folderList.innerHTML = `<div class="err-row">${escapeHTML(String(e))}</div>`;
  }
}

function askFolder(initial) {
  return new Promise((resolve) => {
    folderInput.value = initial || "";
    folderListedPath = "";
    browse(initial || "");
    folderDlg.showModal();
    setTimeout(() => { folderInput.focus(); folderInput.select(); }, 0);
    folderDlg.addEventListener("close", () => {
      resolve(folderDlg.returnValue === "ok" ? folderInput.value.trim() : null);
    }, { once: true });
  });
}

$("#folder-up").addEventListener("click", () => {
  const here = folderListedPath || folderInput.value;
  // Strip trailing slash, then drop last component.
  const clean = here.replace(/\/+$/, "");
  const idx = clean.lastIndexOf("/");
  const up = idx <= 0 ? "/" : clean.slice(0, idx);
  browse(up);
});

$("#folder-go").addEventListener("click", () => browse(folderInput.value));

folderInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter") {
    e.preventDefault();
    browse(folderInput.value);
  }
});

// Toast notifier — used for ephemeral "happened in the background" signals like
// the zip download finishing.
let toastTimer = null;
function toast(text, kind = "ok", ms = 2500) {
  const el = $("#toast");
  el.textContent = text;
  el.className = "toast " + kind;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.add("hidden"), ms);
}

$("#open-folder").addEventListener("click", async () => {
  const input = await askFolder(projectPath);
  if (!input) return;
  if (dirty && !confirm(`Discard unsaved changes to ${currentPath}?`)) return;
  try {
    const r = await fetch("/api/project", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path: input }),
    });
    const j = await r.json();
    if (!r.ok) {
      msg("err", `open folder: ${j.detail || "failed"}`);
      toast(j.detail || "open folder failed", "err");
      return;
    }
    msg("ok", `project: ${j.path}`);
    toast(`Opened ${j.name}`, "ok");
    await refreshProjectName();
    await loadTree();
    // Clear PDF + editor; try to auto-open main.tex if present.
    pdfFrame.src = "about:blank";
    pdfFrame.classList.add("hidden");
    pdfEmpty.classList.remove("hidden");
    dirty = false;
    try {
      await openFile("main.tex");
    } catch {
      suppressChange = true;
      cm.setValue("");
      suppressChange = false;
      currentPath = "";
      currentPathEl.textContent = "(no file open)";
    }
  } catch (e) {
    msg("err", `open folder: ${e}`);
  }
});

$("#download-zip").addEventListener("click", () => {
  // Anchor download avoids changing the current page state.
  const a = document.createElement("a");
  a.href = "/api/zip?t=" + Date.now();
  a.download = "";
  document.body.appendChild(a);
  a.click();
  a.remove();
  toast("Download started → check your Downloads folder", "ok");
});

// ---------- Git CLI ----------

const history = [];
let histIdx = -1;

cliForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const raw = cliInput.value.trim();
  if (!raw) return;
  history.push(raw);
  histIdx = history.length;
  cliInput.value = "";
  for (const part of raw.split(/\s*&&\s*/)) {
    const ok = await runGit(part);
    if (!ok) break;
  }
});

cliInput.addEventListener("keydown", (e) => {
  if (e.key === "ArrowUp") {
    if (histIdx > 0) {
      histIdx--;
      cliInput.value = history[histIdx] || "";
    }
    e.preventDefault();
  } else if (e.key === "ArrowDown") {
    if (histIdx < history.length - 1) {
      histIdx++;
      cliInput.value = history[histIdx] || "";
    } else {
      histIdx = history.length;
      cliInput.value = "";
    }
    e.preventDefault();
  }
});

async function runGit(args) {
  try {
    const r = await fetch("/api/git", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ args }),
    });
    const j = await r.json();
    logTo(gitLog, [
      { cls: "cmd", text: `$ git ${args}` },
      { cls: "out", text: j.stdout || "" },
      { cls: "err", text: j.stderr || "" },
    ]);
    // git ops can change files on disk → refresh tree.
    if (["pull", "checkout", "reset", "stash"].some((sub) => args.startsWith(sub))) {
      loadTree();
    }
    return !!j.ok;
  } catch (e) {
    logTo(gitLog, [{ cls: "cmd", text: `$ git ${args}` }, { cls: "err", text: String(e) }]);
    return false;
  }
}

// ---------- Splitters ----------

function makeSplitter(el, axis, getRect, applySize) {
  let dragging = false;
  el.addEventListener("mousedown", (e) => {
    dragging = true;
    document.body.style.cursor = axis === "x" ? "col-resize" : "row-resize";
    document.body.style.userSelect = "none";
    e.preventDefault();
  });
  window.addEventListener("mouseup", () => {
    if (!dragging) return;
    dragging = false;
    document.body.style.cursor = "";
    document.body.style.userSelect = "";
  });
  window.addEventListener("mousemove", (e) => {
    if (!dragging) return;
    const rect = getRect();
    if (axis === "x") {
      applySize(Math.min(Math.max(e.clientX - rect.left, 80), rect.width - 200));
    } else {
      applySize(Math.min(Math.max(rect.bottom - e.clientY, 80), rect.height - 200));
    }
  });
}

// Splitter between tree and rest of main.
let treeW = 220;
let editorFrac = 0.5; // editor share of (main width - tree - 8)
makeSplitter(
  $("#split-tree"),
  "x",
  () => document.querySelector("main").getBoundingClientRect(),
  (px) => { treeW = px; applyMainCols(); },
);
// Splitter between editor and pdf.
makeSplitter(
  $("#split-editor"),
  "x",
  () => document.querySelector("main").getBoundingClientRect(),
  (px) => {
    const rect = document.querySelector("main").getBoundingClientRect();
    const remaining = rect.width - treeW - 8;
    const editorPx = Math.min(Math.max(px - treeW - 4, 120), remaining - 120);
    editorFrac = editorPx / remaining;
    applyMainCols();
  },
);
function applyMainCols() {
  document.querySelector("main").style.gridTemplateColumns =
    `${treeW}px 4px ${editorFrac}fr 4px ${1 - editorFrac}fr`;
}

// Splitter between messages and git in the footer.
makeSplitter(
  $("#split-footer"),
  "x",
  () => document.querySelector("footer").getBoundingClientRect(),
  (px) => {
    const rect = document.querySelector("footer").getBoundingClientRect();
    const right = Math.min(Math.max(rect.right - rect.left - px - 4, 200), rect.width - 200);
    document.querySelector("footer").style.gridTemplateColumns = `1fr 4px ${right}px`;
  },
);

// Horizontal splitter between main area and footer.
makeSplitter(
  $("#split-bottom"),
  "y",
  () => ({ bottom: window.innerHeight, height: window.innerHeight }),
  (px) => {
    document.body.style.gridTemplateRows = `var(--header-h) 1fr 4px ${px}px`;
  },
);

// ---------- Boot ----------

refreshProjectName();
loadTree();
openFile("main.tex").catch(() => {});
fetch("/api/pdf", { method: "HEAD" }).then((r) => {
  if (r.ok) {
    pdfFrame.src = "/api/pdf?t=" + Date.now();
    pdfFrame.classList.remove("hidden");
    pdfEmpty.classList.add("hidden");
  }
});

window.addEventListener("beforeunload", (e) => {
  if (dirty) {
    e.preventDefault();
    e.returnValue = "";
  }
});

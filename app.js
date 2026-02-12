/* Spanish First Flashcards (PWA) — no backend — local progress only */

const POS_ALL = [
  "noun","verb","adjective","adverb","preposition","conjunction",
  "pronoun","interjection","determiner","other"
];

const $ = (id) => document.getElementById(id);

const state = {
  words: [],            // [{id, spanish, english, partOfSpeech}]
  queue: [],            // array of word IDs
  currentId: null,
  flipped: false
};

// ---------- Service worker ----------
if ("serviceWorker" in navigator) {
  navigator.serviceWorker.register("./sw.js").catch(() => {});
}

// ---------- Tabs ----------
document.querySelectorAll(".tab").forEach(btn => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach(b => b.classList.remove("active"));
    btn.classList.add("active");
    const tab = btn.dataset.tab;
    document.querySelectorAll(".panel").forEach(p => p.classList.remove("active"));
    $(`tab-${tab}`).classList.add("active");
    // refresh visible tab data
    if (tab === "today") refreshToday();
    if (tab === "browse") refreshBrowse();
    if (tab === "stats") refreshStats();
    if (tab === "study") refreshStudyUI();
  });
});

// ---------- IndexedDB ----------
const DB_NAME = "sf_flashcards_db";
const DB_VERSION = 1;

function openDB() {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(DB_NAME, DB_VERSION);
    req.onupgradeneeded = () => {
      const db = req.result;
      if (!db.objectStoreNames.contains("progress")) {
        db.createObjectStore("progress", { keyPath: "id" }); // id = word id
      }
      if (!db.objectStoreNames.contains("meta")) {
        db.createObjectStore("meta", { keyPath: "key" });
      }
    };
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

async function dbGet(store, key) {
  const db = await openDB();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(store, "readonly");
    const st = tx.objectStore(store);
    const req = st.get(key);
    req.onsuccess = () => resolve(req.result || null);
    req.onerror = () => reject(req.error);
  });
}

async function dbPut(store, value) {
  const db = await openDB();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(store, "readwrite");
    tx.oncomplete = () => resolve(true);
    tx.onerror = () => reject(tx.error);
    tx.objectStore(store).put(value);
  });
}

async function dbGetAll(store) {
  const db = await openDB();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(store, "readonly");
    const req = tx.objectStore(store).getAll();
    req.onsuccess = () => resolve(req.result || []);
    req.onerror = () => reject(req.error);
  });
}

async function dbClear(store) {
  const db = await openDB();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(store, "readwrite");
    const req = tx.objectStore(store).clear();
    req.onsuccess = () => resolve(true);
    req.onerror = () => reject(req.error);
  });
}

// ---------- Progress model ----------
function nowMs() { return Date.now(); }
function dayKey(ts = Date.now()) {
  const d = new Date(ts);
  // local date, stable key
  const yyyy = d.getFullYear();
  const mm = String(d.getMonth()+1).padStart(2,"0");
  const dd = String(d.getDate()).padStart(2,"0");
  return `${yyyy}-${mm}-${dd}`;
}

function defaultProgress(id) {
  return {
    id,
    dueAt: nowMs(),      // ms
    intervalDays: 0,
    ease: 2.5,
    reps: 0,
    total: 0,
    correct: 0
  };
}

function clamp(n, lo, hi) { return Math.max(lo, Math.min(hi, n)); }

function applyGrade(progress, grade) {
  // grade: again|hard|good|easy
  progress.total += 1;
  const correct = (grade !== "again");
  if (correct) progress.correct += 1;

  const easeDelta = (grade === "again") ? -0.20
                : (grade === "hard")  ? -0.05
                : (grade === "good")  ?  0.00
                :                        0.10;

  progress.ease = clamp(progress.ease + easeDelta, 1.3, 3.2);

  if (grade === "again") {
    progress.reps = Math.max(0, progress.reps - 1);
    progress.intervalDays = 0;
    progress.dueAt = nowMs() + 10 * 60 * 1000; // 10 minutes
    return progress;
  }

  if (progress.reps === 0) {
    progress.intervalDays = (grade === "hard") ? 1 : 1;
  } else if (progress.reps === 1) {
    progress.intervalDays = (grade === "hard") ? 2 : (grade === "easy" ? 4 : 3);
  } else {
    const mult = (grade === "hard") ? (progress.ease * 0.85)
               : (grade === "easy") ? (progress.ease * 1.15)
               :                      progress.ease;
    progress.intervalDays = Math.max(1, progress.intervalDays * mult);
  }

  progress.reps += 1;
  progress.dueAt = nowMs() + Math.round(progress.intervalDays * 24 * 60 * 60 * 1000);
  return progress;
}

// ---------- Load dataset ----------
async function loadWords() {
  const resp = await fetch("./words.json", { cache: "no-store" });
  const raw = await resp.json();
  // normalize, add stable IDs (spanish|pos). For personal use, good enough.
  state.words = raw.map(w => ({
    id: `${w.spanish}__${(w.partOfSpeech||"other").toLowerCase()}`.normalize("NFC"),
    spanish: String(w.spanish).trim(),
    english: String(w.english).trim(),
    partOfSpeech: String(w.partOfSpeech || "other").trim().toLowerCase()
  }));
}

// ---------- Queue builders ----------
async function buildDueQueue(limit = 200) {
  const allProg = await dbGetAll("progress");
  const progMap = new Map(allProg.map(p => [p.id, p]));

  const due = [];
  const ts = nowMs();

  for (const w of state.words) {
    const p = progMap.get(w.id) || defaultProgress(w.id);
    // if progress didn't exist yet, persist it once
    if (!progMap.has(w.id)) await dbPut("progress", p);
    if (p.dueAt <= ts) due.push(w.id);
  }

  // shuffle
  for (let i = due.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [due[i], due[j]] = [due[j], due[i]];
  }
  return due.slice(0, limit);
}

async function buildAnyQueue(limit = 20) {
  // Just take random words; prefer those with earlier dueAt
  const allProg = await dbGetAll("progress");
  const progMap = new Map(allProg.map(p => [p.id, p]));
  const scored = state.words.map(w => {
    const p = progMap.get(w.id) || defaultProgress(w.id);
    return { id: w.id, score: p.dueAt }; // earlier = more likely
  }).sort((a,b) => a.score - b.score);

  const pickFrom = scored.slice(0, Math.min(800, scored.length));
  const chosen = [];
  while (chosen.length < Math.min(limit, pickFrom.length)) {
    const idx = Math.floor(Math.random() * pickFrom.length);
    const id = pickFrom[idx].id;
    if (!chosen.includes(id)) chosen.push(id);
  }
  return chosen;
}

// ---------- UI helpers ----------
function getWord(id) { return state.words.find(w => w.id === id) || null; }

function setHint(id, msg) { $(id).textContent = msg || ""; }

function setFlipped(on) {
  state.flipped = on;
  const card = $("flashcard");
  if (!card) return;
  card.classList.toggle("flipped", on);
}

function renderCurrent() {
  const w = getWord(state.currentId);
  if (!w) {
    $("frontText").textContent = "All done 🎉";
    $("backText").textContent = "No cards in your queue.";
    $("queueLabel").textContent = `Queue: 0`;
    $("posLabel").textContent = `POS: —`;
    setHint("studyHint", "Go to Today to start a new session.");
    return;
  }
  $("frontText").textContent = w.spanish;
  $("backText").textContent = w.english;
  $("queueLabel").textContent = `Queue: ${state.queue.length + 1}`;
  $("posLabel").textContent = `POS: ${w.partOfSpeech}`;
  setHint("studyHint", "");
}

// ---------- Today ----------
async function refreshToday() {
  const allProg = await dbGetAll("progress");
  const ts = nowMs();
  const dueCount = allProg.filter(p => p.dueAt <= ts).length;

  // "new" = reps == 0
  const newCount = allProg.filter(p => (p.reps || 0) === 0).length;

  $("dueCount").textContent = String(dueCount);
  $("newCount").textContent = String(newCount);

  if (dueCount === 0) {
    setHint("todayHint", "Nothing due. Use “Study Anyway” to keep momentum.");
    $("startDueBtn").textContent = "Start Reviews (0 due)";
  } else {
    setHint("todayHint", "Tap Start Reviews to study what’s due now.");
    $("startDueBtn").textContent = "Start Reviews";
  }
}

// ---------- Study ----------
async function startSession(type) {
  setFlipped(false);
  if (type === "due") state.queue = await buildDueQueue(200);
  else state.queue = await buildAnyQueue(20);

  // If empty due queue, fall back to any
  if (type === "due" && state.queue.length === 0) {
    state.queue = await buildAnyQueue(20);
    setHint("studyHint", "No due cards — studying a mixed set instead.");
  } else {
    setHint("studyHint", "");
  }

  state.currentId = state.queue.shift() || null;
  renderCurrent();

  // switch to Study tab
  document.querySelector('.tab[data-tab="study"]').click();
}

async function refreshStudyUI() {
  // If there's no current, show guidance
  if (!state.currentId) {
    renderCurrent();
  }
}

async function submitGrade(grade) {
  const id = state.currentId;
  if (!id) return;

  const p = (await dbGet("progress", id)) || defaultProgress(id);
  applyGrade(p, grade);
  await dbPut("progress", p);

  // streak meta: update when you do a review today
  const today = dayKey();
  await dbPut("meta", { key: "lastReviewDay", value: today });
  const streak = await computeStreak();
  await dbPut("meta", { key: "streak", value: String(streak) });

  // next
  state.currentId = state.queue.shift() || null;
  setFlipped(false);
  renderCurrent();
  refreshToday();
  refreshStats();
}

// ---------- Browse ----------
function fillPosFilter() {
  const sel = $("posFilter");
  for (const pos of POS_ALL) {
    const opt = document.createElement("option");
    opt.value = pos;
    opt.textContent = pos;
    sel.appendChild(opt);
  }
}

function refreshBrowse() {
  const q = $("searchInput").value.trim().toLowerCase();
  const pos = $("posFilter").value;

  const filtered = state.words
    .filter(w => {
      const matchesText = !q || w.spanish.toLowerCase().includes(q) || w.english.toLowerCase().includes(q);
      const matchesPos = !pos || w.partOfSpeech === pos;
      return matchesText && matchesPos;
    })
    .slice(0, 300) // keep it fast on iPhone
    .sort((a,b) => a.spanish.localeCompare(b.spanish, "es"));

  const list = $("browseList");
  list.innerHTML = "";
  for (const w of filtered) {
    const div = document.createElement("div");
    div.className = "item";
    div.innerHTML = `
      <div class="top">
        <div class="es">${escapeHtml(w.spanish)}</div>
        <div class="pos">${escapeHtml(w.partOfSpeech)}</div>
      </div>
      <div class="en">${escapeHtml(w.english)}</div>
    `;
    list.appendChild(div);
  }
}

function escapeHtml(s) {
  return String(s)
    .replaceAll("&","&amp;")
    .replaceAll("<","&lt;")
    .replaceAll(">","&gt;")
    .replaceAll('"',"&quot;")
    .replaceAll("'","&#039;");
}

// ---------- Stats ----------
async function computeStreak() {
  // Simple personal streak:
  // - store "streak" + "lastReviewDay" in meta
  // - if lastReviewDay is today -> keep
  // - if lastReviewDay is yesterday and you review today -> +1
  // - else -> reset to 1 when you review today
  const metaLast = await dbGet("meta", "lastReviewDay");
  const metaStreak = await dbGet("meta", "streak");
  const lastDay = metaLast?.value || null;
  let streak = parseInt(metaStreak?.value || "0", 10) || 0;

  const today = dayKey();
  const yesterday = dayKey(Date.now() - 24*60*60*1000);

  if (!lastDay) return 0;
  if (lastDay === today) return streak; // already counted today
  // if last day was yesterday, we can increment when we review today
  if (lastDay === yesterday) return Math.max(1, streak + 1);
  // otherwise streak breaks
  return 1;
}

async function refreshStats() {
  const allProg = await dbGetAll("progress");
  const total = allProg.reduce((a,p) => a + (p.total || 0), 0);
  const correct = allProg.reduce((a,p) => a + (p.correct || 0), 0);
  const acc = total > 0 ? Math.round((correct / total) * 100) : null;

  // learned (rough): reps >= 6 and intervalDays >= 14
  const learned = allProg.filter(p => (p.reps || 0) >= 6 && (p.intervalDays || 0) >= 14).length;

  const metaStreak = await dbGet("meta", "streak");
  const streak = parseInt(metaStreak?.value || "0", 10) || 0;

  $("totalReviews").textContent = String(total);
  $("accuracy").textContent = acc === null ? "—" : `${acc}%`;
  $("learnedCount").textContent = String(learned);
  $("streak").textContent = String(streak);
}

// ---------- Reset ----------
async function resetAll() {
  await dbClear("progress");
  await dbClear("meta");
  // recreate progress so counts work immediately
  for (const w of state.words) {
    await dbPut("progress", defaultProgress(w.id));
  }
  setHint("aboutHint", "Progress reset. (Only on this iPhone.)");
  refreshToday();
  refreshStats();
}

// ---------- Wire up events ----------
$("startDueBtn").addEventListener("click", () => startSession("due"));
$("studyAnyBtn").addEventListener("click", () => startSession("any"));

$("flashcard").addEventListener("click", () => setFlipped(!state.flipped));

document.querySelectorAll(".grade").forEach(btn => {
  btn.addEventListener("click", () => submitGrade(btn.dataset.grade));
});

$("searchInput").addEventListener("input", refreshBrowse);
$("posFilter").addEventListener("change", refreshBrowse);

$("resetBtn").addEventListener("click", async () => {
  setHint("aboutHint", "Resetting…");
  await resetAll();
});

// ---------- Boot ----------
(async function main() {
  fillPosFilter();
  await loadWords();

  // Ensure progress exists for all words
  const allProg = await dbGetAll("progress");
  const have = new Set(allProg.map(p => p.id));
  for (const w of state.words) {
    if (!have.has(w.id)) await dbPut("progress", defaultProgress(w.id));
  }

  await refreshToday();
  await refreshStats();
  refreshBrowse();
  renderCurrent();
})();

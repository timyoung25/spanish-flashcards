/* Spanish First Flashcards — simplified flip-safe version */

const $ = (id) => document.getElementById(id);

const state = {
  words: [],
  queue: [],
  currentId: null,
  flipped: false
};

/* ---------- Service worker ---------- */
if ("serviceWorker" in navigator) {
  navigator.serviceWorker.register("./sw.js").catch(() => {});
}

/* ---------- Load words ---------- */
async function loadWords() {
  const resp = await fetch("./words.json", { cache: "no-store" });
  state.words = await resp.json();

  state.words = state.words.map(w => ({
    id: `${w.spanish}_${w.partOfSpeech}`,
    ...w
  }));
}

/* ---------- Queue ---------- */
function buildQueue() {
  state.queue = state.words.slice(0, 20);
}

/* ---------- Rendering ---------- */
function renderCurrent() {
  const word = state.words.find(w => w.id === state.currentId);
  if (!word) return;

  $("frontText").textContent = word.spanish;
  $("backText").textContent = word.english;
  $("posLabel").textContent = `POS: ${word.partOfSpeech}`;
  $("queueLabel").textContent = `Queue: ${state.queue.length}`;
}

/* ---------- Study controls ---------- */
function startSession() {
  buildQueue();
  state.currentId = state.queue.shift().id;
  state.flipped = false;
  document.getElementById("flashcard").classList.remove("flipped");
  renderCurrent();
}

function flipCard() {
  const word = state.words.find(w => w.id === state.currentId);
  if (!word) return;

  state.flipped = !state.flipped;
  document.getElementById("flashcard").classList.toggle("flipped");

  /* Force text repaint (Safari quirk fix) */
  $("frontText").textContent = word.spanish;
  $("backText").textContent = word.english;
}

function nextCard() {
  if (state.queue.length === 0) return;
  state.currentId = state.queue.shift().id;
  state.flipped = false;
  document.getElementById("flashcard").classList.remove("flipped");
  renderCurrent();
}

/* ---------- Init ---------- */
document.addEventListener("DOMContentLoaded", async () => {
  await loadWords();
  startSession();

  $("flashcard").addEventListener("click", flipCard);

  document.querySelectorAll(".grade").forEach(btn => {
    btn.addEventListener("click", nextCard);
  });
});

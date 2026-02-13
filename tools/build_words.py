import json
import re
import tarfile
from io import BytesIO
from typing import Dict, Tuple, List, Optional

import requests
from bs4 import BeautifulSoup

# ---- Config ----
N = 4000

WIKI_API = "https://en.wiktionary.org/w/api.php"
WIKI_TITLE = "Wiktionary:Frequency_lists/Spanish/Subtitles10K"

# FreeDict Spanish->English source archive (TEI XML inside)
FREEDICT_SRC_TAR_XZ = "https://download.freedict.org/dictionaries/spa-eng/0.3.1/freedict-spa-eng-0.3.1.src.tar.xz"

UA = {"User-Agent": "spanish-flashcards-builder/1.3 (personal study)"}

POS_MAP = {
    "noun": "noun",
    "verb": "verb",
    "adj": "adjective",
    "adjective": "adjective",
    "adv": "adverb",
    "adverb": "adverb",
    "prep": "preposition",
    "preposition": "preposition",
    "conj": "conjunction",
    "conjunction": "conjunction",
    "pron": "pronoun",
    "pronoun": "pronoun",
    "interj": "interjection",
    "interjection": "interjection",
    "det": "determiner",
    "determiner": "determiner",
}

def norm_pos(pos: str) -> str:
    p = (pos or "").strip().lower()
    p = re.sub(r"[^a-z]", "", p)
    return POS_MAP.get(p, "other")

def is_probably_verb(pos: str) -> bool:
    return norm_pos(pos) == "verb"

def clean_english(s: str) -> str:
    s = re.sub(r"\s+", " ", (s or "").strip())
    s = s.strip(" ;,")
    return s

def ensure_to_for_verbs(en: str, pos: str) -> str:
    en = clean_english(en)
    if is_probably_verb(pos) and en and not en.lower().startswith("to "):
        return "to " + en
    return en

def clean_spanish_token(token: str) -> str:
    token = (token or "").replace("\u00a0", " ").strip().lower()
    token = re.sub(r"\s+", " ", token).strip()
    if not token:
        return ""
    token = token.split()[0].strip()
    # Keep Spanish letters and inverted punctuation
    token = re.sub(r"[^a-záéíóúüñ¿¡]+", "", token, flags=re.IGNORECASE)
    return token

def get_rendered_html(title: str) -> str:
    params = {
        "action": "parse",
        "page": title,
        "prop": "text",
        "format": "json",
        "formatversion": "2",
    }
    r = requests.get(WIKI_API, params=params, headers=UA, timeout=60)
    r.raise_for_status()
    data = r.json()
    html = (data.get("parse") or {}).get("text", "")
    if not html:
        raise RuntimeError(f"Could not fetch rendered HTML for {title}.")
    return html

def extract_top_n_from_html(html: str, n: int) -> List[str]:
    soup = BeautifulSoup(html, "lxml")

    # Frequency pages usually contain a big table. Grab the "largest" table by row count.
    tables = soup.find_all("table")
    if not tables:
        raise RuntimeError("No tables found in rendered HTML from Wiktionary API.")

    best_table = None
    best_rows = 0
    for t in tables:
        rows = t.find_all("tr")
        if len(rows) > best_rows:
            best_rows = len(rows)
            best_table = t

    if not best_table or best_rows < 100:
        raise RuntimeError("Could not find a large frequency table in rendered HTML.")

    words_by_rank: Dict[int, str] = {}

    for tr in best_table.find_all("tr"):
        tds = tr.find_all(["td", "th"])
        if len(tds) < 2:
            continue

        rank_txt = tds[0].get_text(" ", strip=True).replace(".", "")
        rank_txt = re.sub(r"[^\d]", "", rank_txt)
        if not rank_txt.isdigit():
            continue
        rank = int(rank_txt)
        if rank < 1 or rank > n:
            continue

        # Usually: rank | word | frequency | lemma(s)
        word_cell = tds[1].get_text(" ", strip=True)
        lemma_cell = tds[-1].get_text(" ", strip=True) if len(tds) >= 4 else word_cell

        lemma = clean_spanish_token(lemma_cell)
        word = clean_spanish_token(word_cell)
        tok = lemma or word
        if tok:
            words_by_rank.setdefault(rank, tok)

    out: List[str] = []
    seen = set()
    for rnk in range(1, n + 1):
        w = words_by_rank.get(rnk, "")
        if w and w not in seen:
            seen.add(w)
            out.append(w)
        if len(out) >= n:
            break

    if len(out) < n:
        raise RuntimeError(f"Only got {len(out)} lemmas, expected {n}.")
    return out[:n]

def download_freedict_src() -> bytes:
    r = requests.get(FREEDICT_SRC_TAR_XZ, headers=UA, timeout=120)
    r.raise_for_status()
    return r.content

def extract_tei_from_tar_xz(tar_xz_bytes: bytes) -> bytes:
    bio = BytesIO(tar_xz_bytes)
    with tarfile.open(fileobj=bio, mode="r:xz") as tf:
        members = tf.getmembers()
        tei_member = None

        for m in members:
            name = m.name.lower()
            if ("tei" in name or name.endswith(".tei") or name.endswith(".tei.xml")) and "readme" not in name and "license" not in name:
                tei_member = m
                break

        if not tei_member:
            for m in members:
                if m.name.lower().endswith((".tei", ".tei.xml", ".xml")):
                    tei_member = m
                    break

        if not tei_member:
            raise RuntimeError("Could not find TEI/XML in FreeDict source tar.xz")

        f = tf.extractfile(tei_member)
        if not f:
            raise RuntimeError("Could not extract TEI/XML from FreeDict archive.")
        return f.read()

def parse_freedict_tei(tei_xml: bytes) -> Dict[str, Tuple[str, str]]:
    soup = BeautifulSoup(tei_xml, "lxml-xml")
    mapping: Dict[str, Tuple[str, str]] = {}

    for entry in soup.find_all("entry"):
        orth = entry.find("orth")
        if not orth:
            continue
        head = orth.get_text(" ", strip=True).lower()
        head = re.sub(r"\s+", " ", head).strip()
        if not head:
            continue

        pos = ""
        pos_tag = entry.find("pos")
        if pos_tag:
            pos = pos_tag.get_text(" ", strip=True)
        if not pos:
            gram = entry.find("gram", attrs={"type": "pos"})
            if gram:
                pos = gram.get_text(" ", strip=True)
        pos = norm_pos(pos)

        gloss = ""
        cit = entry.find("cit", attrs={"type": "trans"})
        if cit:
            q = cit.find("quote")
            if q:
                gloss = q.get_text(" ", strip=True)
        if not gloss:
            q = entry.find("quote")
            if q:
                gloss = q.get_text(" ", strip=True)
        gloss = clean_english(gloss)

        if head not in mapping and gloss:
            mapping[head] = (gloss, pos)

    return mapping

def main():
    print("Fetching top words (rendered HTML via API)…")
    html = get_rendered_html(WIKI_TITLE)
    top = extract_top_n_from_html(html, N)

    print("Downloading FreeDict spa-eng source…")
    tar_xz = download_freedict_src()

    print("Extracting TEI/XML…")
    tei = extract_tei_from_tar_xz(tar_xz)

    print("Parsing FreeDict…")
    lex = parse_freedict_tei(tei)

    out = []
    missing = 0
    for w in top:
        en = ""
        pos = "other"
        if w in lex:
            en, pos = lex[w]
        else:
            missing += 1

        en = ensure_to_for_verbs(en, pos)
        out.append({"spanish": w, "english": en, "partOfSpeech": pos})

    with open("words.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False)

    print(f"Done. Wrote {len(out)} items to words.json. Missing translations: {missing}")

if __name__ == "__main__":
    main()

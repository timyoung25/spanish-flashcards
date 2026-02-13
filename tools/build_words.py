import json
import re
import tarfile
from io import BytesIO
from typing import Dict, Tuple, Optional, List

import requests
from bs4 import BeautifulSoup

# ---- Config ----
N = 4000

# Wiktionary subtitles frequency list pages (word + lemma column)
# We'll pull ranks 1..4000 from these 4 pages:
WIKI_PAGES = [
    "https://en.wiktionary.org/wiki/Wiktionary:Frequency_lists/Spanish/Subtitles10K",  # contains 1..10000
]

# FreeDict Spanish->English source archive (TEI XML inside)
# Note: hosted by FreeDict downloads.
FREEDICT_SRC_TAR_XZ = "https://download.freedict.org/dictionaries/spa-eng/0.3.1/freedict-spa-eng-0.3.1.src.tar.xz"

UA = {"User-Agent": "spanish-flashcards-builder/1.0 (personal study)"}

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
    # Drop trailing punctuation that looks like definition fragments
    s = s.strip(" ;,")
    return s

def ensure_to_for_verbs(en: str, pos: str) -> str:
    en = clean_english(en)
    if is_probably_verb(pos) and en and not en.lower().startswith("to "):
        return "to " + en
    return en

def fetch_wiktionary_top_n(n: int) -> List[str]:
    # We use the Subtitles10K page and take the "lemma forms" column when available.
    # If lemma column has multiple lemmas, take the first.
    url = WIKI_PAGES[0]
    html = requests.get(url, headers=UA, timeout=60).text
    soup = BeautifulSoup(html, "lxml")

    # The page is a wikitable; rows contain rank, word, ppm, lemma forms
    table = soup.find("table", class_="wikitable")
    if not table:
        raise RuntimeError("Could not find wikitable on Wiktionary frequency page.")

    out = []
    for tr in table.find_all("tr"):
        tds = tr.find_all(["td", "th"])
        if len(tds) < 2:
            continue

        # rank in first cell usually
        rank_text = tds[0].get_text(strip=True)
        if not rank_text.isdigit():
            continue
        rank = int(rank_text)
        if rank < 1 or rank > n:
            continue

        # Prefer lemma column (often last cell). If missing, use word cell.
        word = tds[1].get_text(" ", strip=True)
        lemma = tds[-1].get_text(" ", strip=True) if len(tds) >= 4 else word

        # lemma can include multiple forms; take first token
        lemma_first = lemma.split()[0].strip()
        lemma_first = lemma_first.strip().lower()

        # keep Spanish letters; drop weird punctuation
        lemma_first = re.sub(r"[^a-záéíóúüñ¿¡]+", "", lemma_first, flags=re.IGNORECASE)
        if not lemma_first:
            continue

        out.append(lemma_first)

    # Keep order; de-dupe while preserving rank order
    seen = set()
    uniq = []
    for w in out:
        if w not in seen:
            seen.add(w)
            uniq.append(w)
        if len(uniq) >= n:
            break

    if len(uniq) < n:
        raise RuntimeError(f"Only got {len(uniq)} lemmas from Wiktionary, expected {n}.")
    return uniq

def download_freedict_src() -> bytes:
    r = requests.get(FREEDICT_SRC_TAR_XZ, headers=UA, timeout=120)
    r.raise_for_status()
    return r.content

def extract_tei_from_tar_xz(tar_xz_bytes: bytes) -> bytes:
    # tarfile can read xz-compressed tar with mode "r:xz"
    bio = BytesIO(tar_xz_bytes)
    with tarfile.open(fileobj=bio, mode="r:xz") as tf:
        # Look for .tei or .xml inside
        members = tf.getmembers()
        tei_member = None
        for m in members:
            name = m.name.lower()
            if name.endswith(".tei") or name.endswith(".tei.xml") or name.endswith(".xml"):
                # prefer main dictionary file, not metadata
                if "tei" in name and "readme" not in name and "license" not in name:
                    tei_member = m
                    break
        if not tei_member:
            # fallback to first xml-like
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
    """
    Returns mapping:
      spanish_lemma -> (english_gloss, pos)
    We grab first translation/gloss and POS if present.
    """
    soup = BeautifulSoup(tei_xml, "lxml-xml")
    mapping: Dict[str, Tuple[str, str]] = {}

    # TEI entries usually under <entry>
    for entry in soup.find_all("entry"):
        # headword often in <form><orth>
        orth = entry.find("orth")
        if not orth:
            continue
        head = orth.get_text(" ", strip=True).lower()
        head = re.sub(r"\s+", " ", head).strip()
        if not head:
            continue

        # POS can appear as <pos> or <gram type="pos"> etc
        pos = ""
        pos_tag = entry.find("pos")
        if pos_tag:
            pos = pos_tag.get_text(" ", strip=True)

        if not pos:
            gram = entry.find("gram", attrs={"type": "pos"})
            if gram:
                pos = gram.get_text(" ", strip=True)

        # English gloss: often in <cit type="trans"><quote>
        gloss = ""
        cit = entry.find("cit", attrs={"type": "trans"})
        if cit:
            q = cit.find("quote")
            if q:
                gloss = q.get_text(" ", strip=True)

        # fallback: first <quote> anywhere
        if not gloss:
            q = entry.find("quote")
            if q:
                gloss = q.get_text(" ", strip=True)

        gloss = clean_english(gloss)
        pos = norm_pos(pos)

        if head not in mapping and gloss:
            mapping[head] = (gloss, pos)

    return mapping

def main():
    print("Fetching top words from Wiktionary…")
    top = fetch_wiktionary_top_n(N)

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
            # Keep it usable even when missing: show blank English so you notice.
            en = ""
            pos = "other"

        en = ensure_to_for_verbs(en, pos)
        out.append({"spanish": w, "english": en, "partOfSpeech": pos})

    with open("words.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False)

    print(f"Done. Wrote {len(out)} items to words.json. Missing translations: {missing}")

if __name__ == "__main__":
    main()

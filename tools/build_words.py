import json
import re
import tarfile
from io import BytesIO
from typing import Dict, Tuple, List

import requests
from bs4 import BeautifulSoup

# ---- Config ----
N = 4000

# Use the actual Wiktionary Spanish frequency list pages (subtitles-based list),
# split by ranges. These pages contain the list content.
WIKI_PAGES = [
    "https://en.wiktionary.org/wiki/Wiktionary:Frequency_lists/Spanish1000",
    "https://en.wiktionary.org/wiki/Wiktionary:Frequency_lists/Spanish1001-2000",
    "https://en.wiktionary.org/wiki/Wiktionary:Frequency_lists/Spanish2001-3000",
    "https://en.wiktionary.org/wiki/Wiktionary:Frequency_lists/Spanish3001-4000",
]

# FreeDict Spanish->English source archive (TEI XML inside)
FREEDICT_SRC_TAR_XZ = "https://download.freedict.org/dictionaries/spa-eng/0.3.1/freedict-spa-eng-0.3.1.src.tar.xz"

UA = {"User-Agent": "spanish-flashcards-builder/1.1 (personal study)"}

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

def _clean_spanish_token(token: str) -> str:
    token = (token or "").strip().lower()
    token = token.replace("\u00a0", " ")
    token = token.split()[0].strip()
    # Keep Spanish letters and punctuation marks that can be part of tokens (¿¡)
    token = re.sub(r"[^a-záéíóúüñ¿¡]+", "", token, flags=re.IGNORECASE)
    return token

def _parse_wikitable(html: str) -> List[str]:
    soup = BeautifulSoup(html, "lxml")
    table = soup.find("table", class_="wikitable")
    if not table:
        return []

    out = []
    for tr in table.find_all("tr"):
        tds = tr.find_all("td")
        if len(tds) < 2:
            continue

        rank_text = tds[0].get_text(strip=True).replace(".", "")
        if not rank_text.isdigit():
            continue
        rank = int(rank_text)

        # Column 2 is "word", last column often "lemma forms"
        word = tds[1].get_text(" ", strip=True)
        lemma = tds[-1].get_text(" ", strip=True) if len(tds) >= 4 else word
        lemma_first = _clean_spanish_token(lemma) or _clean_spanish_token(word)
        if not lemma_first:
            continue

        out.append((rank, lemma_first))

    out.sort(key=lambda x: x[0])
    return [w for _, w in out]

def _parse_plaintext_table(html: str) -> List[str]:
    # Some pages render as plain text rows like:
    # "9001.  moisés  5  moisés Moisés"
    soup = BeautifulSoup(html, "lxml")
    text = soup.get_text("\n", strip=True)

    rows = []
    for line in text.splitlines():
        line = line.strip()
        m = re.match(r"^(\d+)\.\s+([^\s]+)\s+\d+\s+(.*)$", line)
        if not m:
            continue
        rank = int(m.group(1))
        word = _clean_spanish_token(m.group(2))
        lemma_blob = m.group(3)
        lemma = _clean_spanish_token(lemma_blob) or word
        if lemma:
            rows.append((rank, lemma))

    rows.sort(key=lambda x: x[0])
    return [w for _, w in rows]

def fetch_wiktionary_top_n(n: int) -> List[str]:
    collected = []
    for url in WIKI_PAGES:
        html = requests.get(url, headers=UA, timeout=60).text

        part = _parse_wikitable(html)
        if not part:
            part = _parse_plaintext_table(html)

        if not part:
            raise RuntimeError(f"Could not parse word list from: {url}")

        collected.extend(part)

    # de-dupe while preserving order
    seen = set()
    uniq = []
    for w in collected:
        if w and w not in seen:
            seen.add(w)
            uniq.append(w)
        if len(uniq) >= n:
            break

    if len(uniq) < n:
        raise RuntimeError(f"Only got {len(uniq)} lemmas, expected {n}.")
    return uniq

def download_freedict_src() -> bytes:
    r = requests.get(FREEDICT_SRC_TAR_XZ, headers=UA, timeout=120)
    r.raise_for_status()
    return r.content

def extract_tei_from_tar_xz(tar_xz_bytes: bytes) -> bytes:
    bio = BytesIO(tar_xz_bytes)
    with tarfile.open(fileobj=bio, mode="r:xz") as tf:
        members = tf.getmembers()
        tei_member = None

        # Prefer something that looks like the main TEI dictionary
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

        en = ensure_to_for_verbs(en, pos)
        out.append({"spanish": w, "english": en, "partOfSpeech": pos})

    with open("words.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False)

    print(f"Done. Wrote {len(out)} items to words.json. Missing translations: {missing}")

if __name__ == "__main__":
    main()

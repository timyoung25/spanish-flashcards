import json
import re
import tarfile
from io import BytesIO
from typing import Dict, Tuple, List, Optional

import requests
from bs4 import BeautifulSoup

# ---- Config ----
N = 4000

# Pull wikitext via MediaWiki API (much more stable than scraping HTML)
WIKI_API = "https://en.wiktionary.org/w/api.php"
WIKI_TITLE = "Wiktionary:Frequency_lists/Spanish/Subtitles10K"

# FreeDict Spanish->English source archive (TEI XML inside)
FREEDICT_SRC_TAR_XZ = "https://download.freedict.org/dictionaries/spa-eng/0.3.1/freedict-spa-eng-0.3.1.src.tar.xz"

UA = {"User-Agent": "spanish-flashcards-builder/1.2 (personal study)"}

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

def strip_wiki_markup(s: str) -> str:
    """
    Strip common wiki markup like [[link|text]], [[link]], bold/italics.
    """
    s = s or ""
    s = s.replace("\u00a0", " ")
    # [[a|b]] -> b
    s = re.sub(r"\[\[([^\]|]+)\|([^\]]+)\]\]", r"\2", s)
    # [[a]] -> a
    s = re.sub(r"\[\[([^\]]+)\]\]", r"\1", s)
    # bold/italics
    s = s.replace("'''", "").replace("''", "")
    # HTML refs and templates (very rough)
    s = re.sub(r"<ref[^>]*>.*?</ref>", "", s)
    s = re.sub(r"{{[^}]+}}", "", s)
    return s.strip()

def clean_spanish_token(token: str) -> str:
    token = strip_wiki_markup(token).strip().lower()
    token = re.sub(r"\s+", " ", token).strip()
    if not token:
        return ""
    # Take first token if multiple words
    token = token.split()[0].strip()
    # Keep Spanish letters and inverted punctuation
    token = re.sub(r"[^a-záéíóúüñ¿¡]+", "", token, flags=re.IGNORECASE)
    return token

def get_wikitext(title: str) -> str:
    params = {
        "action": "parse",
        "page": title,
        "prop": "wikitext",
        "format": "json",
        "formatversion": "2",
    }
    r = requests.get(WIKI_API, params=params, headers=UA, timeout=60)
    r.raise_for_status()
    data = r.json()
    wt = data.get("parse", explanation := {}).get("wikitext", "")
    if not wt:
        raise RuntimeError(f"Could not fetch wikitext for {title}. Got keys: {list(data.keys())}")
    return wt

def parse_subtitles10k_wikitext(wt: str, n: int) -> List[str]:
    """
    The Subtitles10K page is typically a wikitable in wikitext:
      |-
      | 1 || de || 57770 || de
    We parse rows by capturing lines that start with '|' and contain '||'.
    """
    words_by_rank: Dict[int, str] = {}

    # Find table row lines of the form: | rank || word || ... || lemma
    # We'll parse by splitting on '||' after removing leading '|'.
    for line in wt.splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        if "||" not in line:
            continue

        # remove leading |
        body = line.lstrip("|").strip()
        parts = [p.strip() for p in body.split("||")]
        if len(parts) < 2:
            continue

        rank_txt = strip_wiki_markup(parts[0]).strip().rstrip(".")
        if not rank_txt.isdigit():
            continue
        rank = int(rank_txt)
        if rank < 1 or rank > n:
            continue

        # prefer lemma column if present (often last), otherwise word column
        word_cell = parts[1]
        lemma_cell = parts[-1] if len(parts) >= 4 else parts[1]

        lemma = clean_spanish_token(lemma_cell)
        word = clean_spanish_token(word_cell)
        tok = lemma or word
        if tok:
            words_by_rank.setdefault(rank, tok)

    if len(words_by_rank) < n:
        # As a fallback, sometimes ranks appear as "1. de" in plain text.
        # Try that too.
        for line in wt.splitlines():
            m = re.match(r"^(\d+)\.\s+([^\s]+)", strip_wiki_markup(line))
            if not m:
                continue
            rank = int(m.group(1))
            if rank < 1 or rank > n:
                continue
            tok = clean_spanish_token(m.group(2))
            if tok:
                words_by_rank.setdefault(rank, tok)

    # Build in order, de-dupe while preserving rank order
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

        # Prefer the main TEI dictionary file
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
    print("Fetching top words (wikitext via API)…")
    wt = get_wikitext(WIKI_TITLE)
    top = parse_subtitles10k_wikitext(wt, N)

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

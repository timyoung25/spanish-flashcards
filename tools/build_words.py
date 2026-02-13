import json
import re
import tarfile
from io import BytesIO
from typing import Dict, Tuple, List

import requests
from bs4 import BeautifulSoup
from wordfreq import top_n_list

N = 4000

# FreeDict Spanish->English source archive (TEI XML inside)
FREEDICT_SRC_TAR_XZ = "https://download.freedict.org/dictionaries/spa-eng/0.3.1/freedict-spa-eng-0.3.1.src.tar.xz"

UA = {"User-Agent": "spanish-flashcards-builder/2.0 (personal study)"}

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

def is_verb(pos: str) -> bool:
    return norm_pos(pos) == "verb"

def clean_english(s: str) -> str:
    s = re.sub(r"\s+", " ", (s or "").strip())
    return s.strip(" ;,")

def ensure_to_for_verbs(en: str, pos: str) -> str:
    en = clean_english(en)
    if is_verb(pos) and en and not en.lower().startswith("to "):
        return "to " + en
    return en

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
    print("Getting top words from wordfreq…")
    top: List[str] = top_n_list("es", N)

    print("Downloading FreeDict spa-eng source…")
    tar_xz = download_freedict_src()

    print("Extracting TEI/XML…")
    tei = extract_tei_from_tar_xz(tar_xz)

    print("Parsing FreeDict…")
    lex = parse_freedict_tei(tei)

    out = []
    missing = 0
    for w in top:
        w = w.strip().lower()
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

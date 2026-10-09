"""
Reference fix hook (2026-10-09).

PROBLEM (found by idox_reference_probe.py on the Babergh / Mid Suffolk portal):
85 of 85 list results came back with the application DESCRIPTION as the
reference, because this portal's newer Idox template writes

    <p class="metaInfo"><strong>Application. No:</strong> DC/26/04409
        <span class="divider">·</span> Received: Fri 09 Oct 2026 ...

The label sits in its own <strong> tag, so the label and the number end up in
separate pieces of text, and the parser (which reads "Ref. No: <value>" from one
piece of text) finds an empty reference and falls back to the description. About
4,000 rows across 26 councils have this symptom (the other councils' rows stop on
about 16 July; on this portal it is still happening).

FIX: before the production parser sees a result, merge a "<strong>Application. No:
</strong> value" label into ordinary text "Ref. No: value" — the form the parser
already understands. As a safety net, if the parsed reference STILL looks like a
description, look for the number in the result's text and use that. Results that
already parse correctly are never changed.

Use: add ONE line to the bottom of idox_scraper.py, next to the shared-portals hook:
        import reference_fix_hook
"""
import atexit
import re
import sys

from bs4 import NavigableString

LABEL_RE = re.compile(r"^(application|app|ref|reference)\b[^:]{0,12}:\s*$", re.I)
FALLBACK_RE = re.compile(r"(?:application|app|ref|reference)\.?\s*(?:no|number)?\.?\s*:\s*([A-Za-z0-9][^\s·|]{2,40})", re.I)
stats = {"normalised": 0, "post_fixed": 0}


def looks_bad(ref) -> bool:
    ref = ref or ""
    return len(ref) > 40 or not any(ch.isdigit() for ch in ref)


def _meta(item):
    return item.find(class_=re.compile(r"metaInfo", re.I)) if hasattr(item, "find") else None


def normalise_item(item) -> bool:
    """Turn '<strong>Application. No:</strong> DC/26/04409' into the plain text
    'Ref. No: DC/26/04409'. Returns True if it changed anything."""
    meta = _meta(item)
    if meta is None:
        return False
    changed = False
    for strong in meta.find_all("strong"):
        if not LABEL_RE.match(strong.get_text(" ", strip=True)):
            continue
        value = strong.next_sibling
        if isinstance(value, NavigableString) and str(value).strip():
            value.replace_with(NavigableString("Ref. No: " + " ".join(str(value).split())))
            strong.decompose()
            changed = True
    return changed


def extract_reference(item):
    meta = _meta(item)
    if meta is None:
        return None
    m = FALLBACK_RE.search(meta.get_text(" ", strip=True))
    return m.group(1) if m else None


def wrap(parse):
    if getattr(parse, "_reference_fix", False):
        return parse

    def fixed(item, *args, **kwargs):
        try:
            if normalise_item(item):
                stats["normalised"] += 1
        except Exception:
            pass                                    # never let a tidy-up stop a scrape
        result = parse(item, *args, **kwargs)
        try:
            if isinstance(result, dict) and looks_bad(result.get("reference")):
                found = extract_reference(item)
                if found and not looks_bad(found):
                    result["reference"] = found
                    stats["post_fixed"] += 1
        except Exception:
            pass
        return result

    fixed._reference_fix = True
    return fixed


def install() -> int:
    done = 0
    for name in ("__main__", "idox_scraper"):
        mod = sys.modules.get(name)
        fn = getattr(mod, "_parse_result", None) if mod is not None else None
        if fn is not None and not getattr(fn, "_reference_fix", False):
            mod._parse_result = wrap(fn)
            done += 1
    return done


atexit.register(lambda: (stats["normalised"] or stats["post_fixed"]) and print(
    f"reference_fix_hook: {stats['normalised']} results had their reference label tidied, "
    f"{stats['post_fixed']} more repaired from the text"))
install()

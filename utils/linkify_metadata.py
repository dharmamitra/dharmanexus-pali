#!/usr/bin/env python3
"""Make catalogue raw_metadata (markdown) render properly in the frontend (react-markdown, no GFM, no remark-breaks):
1. bare URLs -> markdown links (2026-11-21)
2. hard line breaks: consecutive lines ("**ID:** ...\n**Title (Sanskrit):** ...") would otherwise run together into
   one paragraph, so every line followed by another non-blank line gets two trailing spaces. Headings, list items,
   table rows and code blocks are left alone.

python3 utils/linkify_metadata.py [--dry-run]     (run from the repo root)

Touches only the raw_metadata field of <XX>_files.json and metadata/*.json. URLs already inside a markdown link
([text](url)), an autolink (<url>) or link text are left alone. Trailing punctuation and unbalanced closing
brackets are not part of the URL; URLs in code spans/blocks stay literal. Each file is written back with the JSON layout it had (detected by re-dumping).
"""
import glob
import json
import re
import sys

URL = re.compile(r"https?://[^\s<>\"\]\[`]+")


def _trim(u):
    while u and u[-1] in ".,;:!?*_'’”":
        u = u[:-1]
    for o, c in ("()", "[]"):
        while u.endswith(c) and u.count(c) > u.count(o):
            u = u[:-1]
            while u and u[-1] in ".,;:!?*_'’”":
                u = u[:-1]
    return u


def _md_escape(u):
    return u.replace("_", r"\_").replace("*", r"\*")


def linkify(md):
    if not md or "http" not in md:
        return md, 0
    out, pos, n = [], 0, 0
    code = [(c.start(), c.end()) for c in re.finditer(r"(?ms)^\s*(```|~~~).*?^\s*\1|`[^`\n]+`", md)]
    for m in URL.finditer(md):
        s = m.start()
        if s < pos or any(a <= s < b for a, b in code):
            continue                                       # inside inline code / a fenced block: literal
        before = md[max(0, s - 2):s]
        if before.endswith("(") and before.startswith("]") or before.endswith("<") or before.endswith("]("):
            continue                                       # already a link target / autolink
        # inside link text [ ... ] ?
        lb, rb = md.rfind("[", 0, s), md.rfind("]", 0, s)
        if lb > rb and md.find("](", s) != -1 and "\n" not in md[lb:md.find("](", s)]:
            continue
        u = _trim(m.group())
        if len(u) <= len("https://"):
            continue
        out.append(md[pos:s])
        out.append(f"[{_md_escape(u)}]({u.replace(' ', '%20').replace(')', '%29').replace('(', '%28')})")
        pos = s + len(u)
        n += 1
    out.append(md[pos:])
    return "".join(out), n


_BLOCK = re.compile(r"^\s*(#{1,6}\s|[-*+]\s|\d+[.)]\s|\||```|~~~|>)")


def hard_breaks(md):
    if not md or "\n" not in md:
        return md, 0
    lines, n, code = md.split("\n"), 0, False
    for i in range(len(lines) - 1):
        l, nx = lines[i], lines[i + 1]
        if l.lstrip().startswith(("```", "~~~")):
            code = not code
            continue
        if code or not l.strip() or not nx.strip() or l.endswith(("  ", "\\")):
            continue
        if re.match(r"^\s*(#{1,6}\s|\||```|~~~)", l) or _BLOCK.match(nx):
            continue                       # headings / tables end themselves; list items start their own line
        if re.match(r"^\s*([-*+]|\d+[.)])\s", l) and not _BLOCK.match(nx):
            pass                           # continuation line of a list item: break too
        lines[i] = l.rstrip() + "  "
        n += 1
    return "\n".join(lines), n


def fix(md):
    md, a = linkify(md)
    md, b = hard_breaks(md)
    return md, a + b


def _layout(path, raw, obj):
    for kw in (dict(indent=2), dict(indent=1), dict(indent=4), dict(), dict(indent=2, separators=(",", ": ")),
               dict(indent=None, separators=(",", ":"))):
        for ea in (False, True):
            s = json.dumps(obj, ensure_ascii=ea, **kw)
            for tail in ("", "\n"):
                if s + tail == raw:
                    return dict(ensure_ascii=ea, **kw), tail
    return None, None


def process(path, dry):
    raw = open(path, encoding="utf-8").read()
    obj = json.loads(raw)
    lay, tail = _layout(path, raw, obj)
    recs = obj if isinstance(obj, list) else [obj]
    n = 0
    for r in recs:
        if isinstance(r, dict) and isinstance(r.get("raw_metadata"), str):
            r["raw_metadata"], k = fix(r["raw_metadata"])
            n += k
    if n and not dry:
        if lay is None:
            sys.exit(f"{path}: unknown JSON layout, not written")
        open(path, "w", encoding="utf-8").write(json.dumps(obj, **lay) + tail)
    return n


if __name__ == "__main__":
    dry = "--dry-run" in sys.argv
    files = sorted(glob.glob("*_files.json")) + sorted(glob.glob("metadata/**/*.json", recursive=True))
    tot = nf = 0
    for p in files:
        k = process(p, dry)
        tot += k
        nf += bool(k)
    print(f"{tot} URLs linked / line breaks added in {nf} files" + (" (dry run)" if dry else ""))

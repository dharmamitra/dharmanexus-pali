#!/usr/bin/env python3
"""Pali reflit – sourced overviews for every Pali text, following the Tibetan reflit recipe (see ../../../dharmanexus-tibetan/utils/reflit).

Steps (all in one file for the pilot):
  1. queries   – per text: acronym/number, PTS reference, title variants
  2. scan      – one pass over the reference-literature corpus (Aho-Corasick for titles, one regex for citations)
  3. aggregate – tier hits, extract ±5-line snippets, write research/<file>.md dossiers
  4. suttacentral – fetch suttaplex (blurb, titles, translations) and parallels for the text
  5. gemini    – one call per dossier -> overviews/<file>.json
  6. assemble  – metadata_preview/<file>-metadata.json (+ .md for reading)

Usage: pali_reflit.py [--files PA_dn_1,PA_mn_10 | all] [--steps scan,aggregate,dossier,gemini,assemble] [--model M] [--workers N]
       assemble writes into ../../metadata/ unless --out is given
"""
import argparse, collections, gzip, hashlib, json, os, re, sys, time
from multiprocessing import Pool
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading
import ahocorasick, requests
from unidecode import unidecode

HERE = os.path.dirname(os.path.abspath(__file__))
PA = os.path.join(HERE, '..', '..', 'PA_files.json')
CATS = {c['category']: c['displayName'] for c in json.load(open(os.path.join(HERE, '..', '..', 'PA_category-names.json')))}
LIT = os.environ.get('REFLIT_CORPUS', os.path.expanduser('~/code/sanskrit-dating/reference-literature'))
W = os.environ.get('REFLIT_WORK', os.path.join(HERE, 'work', 'full'))
SC_DATA = os.environ.get('SC_DATA', os.path.expanduser('~/data/sc-data'))   # git clone --depth 1 https://github.com/suttacentral/sc-data
SC_PAR = os.path.join(SC_DATA, 'relationship', 'parallels.json')
META_OUT = os.path.join(HERE, '..', '..', 'metadata')
SKIP = {'cleaned_skt-en-mono.txt', 'english_sc_monolingual.txt'}
SECRETS = os.path.expanduser('~/code/mitra-evaluation/.secrets.env')
MATCHES = os.environ.get('DN_MATCHES', os.path.expanduser('~/data/dharmanexus-data/matches'))
XLANG_MIN = int(os.environ.get('REFLIT_XLANG_MIN', '10'))   # min gemini_score for cross-language matches
REUSE_MIN_CHARS = 300   # ignore Pali-internal reuse partners sharing less than this (header boilerplate)
for d in ('research', 'overviews', 'metadata_preview', 'sc'):
    os.makedirs(os.path.join(W, d), exist_ok=True)

# ------------------------------------------------------------------ folding
_tr = {ord(c): "'" for c in "’‘ʼ`´ʹʻ′"}
_tr.update({0x2010: '-', 0x2011: '-', 0x2012: '-', 0x2013: '-', 0x2014: '-', 0x00ad: ''})
def fold(s):
    s = s.translate(_tr)
    if not s.isascii():
        s = unidecode(s)
    return s.lower()
def alnum(s):
    return re.sub(r'[^a-z0-9]+', '', fold(s))
def words(s):
    return ' ' + re.sub(r'\s+', ' ', re.sub(r'[^a-z0-9]+', ' ', fold(s))).strip() + ' '

# ------------------------------------------------------------------ queries
# acronym as used in textname -> alternative abbreviations found in the literature (folded)
ALIASES = {'dn': ['dn', 'd', 'digha', 'digha nikaya', 'dighanikaya'], 'mn': ['mn', 'm', 'majjhima', 'majjhima nikaya', 'majjhimanikaya'],
           'sn': ['sn', 's', 'samyutta', 'samyutta nikaya', 'samyuttanikaya'], 'an': ['an', 'a', 'anguttara', 'anguttara nikaya', 'anguttaranikaya'],
           'snp': ['snp', 'suttanipata', 'sutta nipata'], 'thag': ['thag', 'th', 'theragatha'], 'thig': ['thig', 'thi', 'therigatha'],
           'ja': ['ja', 'jat', 'jataka', 'j'], 'kd': ['kd', 'khandhaka'], 'patthana': ['patthana', 'patth', 'tikapatthana'],
           'dhp': ['dhp', 'dhammapada'], 'ud': ['ud', 'udana'], 'iti': ['iti', 'itivuttaka'], 'vv': ['vv', 'vimanavatthu'],
           'pv': ['pv', 'petavatthu'], 'mil': ['mil', 'miln', 'milindapanha'], 'kv': ['kv', 'kvu', 'kathavatthu'], 'cp': ['cp', 'cariyapitaka'],
           'bv': ['bv', 'buddhavamsa'], 'kp': ['kp', 'khp', 'khuddakapatha'], 'ds': ['dhs', 'ds', 'dhammasangani'], 'vb': ['vibh', 'vb', 'vibhanga'],
           'pp': ['pug', 'puggalapannatti'], 'ne': ['nett', 'nettippakarana'], 'pe': ['pet', 'petakopadesa'],
           'ps': ['ps', 'patis', 'patisambhidamagga'], 'tha-ap': ['ap', 'apadana', 'tha ap'], 'thi-ap': ['ap', 'thi ap'],
           'bu-vb': ['bu vb'], 'bi-vb': ['bi vb'], 'mnd': ['nidd', 'mnd', 'mahaniddesa'], 'cnd': ['cnd', 'culaniddesa']}
STRONG_MIN = 2   # acronyms of >=2 letters + number count as tier A; single letters only with a PTS roman volume (tier B)
RULES = {'pj': ['pj', 'parajika', 'paraj'], 'ss': ['ss', 'sanghadisesa', 'sanghad'], 'pc': ['pac', 'pc', 'pacittiya'], 'pd': ['pd', 'patidesaniya'],
         'np': ['np', 'nissaggiya', 'nis'], 'sk': ['sk', 'sekhiya'], 'as': ['as', 'adhikaranasamatha'], 'an': ['aniyata']}
GENERIC_TITLE_WORDS = {'sutta', 'suttam', 'suttanta', 'samyutta', 'vagga', 'nipata', 'jataka', 'therigatha', 'theragatha', 'khandhaka',
                       'vatthu', 'apadana', 'vara', 'vibhanga', 'nikaya', 'maha', 'cula', 'culla', 'pannasa', 'pali', 'kanda', 'katha', 'gatha', 'annatara', 'annatara', 'pathama', 'dutiya', 'tatiya'}
CUE = re.compile(r"\b(sutta|suttas|suttanta|nikaya|pts|pali|vinaya|khandhaka|samyutta|nipata|discourse|discourses|jataka|jatakas|tipitaka|canon|abhidhamma|patthana|therigatha|theragatha|commentary|atthakatha|agama|parallel)\b")

def build_queries(files):
    d = json.load(open(PA))
    q = {}
    for x in d:
        if x['filename'] not in files:
            continue
        tn = x['textname']
        m = re.match(r'^([A-Za-z-]+)\s+([\w.]+)', tn)
        acr, num = (m.group(1).lower(), m.group(2)) if m else (x['category'], '')
        pts = re.search(r'PTS\s+([A-Za-z]+)\s+([ivx]+)\s+(\d+)', tn)
        e = {'id': x['filename'], 'displayName': x['displayName'], 'textname': tn, 'category': x['category'],
             'category_name': CATS.get(x['category'], x['category']), 'collection': x['collection'],
             'link': x.get('link'), 'sc': x.get('link2'), 'acr': acr, 'num': num, 'filenr': x.get('filenr'),
             'pts': (pts.group(1), pts.group(2), pts.group(3)) if pts else None, 'titles': [], 'cores': [], 'idkeys': []}
        # citation keys: (alias, roman-or-'', number)
        rm = re.match(r'^([A-Za-z]+)(\d+)$', num)
        if rm:   # Vinaya rules: 'Pj1', 'Pc12' -> 'parajika 1', 'pac 12'
            for al in RULES.get(rm.group(1).lower(), [rm.group(1).lower()]):
                e['idkeys'].append((al, '', rm.group(2)))
            num = rm.group(2)
        elif re.match(r'^\d+(\.\d+)?$', num):
            for al in ALIASES.get(acr, [acr]):
                e['idkeys'].append((al, '', num))
        if e['pts']:
            for al in ALIASES.get(acr, [acr])[:2]:
                e['idkeys'].append((al, e['pts'][1], e['pts'][2]))
        # title variants
        full = alnum(x['displayName'])
        if len(full) >= 8:
            e['titles'].append(full)
        w = [t for t in words(x['displayName']).split() if t]
        # also split concatenated titles like apannakajataka
        core = [t for t in w if t not in GENERIC_TITLE_WORDS]
        if len(w) == 1:
            for g in sorted(GENERIC_TITLE_WORDS, key=len, reverse=True):
                if w[0].endswith(g) and len(w[0]) - len(g) >= 5:
                    core = [w[0][:-len(g)]]; break
        c = ''.join(core)
        if 5 <= len(c) and c != full:
            e['cores'].append(c)
        q[x['filename']] = e
    return q

# ------------------------------------------------------------------ scan
CIT_RE = re.compile(r"(?<![a-z0-9])(?P<al>[a-z][a-z ]{0,20}?)\.?\s*(?:(?P<rom>i|ii|iii|iv|v|vi)\.?,?\s*)?(?:nos?\.?\s*|pp?\.?\s*|no\s)?(?P<num>\d+(?:\.\d+)?)(?![\d.])")
Q = None; AC_FULL = None; AC_CORE = None; KEYS = None; ALSET = None

def init(q):
    global Q, AC_FULL, AC_CORE, KEYS, ALSET
    Q = q
    AC_FULL = ahocorasick.Automaton(); AC_CORE = ahocorasick.Automaton()
    KEYS = collections.defaultdict(list); ALSET = set()
    fulls = collections.defaultdict(list); cores = collections.defaultdict(list)
    for tid, e in q.items():
        for t in e['titles']: fulls[t].append(tid)
        for c in e['cores']: cores[' ' + c + ' '].append(tid)
        for k in e['idkeys']:
            KEYS[k].append(tid); ALSET.add(k[0])
    for a, v in fulls.items(): AC_FULL.add_word(a, (a, v))
    for a, v in cores.items(): AC_CORE.add_word(a, (a, v))
    for ac in (AC_FULL, AC_CORE):
        if len(ac): ac.make_automaton()
        else: ac.add_word('\x00\x00', ('', [])); ac.make_automaton()

def scan_file(fn):
    out = []
    try:
        with open(os.path.join(LIT, fn), encoding='utf-8', errors='replace') as f:
            for n, line in enumerate(f, 1):
                if not line.strip():
                    continue
                fl = fold(line)
                al = re.sub(r'[^a-z0-9]+', '', fl)
                for end, (a, v) in AC_FULL.iter(al):
                    out.append((fn, n, 'title_full', a, v, line[:400]))
                wl = ' ' + re.sub(r'\s+', ' ', re.sub(r'[^a-z0-9]+', ' ', fl)).strip() + ' '
                for end, (a, v) in AC_CORE.iter(wl):
                    out.append((fn, n, 'title_core', a.strip(), v, line[:400]))
                if any(ch.isdigit() for ch in fl):
                    for m in CIT_RE.finditer(wl):
                        al_ = m.group('al').strip()
                        if al_ not in ALSET:
                            # try last word(s) of the alias run (e.g. 'see dn' -> 'dn')
                            parts = al_.split()
                            al_ = next((' '.join(parts[i:]) for i in range(len(parts)) if ' '.join(parts[i:]) in ALSET), None)
                            if not al_:
                                continue
                        rom = m.group('rom') or ''; num = m.group('num')
                        if len(al_) < STRONG_MIN and not rom:
                            continue
                        key = (al_, rom, num)
                        if key in KEYS:
                            out.append((fn, n, 'cit', f"{al_} {rom} {num}".replace('  ', ' '), KEYS[key], line[:400]))
                        elif '.' in num and not rom and (al_, '', num.split('.')[0]) in KEYS and al_ in ('sn', 'an', 'samyutta', 'anguttara', 'samyutta nikaya', 'anguttara nikaya'):
                            out.append((fn, n, 'cit_sub', f"{al_} {num}", KEYS[(al_, '', num.split('.')[0])], line[:400]))
    except Exception as ex:
        sys.stderr.write(f'ERR {fn}: {ex}\n')
    return out

def scan(q):
    files = [f for f in sorted(os.listdir(LIT)) if f not in SKIP and os.path.isfile(os.path.join(LIT, f))]
    files.sort(key=lambda f: -os.path.getsize(os.path.join(LIT, f)))
    total = 0
    with Pool(96, initializer=init, initargs=(q,)) as pool, open(os.path.join(W, 'hits.jsonl'), 'w') as out:
        for res in pool.imap_unordered(scan_file, files, chunksize=4):
            for r in res:
                out.write(json.dumps(r, ensure_ascii=False) + '\n')
            total += len(res)
    print('hits', total, flush=True)

# ------------------------------------------------------------------ aggregate
TIER_W = {'A': 3.0, 'B': 1.0, 'C': 0.4}
MAX_SNIPS = 22; MAX_PER_FILE = 3; MAX_FILES = 12; CTX = 5
INFO = re.compile(r"\b(pts|pali|sanskrit|chinese|tibetan|agama|parallel\w*|translat\w*|edition|manuscript\w*|chapter\w*|century|composed|commentar\w*|atthakatha|title\w*|catalog\w*|fragment\w*|version\w*|recension|verses?|prose|quoted|cited|corresponds?|nikaya|sutta|jataka|khandhaka|vinaya|abhidhamma|discourse|structure|contents?|dated?|author\w*|attributed)\b")
SRC_BONUS = re.compile(r"(dictionary|encyclop|handbook|literature|history|survey|introduction|bibliograph|studies|journal|review|guide|pali|nikaya|sutta|theravada|buddhist|canon|jataka|vinaya|abhidhamma|hinuber|norman|malalasekera|bodhi|analayo|anālayo)", re.I)

def info_score(body):
    return min(8, len(set(m.group(1)[:5] for m in INFO.finditer(fold(body)))))

_cache = {}
def lines_of(fn):
    if fn not in _cache:
        with open(os.path.join(LIT, fn), encoding='utf-8', errors='replace') as f:
            _cache[fn] = f.read().split('\n')
        if len(_cache) > 200:
            _cache.pop(next(iter(_cache)))
    return _cache[fn]

def nice(fn):
    return re.sub(r'^cleaned_', '', fn)[:-4] if fn.endswith('.txt') else fn

def aggregate(q):
    raw = [json.loads(l) for l in open(os.path.join(W, 'hits.jsonl'))]
    freq = collections.Counter(a for fn, n, k, a, v, t in raw if not k.startswith('cit'))
    hits = collections.defaultdict(list)
    for fn, n, k, a, v, text in raw:
        fl = fold(text)
        for tid in v:
            if tid not in q:
                continue
            if k == 'cit':
                al = a.split()[0]
                tier = 'A' if len(al) >= STRONG_MIN else 'B'
            elif k == 'cit_sub':
                tier = 'B'
            elif k == 'title_full':
                tier = 'A'
            else:  # core
                if freq[a] >= 2500:
                    if not CUE.search(fl):
                        continue
                    tier = 'C'
                else:
                    tier = 'B'
            hits[tid].append((tier, fn, n, k, a, text))
    index = {}
    for tid, hs in hits.items():
        e = q[tid]
        fscore = collections.Counter(); byfile = collections.defaultdict(list)
        for tier, fn, n, k, a, text in hs:
            w = TIER_W[tier] * (1.4 if k == 'cit' else 1.0)
            fscore[fn] += w; byfile[fn].append((w, n, tier, k, a, text))
        files = sorted(fscore, key=lambda f: -(min(fscore[f], 20) + 5 * any(t == 'A' for _, _, t, _, _, _ in byfile[f]) + (3 if SRC_BONUS.search(f) else 0)))[:60]
        cands = []
        for fn in files:
            L = lines_of(fn)
            pre = sorted(byfile[fn], key=lambda x: (-x[0], -info_score(x[5]), x[1]))[:10]
            taken = []
            for w, n, tier, k, a, text in pre:
                if any(abs(n - m) <= CTX for m in taken):
                    continue
                taken.append(n)
                lo, hi = max(0, n - 1 - CTX), min(len(L), n + CTX)
                body = '\n'.join(x.strip() for x in L[lo:hi] if x.strip())
                if len(body) < 40:
                    continue
                sc = w + 0.6 * info_score(body) + (1.5 if SRC_BONUS.search(fn) else 0)
                cands.append((sc, fn, n, tier, k, a, body))
        cands.sort(key=lambda x: -x[0])
        snippets = []; seen = set(); perfile = collections.Counter()
        for sc, fn, n, tier, k, a, body in cands:
            if len(snippets) >= MAX_SNIPS:
                break
            if perfile[fn] >= MAX_PER_FILE or (fn not in perfile and len(perfile) >= MAX_FILES):
                continue
            h = hashlib.md5(re.sub(r'\W+', '', body.lower())[:300].encode()).hexdigest()
            if h in seen:
                continue
            seen.add(h); perfile[fn] += 1
            snippets.append({'file': fn, 'line': n, 'tier': tier, 'kind': k, 'match': a, 'text': body, 'score': round(sc, 1)})
        snippets.sort(key=lambda s: (s['file'], s['line']))
        tiers = collections.Counter(t for t, *_ in hs)
        index[tid] = {'n_hits': len(hs), 'tiers': dict(tiers), 'n_snippets': len(snippets), 'files': sorted({s['file'] for s in snippets})}
        e['_snippets'] = snippets
    for tid, e in list(q.items())[:20]:
        print(f"{tid}: {index.get(tid, {}).get('n_hits', 0)} hits, tiers {index.get(tid, {}).get('tiers')}, {len(e.get('_snippets', []))} snippets from {len(index.get(tid, {}).get('files', []))} files")
    json.dump(index, open(os.path.join(W, 'dossier_index.json'), 'w'), indent=1)
    print(f"{len(index)} texts with hits, {sum(1 for e in q.values() if e.get('_snippets'))} with snippets", flush=True)

# ------------------------------------------------------------------ suttacentral
_sc = None
def sc_index():
    """One in-memory index over the sc-data checkout: blurbs, names, translations, leaf/branch, acronym, PTS volpage."""
    global _sc
    if _sc is not None:
        return _sc
    import glob
    B = os.path.join(SC_DATA, 'sc_bilara_data')
    blurb = {}
    for f in glob.glob(os.path.join(B, 'root/en/blurb/*.json')):
        for k, v in json.load(open(f)).items():
            blurb[k.split(':', 1)[1]] = v
    name = {}; en = {}
    for f in glob.glob(os.path.join(B, 'root/*/*/name/**/*.json'), recursive=True):
        for k, v in json.load(open(f)).items():
            name[re.sub(r'^\d+\.', '', k.split(':', 1)[1])] = v
    for f in sorted(glob.glob(os.path.join(B, 'translation/en/*/name/**/*.json'), recursive=True)):
        for k, v in json.load(open(f)).items():
            en.setdefault(re.sub(r'^\d+\.', '', k.split(':', 1)[1]), v)
    authors = {k: v.get('name', k) for k, v in json.load(open(os.path.join(B, '_author.json'))).items()}
    tr = collections.defaultdict(set)
    for f in glob.glob(os.path.join(B, 'translation/*/*/**/*.json'), recursive=True):
        rel = os.path.relpath(f, os.path.join(B, 'translation')).split(os.sep)
        if 'name' in rel or 'blurb' in rel:
            continue
        m = re.match(r'(.+?)_translation-', os.path.basename(f))
        if m:
            tr[m.group(1)].add((rel[0], rel[1]))
    for f in glob.glob(os.path.join(SC_DATA, 'html_text/*/pli/**/*.html'), recursive=True):
        rel = os.path.relpath(f, os.path.join(SC_DATA, 'html_text')).split(os.sep)
        tr[os.path.basename(f)[:-5]].add((rel[0], 'legacy'))
    leaves = set(); branches = set()
    def walk(x):
        if isinstance(x, str): leaves.add(x)
        elif isinstance(x, dict):
            for k, v in x.items(): branches.add(k); walk(v)
        elif isinstance(x, list):
            for y in x: walk(y)
    for f in glob.glob(os.path.join(SC_DATA, 'structure/tree/**/*.json'), recursive=True):
        walk(json.load(open(f)))
    extra = {x['uid']: x for x in json.load(open(os.path.join(SC_DATA, 'structure/text_extra_info.json')))}
    _sc = {'blurb': blurb, 'name': name, 'en': en, 'authors': authors, 'tr': tr, 'leaves': leaves, 'branches': branches, 'extra': extra}
    print(f"sc-data index: {len(blurb)} blurbs, {len(name)} names, {len(tr)} texts with translations, {len(leaves)} leaves", flush=True)
    return _sc

def sc_fetch(e):
    uid = (e['sc'] or '').rsplit('/', 1)[-1]
    rec = {'uid': uid}
    if not uid:
        return rec
    ix = sc_index()
    if uid not in ix['name'] and uid not in ix['leaves'] and uid not in ix['branches']:
        rec['error'] = 'uid not in sc-data'
        return rec
    x = ix['extra'].get(uid, {})
    rec['suttaplex'] = {'uid': uid, 'acronym': x.get('acronym'), 'original_title': ix['name'].get(uid), 'translated_title': ix['en'].get(uid),
                        'blurb': ix['blurb'].get(uid), 'type': 'branch' if uid in ix['branches'] else 'leaf', 'volpages': x.get('volpage')}
    rec['translations'] = [{'lang': lang, 'author': ix['authors'].get(au, au) if au != 'legacy' else 'legacy translation(s) on SuttaCentral', 'title': None}
                           for lang, au in sorted(ix['tr'].get(uid, ()))]
    return rec

def sc_prefetch(q, workers=8):
    sc_index(); load_parallels()

_par = None; _uid2name = None
PREFIX = [('lzh-', 'Chinese Vinaya'), ('san-', 'Sanskrit Vinaya'), ('xct-', 'Tibetan Vinaya'), ('pli-tv-', 'Pali Vinaya'),
          ('sa-2', 'SĀ² (T 100)'), ('sa-3', 'SĀ³ (T 101)'), ('sa', 'SĀ'), ('ma', 'MĀ'), ('da', 'DĀ'), ('ea-2', 'EĀ² (T 150A)'), ('ea', 'EĀ'),
          ('sf', 'SF (Sanskrit fragment)'), ('sht', 'SHT (Sanskrit fragment)'), ('up', 'Up (Upāyikā, Tibetan)'), ('d', 'D (Tibetan Kangyur)'),
          ('t', 'T (Taishō)'), ('g', 'Gāndhārī'), ('avs', 'Avadānaśataka'), ('divy', 'Divyāvadāna'), ('lal', 'Lalitavistara'), ('mkv', 'Mahākarmavibhaṅga'),
          ('arv', 'Arthaviniścaya'), ('sag', 'Sagāthāvarga (Skt)'), ('uv', 'Udānavarga'), ('pdhp', 'Patna Dharmapada'), ('gdhp', 'Gāndhārī Dharmapada')]
LANG_OF = {'SĀ': 'lzh', 'MĀ': 'lzh', 'DĀ': 'lzh', 'EĀ': 'lzh', 'T': 'lzh', 'SF': 'san', 'SHT': 'san', 'Up': 'xct', 'D': 'xct', 'Gā': 'pgd'}

def load_parallels():
    global _par, _uid2name
    if _par is not None:
        return
    groups = json.load(open(SC_PAR))
    RANK = {'full': 0, 'segment': 1, 'resembling': 2, 'mentions': 3}
    best = {}   # (u, v) -> kind with highest precedence
    def base(x): return x.lstrip('~').split('#')[0]
    for g in groups:
        for key in ('parallels', 'resembling', 'mentions'):
            xs = g.get(key, [])
            for x in xs:
                for y in xs:
                    u, v = base(x), base(y)
                    if u == v:
                        continue
                    if key == 'mentions':
                        kind = 'mentions'
                    elif key == 'resembling' or x.startswith('~') or y.startswith('~'):
                        kind = 'resembling'
                    elif '#' in x or '#' in y:
                        kind = 'segment'
                    else:
                        kind = 'full'
                    if (u, v) not in best or RANK[kind] < RANK[best[(u, v)]]:
                        best[(u, v)] = kind
    _par = collections.defaultdict(list)   # uid -> [(other_uid, kind)]
    for (u, v), kind in best.items():
        _par[u].append((v, kind))
    _uid2name = {}
    for x in json.load(open(PA)):
        if x.get('link2'):
            _uid2name[x['link2'].rsplit('/', 1)[-1]] = f"{x['textname']} {x['displayName']}"

PALI_UID = re.compile(r'^(dn|mn|sn|an|kp|dhp|ud|iti|snp|vv|pv|thag|thig|tha-ap|thi-ap|bv|cp|ja|mnd|cnd|ps|ne|pe|mil|pli-tv|ds|vb|dt|pp|kv|ya|patthana)\d')
def is_pali(u):
    return u in _uid2name or bool(PALI_UID.match(u))

def uid_label(u):
    if u in _uid2name:
        return _uid2name[u]
    ix = sc_index()
    if u in ix['name']:
        acr = (ix['extra'].get(u) or {}).get('acronym')
        return f"{acr or u} {ix['name'][u]}".strip()
    for pre, name in PREFIX:
        if u.startswith(pre) and (len(u) == len(pre) or not u[len(pre)].isalpha() or pre.endswith('-')):
            rest = u[len(pre):].lstrip('-')
            return f"{name} {rest}".strip()
    return u

def children_of(uid):
    """all uids in the parallels table that belong to this SC node (itself, or sutta-level children of a branch)"""
    if uid in _par:
        return [uid]
    sep = r'\.' if uid[-1].isdigit() else r'(?:\d|\.)'
    rx = re.compile('^' + re.escape(uid) + sep)
    return [u for u in _par if rx.match(u)]

def sc_parallels(uid):
    load_parallels()
    kids = children_of(uid)
    per = {}   # child -> {full:[], resembling:[]}
    for k in kids:
        d = collections.defaultdict(list)
        for v, kind in _par.get(k, []):
            if v not in d[kind]:
                d[kind].append(v)
        if d:
            per[k] = d
    return kids, per

def sc_par_block(uid, branch):
    kids, per = sc_parallels(uid)
    out = []
    if not per:
        return out
    if not branch or (len(kids) == 1 and kids[0] == uid):
        d = per.get(uid) or next(iter(per.values()))
        if d.get('full'): out.append('- Full parallels (SC): ' + '; '.join(uid_label(v) for v in d['full'][:40]))
        if d.get('segment'): out.append('- Passage-level parallels (SC): ' + '; '.join(uid_label(v) for v in d['segment'][:40]))
        if d.get('resembling'): out.append('- Resembling parallels (SC): ' + '; '.join(uid_label(v) for v in d['resembling'][:40]))
        if d.get('mentions'): out.append('- Mentioned in (SC): ' + '; '.join(uid_label(v) for v in d['mentions'][:20]))
        return out
    # branch: summarise over the constituent suttas
    coll = collections.Counter(); ex = []
    for k in sorted(per, key=lambda x: [int(t) if t.isdigit() else t for t in re.split(r'(\d+)', x)]):
        for v in per[k].get('full', []):
            lab = uid_label(v); coll['Pali' if is_pali(v) else {'SA': 'SĀ', 'MA': 'MĀ', 'DA': 'DĀ', 'EA': 'EĀ'}.get(lab.split(' ')[0], lab.split(' ')[0])] += 1
            if len(ex) < 60 and not is_pali(v):
                ex.append(f"{uid_label(k)} ↔ {lab}")
    out.append(f"- This file contains {len(kids)} suttas; {len(per)} of them have parallels listed on SuttaCentral. Full parallels by collection: " +
               ', '.join(f"{c} ({n})" for c, n in coll.most_common(12)))
    if ex:
        out.append('- Examples of full non-Pali parallels (SC): ' + '; '.join(ex))
    return out

def sc_block(rec):
    if not rec.get('suttaplex'):
        return f"(no SuttaCentral record{': ' + rec['error'] if rec.get('error') else ''})\n"
    s = rec['suttaplex']; out = []
    out.append(f"- SuttaCentral uid: {s['uid']}   acronym: {s.get('acronym') or '-'}   type: {s.get('type')}")
    out.append(f"- Pali title (SC): {s.get('original_title')}   English title (SC, Sujato): {(s.get('translated_title') or '').strip() or '-'}")
    if s.get('blurb'):
        out.append(f"- SC blurb: {s['blurb'].strip()}")
    tr = rec.get('translations') or []
    if tr:
        by = collections.defaultdict(list)
        for t in tr:
            by[t['lang']].append(t['author'])
        en = ', '.join(a for a in dict.fromkeys(by.get('en', [])) if not a.startswith('legacy'))
        out.append(f"- Translations listed on SC: {len(tr)} in {len(by)} languages" + (f"; English: {en}" if en else ''))
    out += sc_par_block(s['uid'], s.get('type') != 'leaf')
    return '\n'.join(out) + '\n'

# ------------------------------------------------------------------ local (DharmaNexus) parallels
_names = {}
def text_name(fn):
    """displayName + textname for a DharmaNexus file id of any language."""
    lang = fn[:2]
    if lang not in _names:
        cat = {'PA': PA, 'ZH': '~/data/dharmanexus-chinese/ZH_files.json', 'SA': '~/data/dharmanexus-sanskrit/SA_files.json',
               'BO': '~/data/dharmanexus-tibetan/BO_files.json', 'EN': '~/data/dharmanexus-english/EN_files.json'}.get(lang)
        _names[lang] = {}
        if cat and os.path.exists(os.path.expanduser(cat)):
            for x in json.load(open(os.path.expanduser(cat))):
                _names[lang][x['filename']] = x
    x = _names[lang].get(fn)
    if not x:
        return fn
    dn, tn = x.get('displayName', ''), x.get('textname', '')
    return f"{dn} ({tn})" if tn and tn != dn else (dn or fn)

def seg_range(segs):
    a, b = segs[0].split(':', 1)[1], segs[-1].split(':', 1)[1]
    return f"§{a}" if a == b else f"§{a}–{b}"

def pali_reuse(tid, top=15):
    """Pali-internal text reuse from matches/pa/<tid>.ndjson.gz, aggregated per partner file."""
    p = os.path.join(MATCHES, 'pa', f'{tid}.ndjson.gz')
    if not os.path.exists(p):
        return None
    by = collections.defaultdict(lambda: {'n': 0, 'len': 0, 'best': None})
    for line in gzip.open(p, 'rt', encoding='utf-8'):
        m = json.loads(line)
        par = m['par_segnr'][0].split(':', 1)[0]
        if par == tid:
            continue
        d = by[par]; d['n'] += 1; d['len'] += m.get('par_length', 0)
        if d['best'] is None or m.get('par_length', 0) > d['best'][0]:
            d['best'] = (m.get('par_length', 0), seg_range(m['root_segnr']), seg_range(m['par_segnr']), round(m.get('score', 0), 2))
    by = {f: d for f, d in by.items() if d['len'] >= REUSE_MIN_CHARS}
    out = sorted(by.items(), key=lambda kv: -kv[1]['len'])
    return {'n_partners': len(by), 'n_matches': sum(d['n'] for d in by.values()),
            'top': [{'file': f, 'name': text_name(f), 'n': d['n'], 'chars': d['len'], 'longest': d['best']} for f, d in out[:top]]}

_xidx = None
def xlang_index(files):
    """Cross-language matches (pa->zh/sa/bo) for the given Pali files, read once from matches/multilingual/pa-*."""
    global _xidx
    if _xidx is not None:
        return _xidx
    _xidx = collections.defaultdict(lambda: collections.defaultdict(lambda: {'n': 0, 'len': 0, 'best': 0, 'ex': None}))
    global _xprom; _xprom = collections.defaultdict(set)   # partner -> set of Pali files it matches (promiscuity)
    md = os.path.join(MATCHES, 'multilingual')
    for fn in sorted(os.listdir(md)):
        if not fn.startswith('pa-'):
            continue
        for line in gzip.open(os.path.join(md, fn), 'rt', encoding='utf-8'):
            m = json.loads(line)
            gs = m.get('gemini_score', 0) or 0
            if m.get('filename') not in files:
                par = m['par_segnr'][0].split(':', 1)[0]
                if gs >= XLANG_MIN: _xprom[par].add(m['filename'])
                continue
            if gs < XLANG_MIN:
                continue
            par = m['par_segnr'][0].split(':', 1)[0]
            _xprom[par].add(m['filename'])
            d = _xidx[m['filename']][par]; d['n'] += 1; d['len'] += m.get('par_length', 0); d['best'] = max(d['best'], gs)
            if d['ex'] is None or gs > d['ex'][0]:
                d['ex'] = (gs, seg_range(m['root_segnr']), seg_range(m['par_segnr']))
    return _xidx

def xlang_parallels(tid, top=12):
    idx = xlang_index({tid}) if _xidx is None else _xidx
    by = idx.get(tid, {})
    # stock-phrase partners (matching hundreds of Pali files) only count with many matches here
    by = {f: d for f, d in by.items() if d['n'] >= 2 and (len(_xprom.get(f, ())) <= 150 or d['n'] >= 10)}
    out = sorted(by.items(), key=lambda kv: (-(kv[1]['n'] * kv[1]['best']), -kv[1]['len']))
    res = []; perlang = collections.Counter()
    for f, d in out:
        lang = f[:2].lower()
        if perlang[lang] >= max(4, top // 2):
            continue
        perlang[lang] += 1
        res.append({'file': f, 'lang': lang, 'name': text_name(f), 'n': d['n'], 'chars': d['len'], 'best': d['best'], 'ex': d['ex']})
        if len(res) >= top: break
    return res

def dn_block(tid):
    out = []
    pr = pali_reuse(tid)
    if pr is None:
        out.append('- Pali-internal text reuse: (no match file)')
    elif not pr['top']:
        out.append('- Pali-internal text reuse: none found')
    else:
        out.append(f"- Pali-internal text reuse (DharmaNexus): {pr['n_matches']} matching passages shared with {pr['n_partners']} other Pali texts. Largest partners (matched chars; longest shared passage as segment ranges of this text ↔ partner):")
        for t in pr['top']:
            b = t['longest']
            out.append(f"  - {t['name']} [{t['file']}]: {t['n']} matches, {t['chars']} chars; longest {b[1]} ↔ {b[2]} (similarity {b[3]})")
    xl = xlang_parallels(tid)
    if xl:
        out.append(f"- Cross-language matches (DharmaNexus, automatic alignment with a model-assigned plausibility score 0–100, only ≥{XLANG_MIN} listed):")
        for t in xl:
            out.append(f"  - [{t['lang']}] {t['name']} [{t['file']}]: {t['n']} matches, best score {t['best']}; e.g. {t['ex'][1]} ↔ {t['ex'][2]}")
    else:
        out.append(f'- Cross-language matches (DharmaNexus): none with score ≥{XLANG_MIN}')
    return '\n'.join(out) + '\n'

# ------------------------------------------------------------------ dossier
def write_dossiers(q):
    n = 0
    for tid, e in q.items():
        rec = sc_fetch(e)
        e['_sc'] = rec
        snips = e.get('_snippets', [])
        if not snips and not rec.get('suttaplex'):
            continue
        n += 1
        with open(os.path.join(W, 'research', f'{tid}.md'), 'w') as out:
            out.write(f"# {tid} — research dossier\n\n")
            out.write(f"- Text: {e['displayName']}   ID: {e['textname']}\n- Section: {e['category_name']} ({e['category']})   Collection: {e['collection']}\n")
            out.write(f"- SuttaCentral: {e['sc'] or '-'}   tipitaka.org: {e['link'] or '-'}\n\n")
            out.write("## SuttaCentral record (reliable; cite as [SuttaCentral])\n\n" + sc_block(rec) + '\n')
            e['_dn'] = dn_block(tid)
            out.write("## DharmaNexus computed parallels (local match data; cite as [DharmaNexus])\n\n" + e['_dn'] + '\n')
            out.write(f"## Passages found in the reference literature ({len(snips)} passages)\n\n")
            for i, s in enumerate(snips, 1):
                out.write(f"### [{i}] {nice(s['file'])}  (line {s['line']}, tier {s['tier']}, matched: {s['kind']} '{s['match']}')\n\n{s['text']}\n\n")
    print(f'{n} dossiers written', flush=True)

# ------------------------------------------------------------------ gemini
PROMPT = """You are a careful bibliographer of Pali Buddhist literature. Your job is to write a short, sourced one-page
overview of ONE text of the Pali Tipiṭaka / Pali literature ({category_name}), using ONLY the material given below.

INPUT
1. A catalog record (title, sutta number, PTS reference, section) — ground truth for the identity of the text.
2. A SuttaCentral record (blurb, titles, list of translations, parallels in other languages) — reliable; cite it with
   the tag [SuttaCentral].
3. A DharmaNexus block: automatically computed text-reuse matches between this text and other texts in the DharmaNexus
   corpora (Pali-internal reuse, and cross-language alignments with Chinese, Sanskrit or Tibetan texts). These are
   machine results, not curated parallels: cite them with the tag [DharmaNexus], describe them as "shares passages with"
   / "is aligned by DharmaNexus with", give the segment ranges when useful, and do not present them as established
   parallels unless SuttaCentral or a passage confirms the relation. Where SuttaCentral and DharmaNexus agree, say so.
4. Numbered passages [1], [2], ... found by keyword search (sutta numbers, PTS references, Pali titles) in a corpus of
   secondary literature and translations. Each passage is labelled with its source file, the matched string and a tier
   (A = sutta number / full title; B = short title or PTS page; C = generic name with a cue). Passages are noisy: they may
   concern a different text with the same or a similar name (a homonymous sutta in another Nikāya, a Jātaka with the same
   name, the commentary rather than the text, a Sanskrit or Chinese parallel), may be OCR-damaged, or may merely quote or
   cite the text without saying anything about it.

RULES
- Use only information that the passages, the SuttaCentral record, the DharmaNexus block or the catalog record actually support. Do NOT add facts
  from memory, even if you know them. Background knowledge may only be used for uncontroversial framing, never for
  specifics (dates, names, numbers of verses, editions).
- Verify identity before using a passage: sutta numbers / PTS references must match the record; titles must fit; if a
  passage is about another text, reject it and say why in rejected_passages. A bare PTS page number can be a citation of a
  passage of this text or of an adjacent text — use it only if the context fits.
- Every sentence with a substantive claim ends with one or more citation tags in square brackets, e.g. [Norman 1983] or
  [SuttaCentral; Anālayo 2011]. Define every tag in "sources" (one entry per source FILE, plus one for SuttaCentral if
  used, with the passage numbers used). A tag is "Author Year" when the file name gives both, otherwise "Author, Short
  title" or "Short title". Do not cite passages you did not use.
- Prefer facts of bibliographic value: what the text is and what it contains (structure, sections, main teaching, setting
  and interlocutors); its place in the collection; parallels in Chinese Āgamas, Sanskrit, Gāndhārī or Tibetan and extant
  fragments; the commentary (aṭṭhakathā) and later Pali literature that discusses it; questions of date, composition and
  transmission discussed by scholars; modern editions, translations and studies; how the text is used or cited (only if
  the passages show it). Always include a short paragraph on parallels and text reuse, combining SuttaCentral's
  curated parallels with the DharmaNexus matches (which sections of this text are shared with which texts).
- If the passages only quote the text or mention it in passing, say exactly that (which sources cite it and in what
  connection); the SuttaCentral blurb and parallels can still carry the overview. If nothing reliably concerns this text
  and there is no SuttaCentral record, set identification_confidence to "none".
- Write in clear scholarly English, markdown, 1–5 paragraphs, ideally 120–350 words, no headings. Use standard Pali
  diacritics (copy the catalog record's spellings).

OUTPUT: a single JSON object (no markdown fences) with exactly these fields:
{{
  "id": "{tid}",
  "identification_confidence": "high" | "medium" | "low" | "none",
  "english_title": string or null,
  "alternative_titles": [strings actually attested, may be empty],
  "overview_md": string,
  "sources": [{{"tag": "Author Year", "file": "<exact source file label as given in the passage header, or SuttaCentral, or DharmaNexus>",
               "citation": "Author, Title (year) — as far as the file name allows", "passages": [ints],
               "contribution": "one short phrase"}}],
  "rejected_passages": [{{"passages": [ints], "reason": "short"}}]
}}

=== CATALOG RECORD, SUTTACENTRAL RECORD, DHARMANEXUS MATCHES AND PASSAGES ===
{dossier}
"""

def api_key():
    k = os.environ.get('GEMINI_API_KEY') or os.environ.get('GOOGLE_API_KEY')
    if k:
        return k
    for line in open(SECRETS):
        if line.startswith(('GEMINI_API_KEY=', 'GOOGLE_API_KEY=')):
            return line.split('=', 1)[1].strip().strip('"\'')
    sys.exit('no GEMINI_API_KEY')

def call(model, prompt, key, max_tokens=8192):
    url = f'https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}'
    payload = {'contents': [{'parts': [{'text': prompt}]}],
               'generationConfig': {'temperature': 0.2, 'maxOutputTokens': max_tokens, 'responseMimeType': 'application/json'}}
    last = None
    for attempt in range(5):
        try:
            r = requests.post(url, json=payload, timeout=600)
            if r.status_code in (429, 500, 502, 503, 504):
                raise RuntimeError(f'HTTP {r.status_code}: {r.text[:200]}')
            r.raise_for_status()
            d = r.json(); cand = d['candidates'][0]
            text = ''.join(p.get('text', '') for p in cand.get('content', {}).get('parts', []) if not p.get('thought'))
            if not text.strip():
                raise RuntimeError(f'empty: {cand.get("finishReason")}')
            return text, d.get('usageMetadata', {})
        except Exception as ex:
            last = ex; time.sleep(min(60, 3 * 2 ** attempt))
    raise RuntimeError(f'gemini failed: {last}')

def parse_json(text):
    text = re.sub(r'^```(?:json)?\s*|\s*```$', '', text.strip())
    m = re.search(r'\{.*\}', text, re.S)
    if m: text = m.group(0)
    for fix in (lambda t: t, lambda t: re.sub(r'\\(?!["\\/bfnrtu])', r'\\\\', t)):
        try:
            return json.loads(fix(text), strict=False)
        except json.JSONDecodeError:
            continue
    raise ValueError('unparseable')

_log_lock = threading.Lock()
def gemini_one(tid, e, model, key, force):
        outp = os.path.join(W, 'overviews', f'{tid}.json')
        dp = os.path.join(W, 'research', f'{tid}.md')
        if not os.path.exists(dp):
            return tid, 'nodossier', None
        if os.path.exists(outp) and not force:
            try:
                json.load(open(outp)); return tid, 'skip', None
            except Exception:
                pass
        dossier = open(dp).read()
        prompt = PROMPT.format(category_name=e['category_name'], tid=tid, dossier=dossier)
        t0 = time.time(); text, usage = call(model, prompt, key)
        try:
            o = parse_json(text)
        except ValueError:
            text, usage = call(model, prompt + '\n\nYour previous answer was not valid JSON. Answer again with ONLY the JSON object.', key)
            o = parse_json(text)
        o['id'] = tid; o['_model'] = model; o['_usage'] = usage; o['_seconds'] = round(time.time() - t0, 1)
        json.dump(o, open(outp + '.tmp', 'w'), ensure_ascii=False, indent=1); os.replace(outp + '.tmp', outp)
        with _log_lock, open(os.path.join(W, 'gemini_calls.jsonl'), 'a') as f:
            f.write(json.dumps({'id': tid, 'model': model, 'usage': usage, 'seconds': o['_seconds'], 'ts': time.time()}) + '\n')
        return tid, 'ok', o.get('identification_confidence')

def gemini(q, model, force=False, workers=12):
    key = api_key()
    ids = [t for t in q if os.path.exists(os.path.join(W, 'research', f'{t}.md'))]
    print(f'{len(ids)} dossiers, model {model}, workers {workers}', flush=True)
    done = errs = 0; t0 = time.time()
    with ThreadPoolExecutor(workers) as ex:
        futs = {ex.submit(gemini_one, t, q[t], model, key, force): t for t in ids}
        for fut in as_completed(futs):
            t = futs[fut]
            try:
                _, st, conf = fut.result(); done += 1
                if st == 'ok' and done % 25 == 0:
                    print(f'[{done}/{len(ids)}] {t} {conf} ({time.time()-t0:.0f}s)', flush=True)
            except Exception as ex_:
                errs += 1; print(f'ERROR {t}: {ex_}', flush=True)
    print(f'gemini done {done}, errors {errs}, {time.time()-t0:.0f}s', flush=True)

# ------------------------------------------------------------------ assemble
NOTE = "*This overview was drafted by an AI model from the cited scholarship and SuttaCentral data and may contain errors; verify against the sources before relying on it.*"

def parallels_block(e, sc):
    out = []
    s = sc.get('suttaplex') or {}
    if s.get('uid'):
        out += [x.replace('(SC)', '(SuttaCentral)') for x in sc_par_block(s['uid'], s.get('type') != 'leaf')]
    pr = pali_reuse(e['id'], top=8)
    if pr and pr['top']:
        out.append("- Text reuse within the Pali corpus (DharmaNexus): " + '; '.join(f"{t['name']} ({t['longest'][1]} ↔ {t['longest'][2]})" for t in pr['top']))
    xl = xlang_parallels(e['id'], top=8)
    if xl:
        out.append("- Cross-language matches (DharmaNexus): " + '; '.join(f"{t['name']} [{t['lang']}]" for t in xl))
    return ("**Parallels and text reuse**\n\n" + '\n'.join(out) + '\n\n') if out else ''

def assemble(q, outdir):
    os.makedirs(outdir, exist_ok=True)
    load_parallels()
    stats = collections.Counter()
    for tid, e in q.items():
        p = os.path.join(W, 'overviews', f'{tid}.json')
        o = json.load(open(p)) if os.path.exists(p) else None
        sc = e.get('_sc') or sc_fetch(e); s = sc.get('suttaplex') or {}
        head = f"# {e['displayName']} ({e['textname']})\n\n"
        head += f"**ID:** {e['textname']}\n**Section:** {e['category_name']} ({e['category']})\n**Collection:** {e['collection']}\n"
        head += f"**Title (Pali):** {e['displayName']}\n"
        if s.get('translated_title'): head += f"**Title (English, SuttaCentral):** {s['translated_title'].strip()}\n"
        if o and o.get('english_title') and o['english_title'].strip() != (s.get('translated_title') or '').strip():
            head += f"**Title (English, from literature):** {o['english_title']}\n"
        if e.get('pts'): head += f"**PTS:** {e['pts'][0]} {e['pts'][1]} {e['pts'][2]}\n"
        if e.get('sc'): head += f"**SuttaCentral:** {e['sc']}\n"
        if e.get('link'): head += f"**tipitaka.org (CST):** {e['link']}\n"
        body = "\n## AI-generated Overview (from reference literature and SuttaCentral)\n\n"
        conf = o.get('identification_confidence') if o else None
        pb = parallels_block(e, sc)
        srcs = [x for x in (o or {}).get('sources', []) if x.get('file')]
        lit = [x for x in srcs if x['file'] not in ('SuttaCentral', 'DharmaNexus')]
        if o and conf != 'none' and o.get('overview_md', '').strip():
            body += NOTE + '\n\n' + o['overview_md'].strip() + '\n\n'
            if not lit:
                body += "*No discussion of this text was found in the reference-literature corpus; the overview relies on SuttaCentral and DharmaNexus data.*\n\n"
            body += pb
            if srcs:
                body += "**Sources**\n\n"
                for x in srcs:
                    body += f"- [{x.get('tag', '?')}] {x.get('citation', x['file'])}" + (f" — {x['contribution']}" if x.get('contribution') else '') + "\n"
                body += "\n"
            stats['with_overview'] += 1
        else:
            body += "No reliable discussion of this text was found in the reference-literature corpus.\n\n" + pb
            stats['no_overview'] += 1
        raw = head + body.rstrip('\n') + '\n'
        rec = {'displayName': e['displayName'], 'textname': e['textname'], 'category': e['category'], 'filenr': e.get('filenr'),
               'collection': e['collection'], 'filename': tid, 'raw_metadata': raw, 'reflit_confidence': conf,
               'reflit_sources': [x['file'] for x in srcs]}
        json.dump(rec, open(os.path.join(outdir, f'{tid}-metadata.json'), 'w'), ensure_ascii=False, indent=2)
    print('assembled:', dict(stats), '->', outdir, flush=True)

# ------------------------------------------------------------------ main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--files', default='all')
    ap.add_argument('--steps', default='scan,aggregate,dossier,gemini,assemble')
    ap.add_argument('--model', default=os.environ.get('REFLIT_GEMINI_MODEL', 'gemini-3.8-flash'))
    ap.add_argument('--workers', type=int, default=48)
    ap.add_argument('--out', default=META_OUT)
    ap.add_argument('--force', action='store_true')
    a = ap.parse_args()
    files = {x['filename'] for x in json.load(open(PA))} if a.files == 'all' else set(a.files.split(','))
    q = build_queries(files)
    json.dump(q, open(os.path.join(W, 'queries.json'), 'w'), ensure_ascii=False, indent=1)
    print(f"{len(q)} texts, {sum(len(e['titles']) + len(e['cores']) for e in q.values())} title variants, {sum(len(e['idkeys']) for e in q.values())} citation keys", flush=True)
    steps = a.steps.split(',')
    if 'scan' in steps: scan(q)
    if 'aggregate' in steps or 'dossier' in steps: aggregate(q)
    if 'dossier' in steps or 'assemble' in steps:
        xlang_index(set(q)); sc_prefetch(q)
    if 'dossier' in steps: write_dossiers(q)
    if 'gemini' in steps: gemini(q, a.model, a.force, a.workers)
    if 'assemble' in steps: assemble(q, a.out)

if __name__ == '__main__':
    main()

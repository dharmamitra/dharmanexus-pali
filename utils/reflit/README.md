# reflit (Pali) – sourced text overviews from the reference literature, SuttaCentral and DharmaNexus

Pali counterpart of `dharmanexus-tibetan/utils/reflit`. One script, `pali_reflit.py`, runs all steps and
writes `metadata/<filename>-metadata.json` for every file in `PA_files.json`.

| step | what it does |
|------|--------------|
| queries | per text: sutta acronym + number (with the abbreviations used in the literature, e.g. `DN 1`, `Dīgha Nikāya 1`; PTS `D i 1`; Vinaya rules `Pārājika 1`), full title and title core (diacritics folded) |
| scan | one Aho-Corasick + regex pass over `~/code/sanskrit-dating/reference-literature` (≈25 s on 96 cores) → `hits.jsonl` |
| aggregate | tiers (A: acronym+number / full title, B: PTS page / short title / sub-sutta number, C: generic name with cue), ±5-line snippets, ≤22 per text from ≤12 files |
| suttacentral | `api/suttaplex/<uid>` per file (blurb, titles, translations; cached in `work/full/sc/`); parallels from the bulk `sc-data/relationship/parallels.json` (`work/sc-parallels.json`), for whole-saṁyutta / nipāta files summarised over the constituent suttas |
| dharmanexus | Pali-internal text reuse from `~/data/dharmanexus-data/matches/pa/<file>.ndjson.gz` (per partner: matches, chars, longest shared passage as segment ranges; partners < 300 chars dropped) and cross-language alignments from `matches/multilingual/pa-*` (gemini_score ≥ 10; stock-phrase partners that match > 150 Pali files need ≥ 10 matches) |
| gemini | one call per dossier (`gemini-3.8-flash`, 12 workers, resumable) → `work/full/overviews/<file>.json`; must cite every claim, tags `[SuttaCentral]` and `[DharmaNexus]` for the two structured sources |
| assemble | header (titles, PTS, SuttaCentral and tipitaka.org links), `## AI-generated Overview`, caveat line, prose, `**Parallels and text reuse**` (SuttaCentral parallels + DharmaNexus reuse/alignments), `**Sources**` |

```
cd utils/reflit
python3 pali_reflit.py                                   # everything, all files
python3 pali_reflit.py --files PA_dn_1,PA_mn_10 --out /tmp/preview
python3 pali_reflit.py --steps gemini,assemble --force   # regenerate overviews
```

Notes
- Texts without a SuttaCentral link and without literature hits (mostly commentaries and Anya) get a "no reliable
  discussion found" record, still with the DharmaNexus parallels block when there is one.
- `reflit_confidence` and `reflit_sources` in each record allow filtering; `SuttaCentral` / `DharmaNexus` appear in
  `reflit_sources` as pseudo-files.
- Cost: ≈ 9k input + 4.5k output tokens per text on Flash ≈ $0.023, ≈ $100 for the whole collection.

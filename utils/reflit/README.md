# reflit (Pali) – sourced text overviews from the reference literature and SuttaCentral

Pali counterpart of `dharmanexus-tibetan/utils/reflit`. One script, `pali_reflit.py`, runs all steps and
writes `metadata/<filename>-metadata.json` for every file in `PA_files.json`.

| step | what it does |
|------|--------------|
| queries | per text: sutta acronym + number (with the abbreviations used in the literature, e.g. `DN 1`, `Dīgha Nikāya 1`; PTS `D i 1`; Vinaya rules `Pārājika 1`), full title and title core (diacritics folded) |
| scan | one Aho-Corasick + regex pass over `~/code/sanskrit-dating/reference-literature` (≈25 s on 96 cores) → `hits.jsonl` |
| aggregate | tiers (A: acronym+number / full title, B: PTS page / short title / sub-sutta number, C: generic name with cue), ±5-line snippets, ≤22 per text from ≤12 files |
| suttacentral | `api/suttaplex/<uid>` per file (blurb, titles, translations; cached in `work/full/sc/`); parallels from the bulk `sc-data/relationship/parallels.json` (`work/sc-parallels.json`), for whole-saṁyutta / nipāta files summarised over the constituent suttas |
| gemini | one call per dossier (`gemini-3.8-flash`, 12 workers, resumable) → `work/full/overviews/<file>.json`; must cite every claim, tag `[Catalog]` for the catalog record |
| assemble | header (titles, PTS, SuttaCentral and tipitaka.org links), `## AI-generated Overview`, caveat line, prose, `**Parallels and text reuse**` (catalog parallels), `**Sources**` |

```
cd utils/reflit
python3 pali_reflit.py                                   # everything, all files
python3 pali_reflit.py --files PA_dn_1,PA_mn_10 --out /tmp/preview
python3 pali_reflit.py --steps gemini,assemble --force   # regenerate overviews
```

Notes
- Texts without a SuttaCentral link and without literature hits (mostly commentaries and Anya) get a "no reliable
  discussion found" record.
- `reflit_confidence` and `reflit_sources` in each record allow filtering; `Catalog` appears in
  `reflit_sources` as a pseudo-file.
- Cost: ≈ 9k input + 4.5k output tokens per text on Flash ≈ $0.023, ≈ $100 for the whole collection.
- DharmaNexus match statistics are no longer used. Overviews cached in `work/full/overviews/` from before this
  change still cite them, so regenerate (`--steps dossier,gemini,assemble --force`) rather than re-assembling from the cache.

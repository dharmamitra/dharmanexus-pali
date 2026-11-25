# Machine translations

English machine translations of the texts in `segments/`, aligned back to their segments.
They are shown in the DharmaNexus reader under the original text.

```
translations/<model>/<filename>.json
```

One file per text, named like its segment file. It holds a JSON list with one unit per line,
in segment order:

```json
[
{"segs": ["SA_T06_sthtvbh:0", "SA_T06_sthtvbh:1"], "en": "…", "model": "mitra-v4", "translated_at": "2026-11-25T05:19:12Z"},
{"segs": ["SA_T06_sthtvbh:2"], "en": "…", "model": "mitra-v4", "translated_at": "2026-11-25T05:19:12Z"}
]
```

- `segs`: the segments the English covers. Usually one. Several when the alignment could not
  split the translation between them.
- `translated_at`: when the unit was translated (UTC). A text translated again gets a new file
  and new times, so the history shows what changed and when.
  - The first units of the 2026-11 run were written before times were recorded. Their times
    are estimates, to within a few minutes.
- `model`: the model that produced the unit. Its folder holds all its files.

## mitra-v4

- **Model:** the Dharmamitra MT model `v4` (Qwen3.5-9B fine-tune).
- **Serving:** vLLM with fp8 weights and fp8 KV cache, temperature 0.2.
- **Chunking:** texts are translated in windows of 1–3 segments. A window closes at a sentence
  end, and at 400 characters (150 for Chinese).
- **Alignment:** each window's English is split back onto its segments with the
  `mitra-qwen35-2b-embedder-sentence-alignment` model. The search is exact within the window.
- **Quality:** these are unreviewed machine translations. In the D4064 pilot review (MQM), the
  main errors were in Tibetan case-particle parsing.

Loaded into the DharmaNexus database by `invoke load-machine-translations --lang=<lang>`
(dharmanexus-postgresql).

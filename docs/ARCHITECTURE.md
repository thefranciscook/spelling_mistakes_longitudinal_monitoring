# Architecture

## Components

```
┌────────────────────────┐   Native Messaging (stdio, Phase 2)   ┌──────────────────────────────┐
│ Chrome extension (MV3) │ ────────────────────────────────────▶ │ Python service               │
│  per-site adapters     │   word events, provenance, speed      │  spelling_observatory        │
│  status + pause        │                                        │   pipeline · store · analysis│
└────────────────────────┘                                        │                              │
                                                                  │  ChatGPT importer (Phase 1)  │
┌────────────────────────┐   HTTP on 127.0.0.1 + token (Phase 3)  │                              │
│ React dashboard        │ ◀───────────────────────────────────── │  report/build.py results     │
└────────────────────────┘                                        └──────────────┬───────────────┘
                                                                                 │
                                                       SQLCipher DB (key in macOS Keychain)
                                                       Ollama on 127.0.0.1 (optional)
```

Phase 1 implements the Python service and a CLI. The extension and dashboard will reuse the
same pipeline and the same `report.build` computations.

## Analysis pipeline (`classify/pipeline.py`)

```
text
 └─ text/segment.py      code fences, code/quote/table lines, paste typography, inline URLs,
 │                       paths, handles → paragraphs → sentences → tokens (+ exclusion counts)
 └─ text/language.py     per paragraph and sentence: lingua(EN vs HU) AND lexical vote
 └─ importer callback    quoted-assistant sentences (historical only)
 └─ lexicon/lexicon.py   per token: excluded (reason) | valid word | non-word
 └─ classify/candidates  orthographic (SymSpell) + phonological (CMU index of G2P key)
 │                       + segmentation → noisy-channel ranking → deterministic | pending
 └─ classify/llm.py      (optional, batched) closed-set choice among candidates
 └─ classify/classifier  A / B / C rules on (observed, intended)
 ▼
TextAnalysis(words[WordObs], excluded Counter, unknown Counter)
```

Heavy resources load once per process (about 12 s): three English Hunspell dictionaries plus
Hungarian, an 82k-word frequency list, CMUdict with a phonetic index, and the G2P weights.
Throughput on synthetic data is about 200 messages per second without the LLM.

## Storage (`store/schema.sql`)

| Table | Content |
|---|---|
| `classifier_versions` | fingerprint and JSON of every component that can change output |
| `imports` | each import run: source hash, version, statistics |
| `sessions` | Phase 2 typing sessions |
| `texts` | one row per message or submission: source-key hash, provenance, stage, domain, time, local date, input source and confidence, wpm, counts, exclusion reasons |
| `word_observations` | one row per eligible word: observed, intended, error flag, category, resolution, reason, edit distance and operations, phonetic match, candidates (errors only) |
| `unknown_tokens` | aggregated out-of-vocabulary tokens for vocabulary review |
| `cohorts`, `cohort_words` | frozen mastered-word cohorts |
| `llm_cache` | hash(model, prompt, token, context) → answer; no context stored |

Not stored:

- raw message text;
- sentence context;
- raw keystroke streams;
- word order. Rows are shuffled within each text before insertion, so row ids cannot
  rebuild a message.

Deduplication uses `UNIQUE(source_key, stage, classifier_version_id)`, where the source key
is the SHA-256 of the ChatGPT message id.

## Versioning

- `RULES_VERSION` (in `__init__.py`) changes whenever rule logic changes.
- `Analyzer.components()` collects every output-relevant input. Its hash is the classifier
  fingerprint, and a new fingerprint means a new `classifier_versions` row.
- Every query is scoped by `classifier_version_id`. The report shows which version it uses
  and lists any other versions it left out.
- `SCHEMA_VERSION` guards the database layout.

## Security and privacy

- **Local only.**
  - The LLM client refuses any endpoint that is not on the loopback interface, and it
    bypasses proxy settings.
  - No telemetry.
  - The only download is the pinned, checksum-verified dictionaries, at `setup`.
- **Encryption at rest.**
  - SQLCipher, with a random 256-bit key in the macOS Keychain.
  - There is one Keychain entry per database path, so a demo database never touches the
    real key.
  - The application directory is mode 0700 and the database and exports are 0600.
- **Minimisation.** Only word-level data is kept (see Storage).
- **Control.**
  - `export` writes all data to JSON.
  - `delete-all` removes the data directory and the Keychain key.
  - `exclude` removes stored observations of a word retroactively.
- **Phase 2 extension.**
  - Explicit per-site permission, with `chatgpt.com` as the default allowlist.
  - Never active in incognito windows or in password, payment or one-time-code fields.
  - A visible badge and a pause control.

## Configuration

`config.toml` sections:

| Section | Holds |
|---|---|
| `storage` | encryption setting |
| `language` | language-filter thresholds |
| `classifier` | classifier thresholds |
| `llm` | enabled flag, loopback endpoint, model |
| `mastered` | cohort thresholds |
| `report` | timezone, baseline and recent windows, minimum sample, bootstrap iterations |

Thresholds in `language` and `classifier` are part of the classifier fingerprint.

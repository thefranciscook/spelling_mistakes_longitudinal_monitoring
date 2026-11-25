# Implementation plan

This document answers the pre-implementation items in §19 of the specification: the repository
structure, the phased plan, the evaluation of existing libraries, and the limitations of
automatic intended-word inference and browser text capture.

## 1. Repository structure

```
README.md
docs/
  PLAN.md            this file
  METHODOLOGY.md     research design, taxonomy, decision rules, statistics, biases
  ARCHITECTURE.md    components, data flow, schema, versioning, security
service/             Phase 1: Python analysis engine (later also the Native Messaging host)
  pyproject.toml
  src/spelling_observatory/
  scripts/make_synthetic_export.py
  tests/
extension/           Phase 2: Chrome MV3 extension (TypeScript), not yet created
dashboard/           Phase 3: React + TypeScript dashboard, not yet created
```

The Python service is the single source of truth for linguistic analysis. The extension only
captures and pre-aggregates typing evidence, and the dashboard only reads results. This keeps
all classification logic in one versioned, testable place.

## 2. Phases

### Phase 1: offline analysis engine (done)

- Text pipeline: structural filters (code, quotes, tables, pasted typography, URLs,
  identifiers), English vs Hungarian identification, tokenisation.
- Lexicon: US, Canadian and British Hunspell dictionaries, SymSpell frequency list, built-in
  shorthand, informal-contraction and technical lists, and a personal exclusion file.
- Classifier: three candidate channels (orthographic, phonological, segmentation),
  deterministic noisy-channel resolution with abstention, an optional local LLM for
  context-dependent cases, and A/B/C decision rules.
- ChatGPT export importer: user messages only, provenance flags, deduplication, idempotent
  re-import.
- SQLite/SQLCipher storage with classifier-version separation.
- Metrics: weekly, monthly and rolling 30/90-day rates; a fixed baseline vs recent comparison
  with day-clustered bootstrap intervals; frozen mastered-word cohorts; difficult-word tracking.
- Terminal and static HTML retrospective report.
- 68 automated tests.

**Exit criterion for Phase 1.** Run the importer on your real export. Then read the "Examples
by category" and "Data quality" sections of the report and decide whether the observations
are consistent enough to justify Phase 2. Things to look at:

- the share of errors that end up in C;
- whether the B examples really are sound-alike misspellings;
- the exclusion breakdown;
- the frequent unknown tokens (`observatory unknowns`), which show personal vocabulary to
  add with `observatory exclude`.

### Phase 2: Chrome extension

1. MV3 extension with `optional_host_permissions`. A site is monitored only after you grant it
   explicitly; the default allowlist is just `chatgpt.com`.
2. A ChatGPT adapter for the composer (a ProseMirror `contenteditable`). It listens to
   `beforeinput`/`input`/`compositionstart`/`compositionend`/`paste`/`drop`/`keydown`.
3. Word-boundary state machine. A word is emitted once, when the caret leaves it (space,
   punctuation or blur). The *initial* form is the letters as first completed, and the
   *final* form is the word at submit time. Backspaces inside a word mark it as corrected.
   This avoids counting every intermediate keystroke state.
4. Provenance per word: keyboard, paste, drop, dictation (`insertText`/`insertReplacementText`
   events without key events, or multi-word insertions) or programmatic (DOM mutations
   without input events). Each carries a confidence value.
5. Typing speed from inter-key intervals within words (no raw keystroke log is kept), plus
   correction frequency.
6. Native Messaging to the Python host. The extension sends batches of
   `{domain, ts, stage, words[{initial, final, corrected, provenance, wpm}], sentence_context}`.
   Context is processed in memory and discarded.
7. Status badge, a pause toggle, and automatic suspension in incognito windows and on
   `password`, `email`, `tel`, `number`, credit-card (`autocomplete="cc-*"`) and
   one-time-code fields.
8. Fixtures for corrected typing errors, duplicate events, IME composition, paste and
   dictation (spec §15). These need the extension's event model and arrive with it.

### Phase 3: dashboard and continuous operation

- A small loopback-only HTTP API in the Python service (bound to 127.0.0.1, random per-install
  bearer token), serving the same computations as `report/build.py`.
- React/TypeScript dashboard with three views (Overview, Mastered words, Settings and data
  quality), filters by domain and period, and per-word drill-down.
- A launchd agent (`~/Library/LaunchAgents/…plist`) for the HTTP service. The Native Messaging
  host is launched by Chrome on demand.

### Phase 4: refinement

Use the C-category reasons and unknown-token lists from real data to improve precision. Any
rule change bumps `RULES_VERSION`; historical data is re-imported under the new version, so
comparisons stay within a single version. The taxonomy stays at A/B/C unless you explicitly
approve a change.

## 3. Library evaluation

| Need | Chosen | Alternatives considered | Reason |
|---|---|---|---|
| Spell-check dictionary | **Hunspell dictionaries (SCOWL en_US, en_CA, en_GB; Magyar Ispell hu)** via **spylls** (pure-Python Hunspell) | pyenchant (needs system libenchant), pyspellchecker (single list, no affix rules), SymSpell list alone | Real affix handling; the union of three varieties makes `colour`/`color`, `centre`/`center` and `analyse`/`analyze` all correct; pure Python installs cleanly on macOS; ~50 µs per lookup |
| Candidate generation | **symspellpy** (frequency list of Google Books ∩ SCOWL) | Norvig-style edits, pyspellchecker | Fast (sub-ms) lookups within edit distance 2; frequency counts serve as the prior |
| Orthographic distance | Own optimal-string-alignment Damerau–Levenshtein with an operation trace | rapidfuzz, jellyfish | Needs the *operations* (which letter, where) for keyboard geometry, and these libraries return only distances. About 40 lines |
| Pronunciation of real words | **CMU Pronouncing Dictionary** (`cmudict` package) | — | Standard; all variants used |
| Pronunciation of misspellings | **g2p_en checkpoint** (GRU seq2seq, Apache-2.0) run by a small NumPy re-implementation | Importing g2p_en directly; espeak-ng/phonemizer; Double Metaphone | Importing g2p_en triggers NLTK downloads at import time, so only its weights are reused. espeak needs a system binary. Metaphone is too coarse for vowel-driven errors (`seperate`) |
| Language identification | **lingua** restricted to English vs Hungarian, plus a lexical vote | fastText `lid.176` (open set, weaker on short informal text, model download), langdetect (non-deterministic) | Offline, deterministic, ships its models in the wheel; much better on short chat sentences when restricted to the two languages actually used |
| Contextual inference | **Ollama** on loopback with a JSON-schema response | llama.cpp server, MLX | Easiest pinned, offline setup on macOS; structured output; the model choice is configurable and fingerprinted |
| Storage | **SQLite**, encrypted with **SQLCipher** (`sqlcipher3-wheels`), key in macOS Keychain (`keyring`) | Plain SQLite on FileVault | Database-level encryption with an OS-protected key; macOS arm64 wheels exist for Python 3.12/3.13 |

No custom neural network is trained.

## 4. Limitations to keep in mind

### Automatic intended-word inference

1. **Real-word errors are invisible.** `wether` for `whether`, `there` for `their` and
   `form` for `from` are valid words, so they are never flagged. This lowers recall for every
   category, and especially for B, since many phonological errors produce real words. The
   loss is roughly constant over time, so trends stay interpretable, but absolute rates are
   underestimates.
2. **Without context, many short misspellings are genuinely ambiguous.** `nite` is one edit
   from `site`, `note`, `nice` and `nine`; `erros` is one edit from `error` and `errors`. The
   engine abstains (category C, intended unknown) unless the LLM is enabled.
3. **The LLM sees candidates, so it cannot recover a word outside the candidate set.** Its
   self-reported certainty is never used. It can be wrong in a fluent way, so its answers
   only *choose*; the A/B/C rules still decide the category deterministically.
4. **Recall is asymmetric.** Phonological errors with edit distance ≥ 3 can still be found
   through the sound-alike channel, but mechanical errors with distance ≥ 3 cannot. Both rates
   are compared with their own baselines, so this does not bias trends.
5. **G2P on misspellings is imperfect.** If the model reads a misspelling differently from a
   human, B is missed (`definately` → C). This affects precision less than recall.
6. **Unknown tokens with no near candidate are not counted as errors**, so a heavily garbled
   word is treated like personal vocabulary. Precision over recall.

### Historical (submitted-text) data

Keystroke timing, corrected errors and dictation are mostly unknowable. Pasted text is caught
only by heuristics: typographic characters, overlap with earlier assistant replies, code and
quote structure, and message length. A Chrome spellcheck underline may have led you to fix a
word before sending. All of this is why historical data is only ever compared with
prospective *submitted-stage* data.

### Browser text capture (Phase 2)

- Rich editors (ProseMirror, Draft.js, Google Docs' canvas renderer) do not expose edits the
  same way. Google Docs in particular does not use a normal `contenteditable`, so it needs its
  own adapter or may stay unsupported.
- macOS dictation and some IMEs insert text through `insertText` or composition events that
  can look like typing. Dictation detection is probabilistic.
- Autocorrect or extension-based correction (Grammarly etc.) can change words without key
  events. These are flagged as programmatic where detectable and otherwise reduce the
  observed error rate.
- Sensitive-field detection is heuristic. The explicit per-site allowlist is the primary
  protection.

## 5. Decisions you may want to revisit

- **Baseline window.** By default it is the first 365 days of imported data, and the recent
  window is the last 90 days. If you know roughly when you moved to Canada, set
  `report.baseline_start` and `report.baseline_end` to a period before the change you
  suspect.
- **LLM on or off.** Off by default. Turning it on raises coverage but adds a model
  dependency. Re-import after changing it.
- **Precision rules.** Doubled-letter changes (`ocasion`), vowel reorderings (`recieve`) and
  cases that fit both explanations (`goverment`) are deliberately C. These are classic
  spelling-knowledge errors. If you would rather count some of them as B, that is a taxonomy
  decision for you to make explicitly.

# Personal Spelling Observatory

A local, single-user research instrument for one question: **is my spelling of words I used to
spell correctly getting worse over time, and are the new errors mostly phonologically
plausible (`century` → `sentury`) rather than ordinary typing slips (`century` → `centruy`)?**

It is an N-of-1 observational study tool, **not a medical or diagnostic tool**. It reports
rates, sample sizes and uncertainty. It does not report causes, risk scores or diagnoses.

Everything runs on your own computer. Nothing is sent anywhere. The only network access is
a one-time `observatory setup` download of four pinned Hunspell dictionaries (checksum-verified),
plus model downloads if you choose to enable the optional local LLM.

## Status

| Phase | Scope | Status |
|---|---|---|
| 1 | Offline analysis engine, ChatGPT history importer, SQLite schema, mastered-word tracking, retrospective report | **Implemented** (this repository) |
| 2 | Chrome extension (typing capture, provenance, Native Messaging) | Planned, see [docs/PLAN.md](docs/PLAN.md) |
| 3 | React dashboard, launchd service | Planned |
| 4 | Precision refinement from observed failure patterns | Planned |

## Quick start (macOS)

Requires Python 3.11+ (`brew install python@3.12`).

```bash
cd service
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[secure,dev]'
```

```bash
observatory setup
```

`setup` creates `~/Library/Application Support/SpellingObservatory/`: `config.toml`,
`exclusions.txt`, the encrypted database (SQLCipher; the key is generated and stored in the
macOS Keychain) and the dictionaries.

Import your ChatGPT history. In ChatGPT, open Settings → Data controls → Export data, then
download the zip from the email.

```bash
observatory import-chatgpt ~/Downloads/chatgpt-export.zip
```

```bash
observatory report --html ~/Desktop/spelling-report.html
```

Re-running an import is safe: messages already stored for the current classifier version
are skipped.

### Optional: local LLM for ambiguous words

Without an LLM, misspellings with several plausible intended words (e.g. `nite`: night?
nice? note?) are kept as *unclassified*. With [Ollama](https://ollama.com) installed:

```bash
ollama pull qwen2.5:7b-instruct-q4_K_M
```

Then set `enabled = true` under `[llm]` in `config.toml`. The model is only called on the
loopback interface, only for ambiguous words, and only to choose among listed candidates.
Turning it on creates a new classifier version, so re-import your export afterwards.

### Try it without your own data

```bash
python scripts/make_synthetic_export.py /tmp/synthetic.json
```

```bash
SPELLING_OBSERVATORY_HOME=/tmp/obs-demo observatory setup
```

```bash
SPELLING_OBSERVATORY_HOME=/tmp/obs-demo observatory import-chatgpt /tmp/synthetic.json
```

```bash
SPELLING_OBSERVATORY_HOME=/tmp/obs-demo observatory report --html /tmp/obs-demo/report.html
```

The synthetic writer's phonological error rate rises in its second year while its typing-slip
rate stays flat; the report should show exactly that.

## Commands

| Command | Purpose |
|---|---|
| `observatory setup` | Create config, database and key; download pinned dictionaries |
| `observatory import-chatgpt PATH [--no-llm]` | Import a ChatGPT export (.zip, folder or `conversations.json`) |
| `observatory report [--html FILE] [--domain D] [--baseline-start/--baseline-end]` | Summary in the terminal, optional HTML report |
| `observatory classify "some text"` | Show every decision the engine makes for a piece of text |
| `observatory exclude WORD...` | Add personal vocabulary; also removes already-stored observations |
| `observatory unknowns` | Frequent out-of-vocabulary tokens (candidates for `exclude`) |
| `observatory cohort NAME START END` | Freeze a mastered-word cohort for a custom baseline |
| `observatory versions` / `prune` | List classifier versions / delete all but one |
| `observatory export FILE` | Export all stored data as JSON |
| `observatory delete-all --yes` | Delete database, config, resources and the Keychain key |

## Tests

```bash
cd service && python -m pytest -q
```

The tests use synthetic examples, known misspellings and invariants. They check that the
software is correct and consistent. They do **not** establish real-world classification
accuracy or clinical validity.

## Documentation

- [docs/PLAN.md](docs/PLAN.md): implementation plan, repository structure, library evaluation, known limitations
- [docs/METHODOLOGY.md](docs/METHODOLOGY.md): research design, error taxonomy, decision rules, statistics, biases
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md): components, data flow, storage schema, versioning, security

## Repository layout

```
service/                     Python analysis engine (Phase 1)
  src/spelling_observatory/
    text/        segmentation, structural filters, language identification
    lexicon/     dictionaries, frequency list, built-in exclusion lists
    classify/    candidates, orthography, phonology (G2P), A/B/C rules, local LLM, pipeline
    importers/   ChatGPT export importer
    store/       SQLite / SQLCipher schema and access
    analysis/    rates, windows, bootstrap, mastered-word cohorts
    report/      terminal summary and static HTML report
  scripts/       synthetic export generator
  tests/
docs/
```

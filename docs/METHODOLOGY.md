# Research methodology

## 1. Design

- **Type:** N-of-1 longitudinal observational study of naturally produced text.
- **Participant:** one adult, Hungarian L1, highly proficient English L2 learned mainly
  through reading. Several years of immersion in spoken Canadian English.
- **Primary question:** Is spelling of previously mastered English words deteriorating, and
  is the deterioration disproportionately made of phonologically plausible errors, once
  typing behaviour and writing environment are taken into account?
- **Scope of inference:** whether a change is present and measurable in this person's
  writing. The design cannot identify causes. Strategy change, attention, fatigue, tooling
  and neurological factors are all compatible with the same observations.

## 2. Data sources and comparability

| Source | Provenance | Stage | Available signals |
|---|---|---|---|
| ChatGPT export (Phase 1) | `historical_submitted` | `submitted` | Text as sent, timestamp |
| Chrome extension (Phase 2) | `prospective` | `initial`, `final`, `submitted` | Plus typing speed, corrections, input provenance |

Every analysis is restricted to one provenance and stage. Historical submitted-text error
rates are never compared with prospective *initial-typing* rates. They are only compared
with prospective submitted-stage rates. Writing environments (domains) are stored separately
and are not pooled by default.

## 3. Unit of analysis and eligibility

The unit is an **eligible word occurrence**: a token in confidently English text that passed
every filter below. Correct words are stored as well as errors, because a rate of change
needs both.

Filters, in pipeline order. Each excluded word is counted by reason.

1. **Message level** (historical): hidden or system messages, non-text content, dictated
   messages (voice metadata or audio-transcription parts), messages over 4,000 characters
   (likely pastes), and exact re-submissions within a conversation.
2. **Structure:** fenced and inline code, code-like lines, quoted lines (`>`), tables, email
   headers, Markdown headings, URLs, e-mail addresses, file paths, @handles and #tags.
3. **Paste typography:** paragraphs with curly quotes, `…`, bullets or non-breaking spaces.
   Chrome text fields on macOS do not produce these from ordinary typing.
4. **Quoted assistant text:** sentences where at least half of the word 6-grams appear in
   *earlier* assistant replies in the same conversation.
5. **Language:** a sentence is English only if (a) lingua, restricted to English vs
   Hungarian, gives English ≥ 0.5, **and** (b) at least 60% of its tokens are English words
   and at most 20% are Hungarian words. Paragraphs where more than 25% of words are in
   non-English sentences are excluded as mixed. Sentences with fewer than 3 tokens follow
   their paragraph. The keyboard layout is not used.
6. **Token:** numbers and identifiers, ALL-CAPS acronyms, camelCase, elongations (`sooo`),
   shorthand (`w`, `ppl`, `bc`, `tbh`, `thru`, ...), apostrophe-less contractions (`dont`),
   technical vocabulary, personal exclusions, proper names (capitalised mid-sentence or
   capital-only in the dictionary), Hungarian words, non-words shorter than 3 letters, and
   unknown tokens with no candidate within the search space.

**Invariant:** no filter looks at whether a word is spelled correctly, except the last,
which only *drops* tokens the engine cannot interpret. Error-free text is never excluded for
being error-free. The test `test_error_free_text_is_never_excluded_for_being_error_free`
checks this. Exact-duplicate removal is used instead of near-duplicate removal for the same
reason: near-duplicate removal would drop error-free copies of a sentence and keep copies
with a new typo.

## 4. Error detection and intended-word inference

A token is a **candidate error** when it is not valid in any of the US, Canadian or British
Hunspell dictionaries and not in the SCOWL frequency list. Regional variants are therefore
never errors.

Intended-word candidates come from three channels:

| Channel | Source | Example |
|---|---|---|
| Orthographic | SymSpell, edit distance ≤ 2 | `centruy` → century |
| Phonological | words whose CMU pronunciation equals the G2P reading of the token | `nite` → night (distance 3) |
| Segmentation | split into two frequent words | `alot` → a lot |

Candidates are ranked by a simple noisy-channel score, log(corpus frequency) − log(1000) × edit
distance. **Error-type features (keyboard adjacency, phonetic match) are deliberately not
used to rank candidates**, so choosing the intended word does not presuppose the category.

The intended word is accepted deterministically only if:

- the best candidate beats the runner-up by a factor of ≥ 20, **and**
- no frequent sound-alike word disagrees with it (channel conflict).

Otherwise the item is ambiguous. With the local LLM enabled, the sentence (in memory only) and
the candidate list are sent to the model, which must pick one candidate, say "none", or say
the token is intentional. Answers outside the candidate list are rejected. Without the LLM,
or when it abstains, the error stays category C with the intended word unknown.

## 5. Error taxonomy and decision rules

Categories describe **observable form, not mechanism**.

Given the typed form *o* and the intended word *t*, with *d* the Damerau–Levenshtein
distance, *mech* meaning *d* = 1 and the edit is keyboard-shaped, and *phon* meaning the G2P
reading of *o* matches a CMU pronunciation of *t* (or the G2P reading of *t*):

| Rule (first match wins) | Category | Reason code |
|---|---|---|
| intended word unknown | C | `ambiguous_intended` / `llm_abstained` |
| intended is two words | C | `segmentation` |
| *d* = 1 and the edit adds or removes one of a doubled letter | C | `doubled_letter` |
| *d* = 1 and two adjacent vowels are swapped | C | `vowel_transposition` |
| *mech* and *phon* | C | `both_explanations` |
| *mech* | A | `transposition`, `adjacent_key`, `extra_key`, `omission` |
| *phon* | B | `pronunciation_preserved` |
| otherwise | C | `no_reliable_explanation` |

Keyboard-shaped means one of: an adjacent transposition, a substitution by a neighbouring
QWERTY key, an extra key equal or adjacent to a neighbouring letter, or an omitted letter.
Pronunciations are compared without stress, with unstressed reduced vowels (AH0, IH0, UH0,
EH0) merged.

Examples:

| Typed | Intended | Category | Why |
|---|---|---|---|
| sentury | century | B | c→s is not adjacent; same pronunciation |
| seperate | separate | B | vowel substitution, same pronunciation |
| centruy | century | A | transposition, different reading |
| becuse | because | A | omission |
| recieve | receive | C | vowel reordering (fits both explanations) |
| ocasion | occasion | C | doubled letter |
| goverment | government | C | omission that keeps the pronunciation |
| nite | ? | C (or B via LLM → night) | several intended words possible |

The rules favour precision. Several classic spelling-knowledge errors land in C because both
explanations fit them. The report shows each category's reason codes, so the size of C and
its composition are always visible.

**Sensitivity analysis.** The `omission` subtype of A is the least specific: some omissions
are pronunciation-neutral knowledge errors that G2P fails to recognise. Reason codes are
stored per observation, so trends can be recomputed with A excluding omissions.

## 6. Previously mastered words

- A word's accuracy uses every eligible occurrence where the intended word is known.
  Misspellings with an unknown intended word count toward error rates, not toward any word.
- **Mastered cohort:** words with at least 20 observations and at least 95% accuracy in a
  fixed baseline window (both configurable engineering defaults, not clinical values). The
  cohort is **frozen** when it is first computed and stored in the database. Later errors
  never redefine it.
- **Consistently difficult words** (frequent but below 95% in the baseline) are listed
  separately. A word that stays at 80% is not deterioration.
- Per-word changes are reported only when there are at least 10 recent observations.
  Wilson intervals are shown.
- Pooled cohort accuracy compares baseline with recent using a day-clustered bootstrap.

## 7. Metrics and statistics

Primary metrics, per 1,000 eligible words:

- B (phonologically plausible) rate;
- A (mechanical-like) rate;
- overall error rate;
- mastered-word accuracy.

Also reported: the B share of classified errors (B / (A + B)). It addresses "B increasing
*independently of* A" more directly than either rate alone.

Control and quality metrics: eligible-word counts, classification coverage ((A + B) /
errors), share of words excluded, exclusion reasons, how intended words were resolved,
domain, and (Phase 2) typing speed and correction frequency.

Aggregation is weekly, monthly, and in rolling 30- and 90-day windows. Periods with fewer
than 1,000 eligible words are shown hollow or grey and are not connected in charts.

Uncertainty:

- Single-period rates have **Wilson 95% intervals**. These assume independent words and are
  optimistic.
- Baseline-vs-recent differences use a **day-clustered bootstrap**: days are resampled
  within each window, 2,000 iterations, percentile 95% interval. This respects within-day
  clustering from fatigue, topic and speed.

No alarm thresholds, composite scores or significance-driven notifications are produced.
Intervals are descriptive aids for one person reading their own data, not hypothesis tests
corrected for the many comparisons the report shows.

## 8. Reproducibility and versioning

Each observation stores the classifier version id. The fingerprint is a hash of:

- the rules version and package version;
- the built-in word lists;
- the pinned dictionary checksums and the SymSpell list checksum;
- the G2P checkpoint hash;
- the cmudict and lingua versions;
- all classification and language thresholds;
- the LLM model digest and prompt version.

Analyses always use a single version. Historical data is reprocessed by re-importing the
export under the new version; `observatory prune` removes old versions. Personal exclusions
are applied retroactively instead of creating a new version.

## 9. Known biases and threats to validity

| Threat | Direction | Mitigation |
|---|---|---|
| Real-word errors undetected | Underestimates all rates, B more than A | Constant over time; stated |
| Error-dense short sentences fail the language vote | Slight underestimate | Lenient thresholds; sentence-level fallback to the paragraph |
| Paste or dictation not detected | Error-free text inflates denominators | Typography, overlap, metadata and length filters; Phase 2 event provenance |
| Fixing words after a spellcheck underline | Underestimates submitted-text errors | Phase 2 records initial typing |
| Topic and vocabulary drift (e.g. more technical writing) | Changes the error opportunity mix | Mastered-word cohort controls for vocabulary; per-domain analysis |
| Writing-mode drift (faster, sloppier AI prompting over time) | Raises A and possibly B | Phase 2 speed-stratified comparisons; B share of A + B |
| Classifier or LLM change | Spurious steps | Version separation, re-import |
| G2P reading misspellings differently from a human | B recall loss; occasional misclassification | Conservative rules; C absorbs conflicts |

## 10. Validation status

The automated tests (synthetic examples, known misspellings, invariants, an end-to-end
synthetic drift recovery) establish **software correctness and consistency only**. They do
not establish real-world precision or recall on this user's writing, and they do not
establish any clinical meaning.

## 11. Literature (conceptual background only)

None of these studies provides weights or thresholds applicable to natural typing by a
healthy bilingual adult. They motivate the distinction between phonologically plausible and
other errors, and the need to separate *what* was written from *how* it was written.

1. An SK, Jang H, Kim HJ, Na DL, Yoon JH (2023). Linguistic, visuospatial, and kinematic
   writing characteristics in cognitively impaired patients with beta-amyloid deposition.
   *Front Aging Neurosci* 15:1217746. doi:10.3389/fnagi.2023.1217746.
   - Korean (Hangeul) written dictation of regular words, irregular words and non-words in
     healthy controls (n = 33), amnestic MCI (31) and AD (26).
   - Phonologically plausible errors on irregular words were higher in aMCI and AD.
     Phonologically implausible errors were higher only in AD. Writing was slower in both
     patient groups.
   - Hangeul's near one-to-one sound–letter mapping limits transfer to English.
2. *Comparison between oral and written spelling in Alzheimer's disease* (1996), PubMed
   8866054. The abstract could not be retrieved while this document was written. Citation
   details are unverified and the paper is listed for completeness only.
3. *Evolution of writing impairment in Spanish patients with Alzheimer's disease* (2020),
   PubMed 33280596. Not retrieved, as above. Spanish orthography is far more transparent than
   English, which limits transfer.
4. Ntracha A et al. (2020). Detection of mild cognitive impairment through natural language
   and touchscreen typing processing. *Front Digit Health* 2:567158.
   - Smartphone free typing, 11 MCI and 12 controls (Greek).
   - Keystroke timing only, with characters not captured. Spelling errors were corrected
     before the NLP analysis, so error types were not studied.
   - Lesson: timing and content are separate channels.
5. Meulemans C, Leijten M, Van Waes L, Engelborghs S, De Maeyer S (2022). Cognitive writing
   process characteristics in Alzheimer's disease. *Front Psychol* 13:872280.
   - Keystroke-logged typed picture descriptions by 15 patients and 15 controls (Dutch).
   - Patients typed slower, with longer between-word pauses. Error types were not
     distinguished.
   - The study used personal inter-key latency as a covariate, which supports typing-speed
     stratification.

Cross-cutting lessons applied here:

- Keep content and process channels separate.
- Treat the target word as unknown in natural text, and abstain when it is unclear.
- Use a per-person baseline instead of population norms.
- Treat task and writing context as confounds: domain separation, and stage separation
  between initial and submitted text.
- Expect noisier phonological classification in English than in transparent orthographies.

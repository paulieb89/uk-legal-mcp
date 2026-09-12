# Frozen test fixtures

Committed, deterministic inputs for the offline suite (`pytest -m "not live"`).
Ad-hoc live captures go in the gitignored `tests/live/fixtures/`; copy one here,
and add a row below, only when a test is to depend on it.

"Not recorded" means the repository holds no evidence of the value.

| File | Source | Identifier | Captured | Licence |
|---|---|---|---|---|
| `uksc_2024_12_full.xml` | TNA Find Case Law (LegalDocML) | `https://caselaw.nationalarchives.gov.uk/uksc/2024/12/data.xml`, *Secretary of State for Business and Trade v Mercer* [2024] UKSC 12. Manifestation dated 2024-11-27 (`tna-enriched`). | Not recorded. First committed 2026-04-19 (`3ca81ef`). | Open Justice Licence (per `docs/api-reference.md`) |
| `housing_act_1988_s21_clml.xml` | legislation.gov.uk (CLML) | `http://www.legislation.gov.uk/ukpga/1988/50/section/21/data.xml`, `dct:valid` 2026-05-01 | Not recorded. A live recording that replaced an earlier synthetic fixture, as described in `a4a7eac` (2026-05-29). | OGL v3 (per `docs/api-reference.md`) |
| `housing_act_1988_s21_html.html` | Hand-built stand-in for a legislation.gov.uk section page, not a capture. Carries only s.21(1) and (4) plus placeholder nav/script. | Housing Act 1988 s.21 | Not applicable. Its tests arrived in `9f0f750` (2026-05-16). | Not recorded |
| `cl100k_base.tiktoken` | tiktoken's `cl100k_base` BPE ranks. `test_legislation_parsers.py` builds the exact encoding from this file, because `tiktoken.get_encoding("cl100k_base")` downloads it on a cold cache. | `https://openaipublic.blob.core.windows.net/encodings/cl100k_base.tiktoken`, 1,681,126 bytes, SHA-256 `223921b76ee99bde995b7ff738513eef100fb51d18c93597a113bcffe865b2a7`. That hash is the `expected_hash` in `tiktoken_ext/openai_public.py` for the project's pinned tiktoken 0.12.0 (`uv.lock`), and the loader re-checks it on every run. | Copied 2026-09-11 from this machine's tiktoken download cache. Not re-downloaded. | MIT, with the notice in `cl100k_base.tiktoken.LICENSE`. The LICENSE itself does not name the encoding files. A tiktoken collaborator stated in [openai/tiktoken#92](https://github.com/openai/tiktoken/issues/92) (2023-04-05) that "the license in this repo applies to the encoding files as well". |

Packaging follow-up: hatch's sdist includes `tests/`, so these fixtures ship in the PyPI sdist (about 2 MB, mostly `cl100k_base.tiktoken`).

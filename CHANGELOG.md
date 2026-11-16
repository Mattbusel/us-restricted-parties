# Changelog

## 0.2.0 (2026-11-15)

- Hugging Face mirror: `hf_publish.py` writes one parquet split per list (flat columns: name, aliases, parent,
  listed date, source agency, source document and text) plus the Senzing files, manifest and dataset card to
  huggingface.co/datasets/tensorust/us-restricted-parties. `load_dataset("tensorust/us-restricted-parties",
  split="us_dhs_uflpa")` loads one list; the `all` config loads every list.
- The daily Action runs the mirror after the release step when the `HF_TOKEN` secret is set, and uploads only when
  the dataset does not already hold the same lists.
- Offline tests for the flat rows and the dataset card.

## 0.1.0 (2026-11-15)

- First release. Builds Senzing JSON Lines for the DoD 1260H list (with named subsidiaries and later removal
  notices applied), Section 889 (FAR 52.204-25), the FCC Covered List, the DHS UFLPA Entity List and an interim
  BIOSECURE list derived from the 1260H biotechnology listings.
- Daily GitHub Action publishes a release when any list changes; `releases/latest/download/<file>` always serves
  the current files.
- dhs.gov blocks GitHub's runners, so the UFLPA list falls back to the full list DHS publishes as the appendix
  of its latest Federal Register notice (same 190 entities), keeping the per-entity dates from the last dhs.gov build.
- Offline tests against real excerpts of each source.

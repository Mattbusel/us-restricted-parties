# Changelog

## 0.1.0 (2026-11-15)

- First release. Builds Senzing JSON Lines for the DoD 1260H list (with named subsidiaries and later removal
  notices applied), Section 889 (FAR 52.204-25), the FCC Covered List, the DHS UFLPA Entity List and an interim
  BIOSECURE list derived from the 1260H biotechnology listings.
- Daily GitHub Action publishes a release when any list changes; `releases/latest/download/<file>` always serves
  the current files.
- Offline tests against real excerpts of each source.

# Affiliates feed (derived)

`affiliates.senzing.jsonl` lists companies that parties on US restricted lists own or control, according to public
ownership data, with a verdict and a proof path for each rule applied. It is rebuilt with the lists every day.

**It is evidence for a compliance review, not a determination.** No agency has said these entities are blocked or
restricted. Each record says which rule was applied, which listed party it traces to, and through which ownership
or control links, so a reviewer can check the chain.

```
https://github.com/Mattbusel/us-restricted-parties/releases/latest/download/affiliates.covered.senzing.jsonl
https://github.com/Mattbusel/us-restricted-parties/releases/latest/download/affiliates.review.senzing.jsonl
https://github.com/Mattbusel/us-restricted-parties/releases/latest/download/affiliates.senzing.jsonl
https://github.com/Mattbusel/us-restricted-parties/releases/latest/download/affiliates.ftm.json
https://github.com/Mattbusel/us-restricted-parties/releases/latest/download/affiliates.manifest.json
https://github.com/Mattbusel/us-restricted-parties/releases/latest/download/affiliates.changes.jsonl
```

| File | Records |
|---|---|
| `affiliates.covered.senzing.jsonl` | strongest verdict `definitely` or `possibly` under some rule: owned 50 percent or more by listed parties, or possibly so. Screen against this file. |
| `affiliates.review.senzing.jsonl` | `control_only` under every rule: a listed party controls or consolidates the entity, but no reported share reaches 50 percent. The rules do not reach control alone (OFAC FAQ 398, BIS FAQ 43), so these are for a review queue, not for screening hits. |
| `affiliates.senzing.jsonl` | both, in one file (each record's `VERDICT_CLASS` says which) |

The split exists because a screening tool that reads only names, such as Watchman, cannot show the verdict: a
`control_only` record loaded beside the others would look like a sanctions match.

## Why

OFAC's 50 Percent Rule blocks entities owned 50 percent or more by blocked persons even when they are not on the
SDN List. BIS adopted the same test for the Entity List, the MEU List and some SDNs (the Affiliates Rule,
90 FR 47201); it is suspended through January 10, 2027 (FR Doc. 2026-22793), and BIS says the Consolidated
Screening List will not list these affiliates (Entity List FAQ Q.46). A name screen against the lists alone misses
them.

## How it is built

`affiliates.py`, after `build.py`:

1. **Seeds**: this repository's five lists, plus the SDN List (with program tags), Entity List and MEU List from
   the trade.gov Consolidated Screening List.
2. **Seed to LEI**: a seed is tied to a Legal Entity Identifier only when the evidence is unambiguous, in this
   order: the LEI printed on the SDN entry; a registration number on the entry that equals the registration
   number in GLEIF in the same country; or an exact normalized name (legal, other or transliterated GLEIF name)
   in the same country that is unique on both sides. The method is recorded per seed in the manifest counts.
3. **Links**: GLEIF Level 2 relationship records reachable from a matched seed:
   - `IS_DIRECTLY_CONSOLIDATED_BY`: accounting consolidation, which means control. When the child reported an
     accounting consolidation percentage, it is used as the share; otherwise the link has no share.
   - `IS_INTERNATIONAL_BRANCH_OF`: a branch is part of its head office (100 percent).
   - `IS_ULTIMATELY_CONSOLIDATED_BY`, only where the child reports no direct parent: control, no share.
   - Parent and subsidiary links stated in the DoD 1260H notices (share not stated).
4. **Rules** ([ownership-rules](https://github.com/Mattbusel/ownership-rules) 0.1.1):
   - `ofac-50`: seeds are SDN entries;
   - `bis-affiliates`: seeds are Entity List entries (not address-only entries), MEU List entries, and SDNs under the
     15 CFR 744.8(a)(1) programs; only entities outside the United States as 15 CFR 772.1 defines it (so Puerto
     Rico and the US territories count as home). Ownership passes through a US company: a foreign company that a
     listed party owns through a US holding company is reached, and the US company's proof step is marked
     `out_of_scope`;
   - `any-list-50`: seeds are this repository's five lists. Those lists have no ownership rule of their own, so this
     is a screening signal only (Section 889 and the FCC Covered List name "subsidiaries and affiliates" without a
     threshold).

A record is written for every entity that gets a verdict other than `not` under at least one rule of which it is
not itself a seed. A party named on one list can therefore have a record under another rule: an Entity List
company that an SDN owns 50 percent or more is blocked under `ofac-50`. `LISTED_ON` then names the lists it is on,
and the rules it is a seed of are left out of its verdicts.

## Verdicts

| Verdict | Meaning here |
|---|---|
| `definitely` | the reported shares held by listed (or definitely covered) parties reach 50 percent |
| `possibly` | a listed party owns a share of unknown size, or the shares reach 50 percent only at the top of a range |
| `control_only` | consolidated by (controlled by) a listed or covered party, with no percentage that reaches 50. OFAC FAQ 398 and BIS FAQ 43: control alone does not trigger the rule; check the ownership |

Known data limit: most GLEIF consolidation links carry no percentage, so most records are `control_only`. That is
stated in the verdict, not hidden. The consolidation percentage, when present, is what the child entity reported
to GLEIF for accounting consolidation; it usually equals the parent's ownership interest but is not an equity
filing, and full consolidation can rest on control below 50 percent. Every rule's proof therefore names the
`share_basis` of the shares behind the verdict (`accounting_consolidation`, `branch`), and each link in the proof
carries its `basis`; none of them is an equity filing. Coverage depends on seeds having LEIs: entities that never registered for an LEI, and Chinese entities
whose GLEIF record has only a Chinese legal name, are not matched. `affiliates.manifest.json` reports how many seeds
of each list were matched.

## Record

```json
{
  "DATA_SOURCE": "US_RESTRICTED_AFFILIATES",
  "RECORD_ID": "lei:213800FEEFIWRLXDF608",
  "RECORD_TYPE": "ORGANIZATION",
  "NAME_ORG": "MEGAFON INVESTMENTS (CYPRUS) LIMITED",
  "NAMES": [{"NAME_TYPE": "PRIMARY", "NAME_ORG": "MEGAFON INVESTMENTS (CYPRUS) LIMITED"}],
  "LEI_NUMBER": "213800FEEFIWRLXDF608",
  "REGISTRATION_COUNTRY": "CY",
  "GLEIF_ENTITY_STATUS": "ACTIVE",
  "LIST_NAME": "Affiliates of US restricted parties (derived)",
  "EVIDENCE_STATE": "evidence, not determination",
  "OWNERSHIP_VERDICTS": {"ofac-50": "control_only", "bis-affiliates": "control_only"},
  "OWNERSHIP_PROOF": {"ofac-50": {"verdict": "control_only", "share": [0.0, 0.0], "reasons": ["control"],
    "share_basis": [], "seeds": ["lei:213800HE6VDVJWT85874"],
    "proof": [
      {"entity": "lei:213800HE6VDVJWT85874", "verdict": "listed", "lists": ["SDN"], "programs": ["RUSSIA-EO14024"], "name": "MEGAFON PAO"},
      {"entity": "lei:213800FEEFIWRLXDF608", "verdict": "control_only", "via": [{"owner": "lei:213800HE6VDVJWT85874",
        "owner_verdict": "listed", "pct": null, "control": true, "source": "GLEIF IS_DIRECTLY_CONSOLIDATED_BY",
        "basis": "accounting_consolidation_no_pct", "as_of": "2021-05-06"}]}]}},
  "INPUTS_SHA256": "...",
  "RELATIONSHIPS": [{"REL_ANCHOR_DOMAIN": "US_RESTRICTED_AFFILIATES", "REL_ANCHOR_KEY": "lei:213800FEEFIWRLXDF608"},
    {"REL_POINTER_DOMAIN": "LISTED_PARTY", "REL_POINTER_KEY": "lei:213800HE6VDVJWT85874", "REL_POINTER_ROLE": "OWNED_OR_CONTROLLED_BY"}],
  "VERDICT_CLASS": "review"
}
```

(Shortened: the real record carries the `bis-affiliates` proof too.) The primary name is a Latin-script name when
GLEIF has one; the legal name in its own script is an alias. `INPUTS_SHA256` is the hash over the input files listed
in `affiliates.manifest.json` (CSL, GLEIF files and the five list files), so a record can be traced to the exact
inputs it was built from. GLEIF republishes its files several times a day, so that hash changes on days when no
record does; the build decides whether to publish a release from `records_sha256`, a hash of the records without
it, and publishes only when a list or a record changed.

`affiliates.changes.jsonl` has one event per record per build: `added`, `removed` or `verdict_changed` (with the
verdicts before and after). `CHANGES.md` in the release summarizes them.

## Load it into Watchman

The files are Senzing JSON Lines, so moov-io/watchman loads them like the other lists. Watchman keeps only the
name fields, so load the covered file as your screening list, and the review file (if at all) under its own source
list name so its hits can be routed to a review queue:

```yaml
Watchman:
  Download:
    Senzing:
      - SourceList: "us_restricted_affiliates"
        Location: "https://github.com/Mattbusel/us-restricted-parties/releases/latest/download/affiliates.covered.senzing.jsonl"
      # Optional: control without 50 percent ownership, for review only
      - SourceList: "us_restricted_affiliates_review"
        Location: "https://github.com/Mattbusel/us-restricted-parties/releases/latest/download/affiliates.review.senzing.jsonl"
```

The verdict and proof fields stay in the files for your review step.

## Load it into yente (OpenSanctions)

`affiliates.ftm.json` is FollowTheMoney entities, one per line: a `Company` per derived record (topics
`sanction.control` for the OFAC rule, `export.control.linked` for the BIS rule, `export.risk` for the screening
rule), a `Company` per owner it cites, and an `Ownership` per link. Add it to your yente manifest:

```yaml
catalogs:
  - url: "https://data.opensanctions.org/datasets/latest/index.json"
    scope: default
    resource_name: entities.ftm.json
datasets:
  - name: us_restricted_affiliates
    title: Affiliates of US restricted parties (derived, evidence not determination)
    entities_url: https://github.com/Mattbusel/us-restricted-parties/releases/latest/download/affiliates.ftm.json
  - name: screening
    title: OpenSanctions default plus derived affiliates
    datasets:
      - default
      - us_restricted_affiliates
```

Point `YENTE_MANIFEST` at the file and match against the `screening` collection. Check the OpenSanctions licence for
your use of their data; this file is CC BY 4.0.

## Run it yourself

```
pip install "ownership-rules @ git+https://github.com/Mattbusel/ownership-rules@5fa9b7d9cf581bc9465f229c7d06f9c171d81f64"
python build.py --out dist
python affiliates.py --lists dist --out dist --cache .cache
```

That pins ownership-rules 0.1.1 to its commit, which is also what the nightly build installs. (Once the package is
on PyPI, `pip install ownership-rules==0.1.1` installs the same code.)

The GLEIF Level 1 file is about 500 MB compressed. Once the inputs are downloaded, the run took under two minutes
on a laptop on November 17, 2026. GLEIF data is published under CC0.

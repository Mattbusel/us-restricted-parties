#!/usr/bin/env python3
"""Load the US Restricted Parties feed into Python, or convert it to CSV or FollowTheMoney.

Standard library only, Python 3.9+. Copy this one file into your project, or run it:

    python load.py                         # counts per list from the latest release
    python load.py --list us_dhs_uflpa --format csv > uflpa.csv
    python load.py --format ftm > restricted.ftm.jsonl     # FollowTheMoney entities
    python load.py --feed dist/all.jsonl   # a local file instead of the latest release

As a module:

    from load import load, names, parent_id
    for rec in load("us_dod_1260h"):
        print(rec["NAME_ORG"], names(rec), parent_id(rec))

Every record is the Senzing JSON object from the release file, unchanged. The fields are
described list by list in docs/lists.md. Data: CC BY 4.0, "US Restricted Parties feed by
Tensorust LLC".
"""
import argparse
import csv
import json
import sys
import urllib.request
from typing import Any, Dict, Iterable, Iterator, List, Optional

LATEST = "https://github.com/Mattbusel/us-restricted-parties/releases/latest/download/"
LISTS = ("us_dod_1260h", "us_far_889", "us_fcc_covered", "us_dhs_uflpa", "us_biosecure")

Record = Dict[str, Any]


def _lines(source: str) -> Iterator[str]:
    if source.startswith(("http://", "https://")):
        req = urllib.request.Request(source, headers={"User-Agent": "us-restricted-parties-load/1.0"})
        with urllib.request.urlopen(req, timeout=60) as resp:
            yield from resp.read().decode("utf-8").splitlines()
    else:
        with open(source, encoding="utf-8") as f:
            yield from f


def load(list_name: str = "all", source: Optional[str] = None) -> List[Record]:
    """Records of one list (``"us_dod_1260h"``, ``"us_far_889"``, ``"us_fcc_covered"``,
    ``"us_dhs_uflpa"``, ``"us_biosecure"``) or ``"all"``.

    ``source`` is a URL or a local path to a JSON Lines file; by default the latest release
    file for ``list_name``. With a local ``all.jsonl`` and a single list name, records of the
    other lists are left out.
    """
    if list_name != "all" and list_name not in LISTS:
        raise ValueError(f"unknown list {list_name!r}; expected 'all' or one of {', '.join(LISTS)}")
    src = source or f"{LATEST}{list_name}.jsonl"
    want = None if list_name == "all" else list_name.upper()
    out = []
    for line in _lines(src):
        if not line.strip():
            continue
        rec = json.loads(line)
        if want is None or rec.get("DATA_SOURCE") == want:
            out.append(rec)
    return out


def manifest(source: Optional[str] = None) -> Dict[str, Any]:
    """The release's manifest.json: record counts, hashes, source documents, fetch errors."""
    return json.loads("".join(_lines(source or LATEST + "manifest.json")))


def names(rec: Record) -> List[str]:
    """The primary name first, then every alias, without duplicates."""
    out = [rec["NAME_ORG"]]
    for n in rec.get("NAMES", []):
        v = n.get("NAME_ORG")
        if v and v not in out:
            out.append(v)
    return out


def parent_id(rec: Record) -> Optional[str]:
    """``RECORD_ID`` of the listed parent for a named subsidiary, else ``None``."""
    for rel in rec.get("RELATIONSHIPS", []):
        if rel.get("REL_POINTER_ROLE") == "SUBSIDIARY_OF":
            return str(rel["REL_POINTER_KEY"])
    return None


CSV_FIELDS = (
    "data_source", "record_id", "name", "aliases", "list_name", "listed_on", "parent_name",
    "parent_record_id", "includes_affiliates", "source_url", "source_doc",
)


def to_rows(records: Iterable[Record]) -> Iterator[Dict[str, Any]]:
    """One flat dict per record, the columns of ``CSV_FIELDS``. Aliases are joined with
    ``" | "``."""
    for r in records:
        yield {
            "data_source": r["DATA_SOURCE"],
            "record_id": r["RECORD_ID"],
            "name": r["NAME_ORG"],
            "aliases": " | ".join(names(r)[1:]),
            "list_name": r.get("LIST_NAME", ""),
            "listed_on": r.get("LISTED_ON", ""),
            "parent_name": r.get("PARENT_NAME", ""),
            "parent_record_id": parent_id(r) or "",
            "includes_affiliates": "true" if r.get("INCLUDES_AFFILIATES") else "",
            "source_url": r.get("SOURCE_URL", ""),
            "source_doc": r.get("SOURCE_DOC", ""),
        }


def to_ftm(records: Iterable[Record], authority: Optional[Dict[str, str]] = None) -> Iterator[Dict[str, Any]]:
    """FollowTheMoney entities: a ``Company`` per record, a ``Sanction`` linking it to the
    list, and an ``Ownership`` from the listed parent to each named subsidiary. Entity ids are
    the feed's ``RECORD_ID`` (stable from build to build) with a suffix for the derived
    entities. ``authority`` maps a ``DATA_SOURCE`` to the issuing body; the defaults come from
    each list's operator in manifest.json."""
    auth = dict(_AUTHORITY)
    auth.update(authority or {})
    for r in records:
        rid = r["RECORD_ID"]
        company = {"name": [r["NAME_ORG"]], "sourceUrl": [r["SOURCE_URL"]] if r.get("SOURCE_URL") else []}
        aliases = names(r)[1:]
        if aliases:
            company["alias"] = aliases
        yield {"id": rid, "schema": "Company", "properties": company}
        sanction: Dict[str, List[str]] = {"entity": [rid], "program": [r.get("LIST_NAME", "")]}
        if r["DATA_SOURCE"] in auth:
            sanction["authority"] = [auth[r["DATA_SOURCE"]]]
        if r.get("LISTED_ON"):
            sanction["listingDate"] = [r["LISTED_ON"]]
        if r.get("SOURCE_URL"):
            sanction["sourceUrl"] = [r["SOURCE_URL"]]
        reason = r.get("BASIS") or r.get("SOURCE_TEXT")
        if reason:
            sanction["reason"] = [reason]
        yield {"id": f"{rid}-sanction", "schema": "Sanction", "properties": sanction}
        pid = parent_id(r)
        if pid:
            yield {"id": f"{pid}-owns-{rid}", "schema": "Ownership", "properties": {"owner": [pid], "asset": [rid]}}


_AUTHORITY = {
    "US_DOD_1260H": "Department of Defense, OUSD(A&S)",
    "US_FAR_889": "FAR Council (FAR 52.204-25)",
    "US_FCC_COVERED": "Federal Communications Commission, PSHSB",
    "US_DHS_UFLPA": "DHS Forced Labor Enforcement Task Force",
    "US_BIOSECURE": "Department of Defense, OUSD(A&S) (Section 1260H biotechnology listings)",
}


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--list", default="all", help="all (default) or one of: " + ", ".join(LISTS))
    ap.add_argument("--feed", help="local JSON Lines file or URL instead of the latest release")
    ap.add_argument("--format", choices=("summary", "jsonl", "csv", "ftm"), default="summary")
    args = ap.parse_args(argv)
    recs = load(args.list, args.feed)
    out = sys.stdout
    if args.format == "summary":
        counts: Dict[str, int] = {}
        for r in recs:
            counts[r["DATA_SOURCE"]] = counts.get(r["DATA_SOURCE"], 0) + 1
        for ds, n in sorted(counts.items()):
            print(f"{ds:<16} {n:>5} records", file=out)
        print(f"{'total':<16} {len(recs):>5} records", file=out)
    elif args.format == "jsonl":
        for r in recs:
            out.write(json.dumps(r, ensure_ascii=False) + "\n")
    elif args.format == "csv":
        w = csv.DictWriter(out, fieldnames=CSV_FIELDS, lineterminator="\n")
        w.writeheader()
        w.writerows(to_rows(recs))
    else:
        for e in to_ftm(recs):
            out.write(json.dumps(e, ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())

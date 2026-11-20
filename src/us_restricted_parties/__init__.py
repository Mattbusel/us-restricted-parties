"""Load the US Restricted Parties feed into Python, or convert it to CSV or FollowTheMoney.

Standard library only, Python 3.9+, no dependencies. Install the package, or copy this one file into your
project as ``us_restricted_parties.py``. As a command (``us-restricted-parties``, or ``python -m
us_restricted_parties``):

    us-restricted-parties                          # counts per list from the latest release
    us-restricted-parties --list us_dhs_uflpa --format csv > uflpa.csv
    us-restricted-parties --format ftm > restricted.ftm.jsonl     # FollowTheMoney entities
    us-restricted-parties --feed dist/all.jsonl    # a local file instead of the latest release
    us-restricted-parties --list affiliates.covered --release data-2026-11-17-2241 --verify

As a module:

    from us_restricted_parties import latest_release, load, names, parent_id, verdicts
    for rec in load("us_dod_1260h"):
        print(rec["NAME_ORG"], names(rec), parent_id(rec))
    tag = latest_release()                          # "data-2026-11-18": store it with your screening results
    pinned = load("us_dhs_uflpa", release=tag, verify=True, cache_dir=".urp-cache")
    for rec in load("affiliates.covered", release=tag):  # owned 50 percent or more by listed parties, or possibly
        print(rec["NAME_ORG"], verdicts(rec))

Every record is the Senzing JSON object from the release file, unchanged. The fields are
described list by list in docs/lists.md. Data: CC BY 4.0, "US Restricted Parties feed by
Tensorust LLC". Code: MIT.
"""
import argparse
import csv
import hashlib
import json
import os
import re
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional

__version__ = "0.7.0"

__all__ = [
    "__version__", "REPO", "LATEST", "RELEASES", "LISTS", "AFFILIATES", "CSV_FIELDS", "Record",
    "latest_release", "load", "manifest", "file_name", "url", "expected_sha256",
    "names", "parent_id", "verdicts", "to_rows", "to_ftm", "main",
]

REPO = "https://github.com/Mattbusel/us-restricted-parties"
LATEST = REPO + "/releases/latest/download/"
RELEASES = REPO + "/releases/download/"
LISTS = ("us_dod_1260h", "us_far_889", "us_fcc_covered", "us_dhs_uflpa", "us_biosecure")
# Derived feed: companies listed parties own or control (docs/affiliates.md). Screen against
# "affiliates.covered"; "affiliates.review" is control without a 50 percent share.
AFFILIATES = ("affiliates.covered", "affiliates.review", "affiliates")

Record = Dict[str, Any]

_UA = {"User-Agent": f"us-restricted-parties/{__version__} (python)"}
_TAG = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def file_name(list_name: str) -> str:
    """Release asset name of a list: ``us_dhs_uflpa.jsonl``, ``all.jsonl``,
    ``affiliates.covered.senzing.jsonl``."""
    if list_name in AFFILIATES:
        return f"{list_name}.senzing.jsonl"
    if list_name == "all" or list_name in LISTS:
        return f"{list_name}.jsonl"
    raise ValueError(f"unknown list {list_name!r}; expected 'all' or one of "
                     f"{', '.join(LISTS + AFFILIATES)}")


def _release_dir(release: Optional[str]) -> str:
    if release is None:
        return LATEST
    if not _TAG.match(release):
        raise ValueError(f"release {release!r}: expected a data release tag such as 'data-2026-11-17-2241'")
    return RELEASES + release + "/"


def url(list_name: str = "all", release: Optional[str] = None) -> str:
    """Download URL of a list file: the latest release, or a fixed one such as
    ``release="data-2026-11-17-2241"`` (every data release is kept, so a pinned URL never
    changes)."""
    return _release_dir(release) + file_name(list_name)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req: Any, fp: Any, code: Any, msg: Any, headers: Any, newurl: str) -> None:
        return None


def latest_release(timeout: float = 30) -> str:
    """Tag of the latest data release, such as ``"data-2026-11-18"``: one HEAD request to github.com (the
    ``releases/latest`` redirect), no API token and no API rate limit. Pass it as ``release=`` so that every file
    of one screening run, and its hash check, come from the same release, and store it with the results."""
    req = urllib.request.Request(REPO + "/releases/latest", headers=_UA, method="HEAD")
    location = ""
    try:
        with urllib.request.build_opener(_NoRedirect).open(req, timeout=timeout) as resp:
            location = resp.headers.get("Location") or resp.geturl()
    except urllib.error.HTTPError as e:
        if e.code not in (301, 302, 303, 307, 308):
            raise
        location = e.headers.get("Location") or ""
    m = re.search(r"/releases/tag/([^/?#]+)$", location)
    if not m or not _TAG.match(urllib.parse.unquote(m.group(1))):
        raise ValueError(f"could not read the latest release tag from {location!r}")
    return urllib.parse.unquote(m.group(1))


def _read(source: str, timeout: float = 60) -> bytes:
    if source.startswith(("http://", "https://")):
        req = urllib.request.Request(source, headers=_UA)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return bytes(resp.read())
    with open(source, "rb") as f:
        return f.read()


def _fetch(name: str, release: Optional[str], cache_dir: Optional[str]) -> bytes:
    """A file of a release, from ``cache_dir/<release>/`` when it is there. Only pinned releases are cached:
    their files never change."""
    src = _release_dir(release) + name
    if cache_dir is None or release is None:
        return _read(src)
    path = Path(cache_dir) / release / name
    if path.is_file():
        return path.read_bytes()
    return _read(src)


def _store(data: bytes, name: str, release: Optional[str], cache_dir: Optional[str]) -> None:
    if cache_dir is None or release is None:
        return
    path = Path(cache_dir) / release / name
    if path.is_file():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".part-")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def expected_sha256(list_name: str, man: Dict[str, Any]) -> str:
    """The SHA-256 a release's manifest.json gives for one list file (not for ``all``)."""
    if list_name in LISTS:
        return str(man["lists"][list_name]["sha256"])
    if list_name in AFFILIATES:
        return str(man["affiliates"]["files"][file_name(list_name)]["sha256"])
    raise ValueError(f"manifest.json has no hash for {list_name!r}; verify the single lists instead")


def load(list_name: str = "all", source: Optional[str] = None, release: Optional[str] = None,
         verify: bool = False, cache_dir: Optional[str] = None) -> List[Record]:
    """Records of one list (``"us_dod_1260h"``, ``"us_far_889"``, ``"us_fcc_covered"``,
    ``"us_dhs_uflpa"``, ``"us_biosecure"``), ``"all"`` of them, or the derived affiliates
    (``"affiliates.covered"``, ``"affiliates.review"``, ``"affiliates"``).

    ``source`` is a URL or a local path to a JSON Lines file; by default the file of the latest
    release, or of ``release`` (a data release tag such as ``"data-2026-11-17-2241"``). With a
    local ``all.jsonl`` and a single list name, records of the other lists are left out.

    ``verify=True`` checks the file's SHA-256 against manifest.json of the same release (next to
    ``source`` when one is given) and raises ``ValueError`` on a mismatch. Without ``release``, the
    latest release's tag is looked up first, so the file and the manifest cannot come from two
    different builds.

    ``cache_dir`` keeps downloaded files of a pinned release in ``cache_dir/<release>/`` and reads
    them from there next time (release files never change; with ``verify=True`` they are checked
    on every read). Without ``release``, the latest tag is looked up (one small request) and its
    files are cached under that tag.
    """
    name = file_name(list_name)
    if verify and list_name not in LISTS and list_name not in AFFILIATES:
        raise ValueError(f"manifest.json has no hash for {list_name!r}; verify the single lists instead")
    if source is None and release is None and (verify or cache_dir is not None):
        release = latest_release()
    if source is not None:
        data = _read(source)
    else:
        data = _fetch(name, release, cache_dir)
    if verify:
        if source is None:
            man = manifest(release=release, cache_dir=cache_dir)
        elif source.startswith(("http://", "https://")):
            man = manifest(urllib.parse.urljoin(source, "manifest.json"))
        else:
            man = manifest(str(Path(source).with_name("manifest.json")))
        want_sha = expected_sha256(list_name, man)
        got = hashlib.sha256(data).hexdigest()
        if got != want_sha:
            raise ValueError(f"{name}: SHA-256 {got} does not match manifest.json ({want_sha})")
    if source is None:
        _store(data, name, release, cache_dir)
    want = list_name.upper() if list_name in LISTS else None
    out = []
    for line in data.decode("utf-8").splitlines():
        if not line.strip():
            continue
        rec = json.loads(line)
        if want is None or rec.get("DATA_SOURCE") == want:
            out.append(rec)
    return out


def manifest(source: Optional[str] = None, release: Optional[str] = None,
             cache_dir: Optional[str] = None) -> Dict[str, Any]:
    """The release's manifest.json: record counts, hashes, source documents, fetch errors, and
    the affiliates build (``data_date``, ``stale``, the ownership rules and their status)."""
    if source is not None:
        data = _read(source)
    else:
        data = _fetch("manifest.json", release, cache_dir)
    out = json.loads(data.decode("utf-8"))
    if not isinstance(out, dict):
        raise ValueError("manifest.json: expected a JSON object")
    if source is None:
        _store(data, "manifest.json", release, cache_dir)
    return out


def verdicts(rec: Record) -> Dict[str, str]:
    """For an affiliates record, the verdict under each rule, e.g. ``{"ofac-50": "definitely",
    "bis-affiliates": "possibly"}``; ``{}`` for a list record."""
    return dict(rec.get("OWNERSHIP_VERDICTS") or {})


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
    args_in = sys.argv[1:] if argv is None else list(argv)
    if args_in[:1] == ["coverage"]:  # us-restricted-parties coverage PATH...: see us_restricted_parties.coverage
        from . import coverage
        return coverage.main(args_in[1:])
    ap = argparse.ArgumentParser(prog="us-restricted-parties", description=(__doc__ or "").split("\n\n")[0])
    ap.add_argument("--version", action="version", version=f"us-restricted-parties {__version__}")
    ap.add_argument("--list", default="all", help="all (default) or one of: " + ", ".join(LISTS + AFFILIATES))
    ap.add_argument("--feed", help="local JSON Lines file or URL instead of the latest release")
    ap.add_argument("--release", help="a data release tag such as data-2026-11-17-2241 instead of the latest")
    ap.add_argument("--verify", action="store_true", help="check the file's SHA-256 against manifest.json")
    ap.add_argument("--cache-dir", help="keep release files here and reuse them (pinned releases never change)")
    ap.add_argument("--latest-release", action="store_true", help="print the latest data release tag and exit")
    ap.add_argument("--format", choices=("summary", "jsonl", "csv", "ftm"), default="summary")
    args = ap.parse_args(args_in)
    out = sys.stdout
    try:
        if args.latest_release:
            print(latest_release(), file=out)
            return 0
        recs = load(args.list, args.feed, release=args.release, verify=args.verify, cache_dir=args.cache_dir)
    except (ValueError, OSError) as e:  # bad input, hash mismatch, HTTP and network errors
        print(f"us-restricted-parties: {e}", file=sys.stderr)
        return 2
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
        for ent in to_ftm(recs):
            out.write(json.dumps(ent, ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Coverage check: fail when a sanctions screen is missing a US restricted list, runs on stale data, or loaded a
list whose names are empty.

"Screened against sanctions" usually means screened against the lists that were easy to download. This check
takes what a screening tool actually uses (its config, its list directory, a dump of its index, or its whole
source tree) and fails unless every US restricted list is there, current, and has names:

    us-restricted-parties coverage path/to/lists/            # a directory of list files
    us-restricted-parties coverage watchman.yml               # a config that names its lists or their URLs
    us-restricted-parties coverage index.jsonl --json         # a dump of what the engine loaded
    us-restricted-parties coverage . --skip far_889           # a source tree; drop a list you do not need

Lists checked (``--list-ids`` prints them): OFAC SDN, OFAC non-SDN (consolidated), the trade.gov Consolidated
Screening List, the BIS Entity List, Military End User List and Unverified List, DoD 1260H, Section 889, the FCC
Covered List, the UFLPA Entity List, and BIOSECURE once OMB publishes it.

For each list the result is one of:

* ``ok``: list data was found, it has names, and its data date is not older than the agency's latest update.
* ``declared``: the config or code names the list or its URL and fetches it at run time; no local data to inspect.
* ``missing``: nothing in the input names, references or contains the list. Fails.
* ``stale``: list data was found but its data date is older than the agency's latest update. Fails.
* ``empty_names``: list data was found and some of its records have no name at all. Fails.
* ``not_published``: BIOSECURE before OMB publishes its list. Never fails.

The agencies' latest update dates come from ``sources.json`` on the status branch of this repository, rebuilt by the
daily build from OFAC's Sanctions List Service, the trade.gov CSL and the Federal Register notices behind the other
lists (``--sources-live`` reads the agencies directly, ``--sources FILE`` reads a saved copy). A list file's data date
is its own publish date where the format has one (OFAC XML, the CSL's ``sources_used``, FollowTheMoney
``last_seen``, this feed's manifest.json), else the last git commit that touched the file, else its modification
time. Run it in a full clone (``fetch-depth: 0``) so the git dates are real.

Exit status: 0 when every required list is ok or declared, 1 when any fails, 2 on bad input or when the source dates
cannot be read. Standard library only.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional, Pattern, Sequence, Set, Tuple

__all__ = [
    "SPECS", "Spec", "Evidence", "ListResult", "Report", "SOURCES_URL", "OPENSANCTIONS_INDEX_URL",
    "check", "live_source_dates", "load_source_dates", "classify_value", "row_names", "main",
]

SOURCES_URL = "https://raw.githubusercontent.com/Mattbusel/us-restricted-parties/status/sources.json"
OPENSANCTIONS_INDEX_URL = "https://data.opensanctions.org/datasets/latest/index.json"
MANIFEST_URL = "https://github.com/Mattbusel/us-restricted-parties/releases/latest/download/manifest.json"
OFAC_SDN_URL = "https://sanctionslistservice.ofac.treas.gov/api/PublicationPreview/exports/SDN.CSV"
OFAC_CONS_URL = "https://sanctionslistservice.ofac.treas.gov/api/PublicationPreview/exports/CONSOLIDATED.XML"
CSL_URL = "https://data.trade.gov/downloadable_consolidated_screening_list/v1/consolidated.json"
_UA = {"User-Agent": "us-restricted-parties-coverage (python)"}


@dataclass(frozen=True)
class Spec:
    id: str
    title: str
    agency: str
    # names the list in code, config or a URL
    ref: Pattern[str]
    # a per-row list field ("source", "DATA_SOURCE", "datasets") naming this list
    value: Pattern[str]
    # a file of this list, by its file name
    filename: Optional[Pattern[str]]
    # in the trade.gov CSL, so a CSL file or reference brings it along
    in_csl: bool = False
    # published as Federal Register notices: the agency site can show a change a few days before the FR date
    federal_register: bool = False


def _re(p: str) -> Pattern[str]:
    return re.compile(p, re.I)


# Order matters for classify_value: non-SDN before SDN, UFLPA before the BIS Entity List.
SPECS: Tuple[Spec, ...] = (
    Spec("ofac_nonsdn", "OFAC non-SDN lists (consolidated: SSI, FSE, NS-MBS, NS-CMIC, PLC, CAPTA)", "Treasury OFAC",
         _re(r"cons_(?:prim|alt|add|comments)\.csv|cons_advanced\.xml|ofac/downloads/consolidated|exports/consolidated"
             r"|us_ofac_cons|non[-_ ]?sdn|sectoral[-_ ]sanctions[-_ ]identifications|ns[-_]mbs"
             r"|menu[-_ ]based[-_ ]sanctions"
             r"|chinese[-_ ]military[-_ ]industrial[-_ ]complex|foreign[-_ ]sanctions[-_ ]evaders"),
         _re(r"non[-_ ]?sdn|sectoral sanctions|\bssi\b|ns[-_ ]mbs|menu-based|\bcmic\b|military-industrial complex"
             r"|palestinian legislative|\bplc\b|\bcapta?\b|foreign sanctions evaders|\bfse\b|us_ofac_cons|ns-isa"
             r"|ofac[-_ ]?cons(?:olidated)?\b"),
         _re(r"^(?:cons_(?:prim|alt|add)\.csv|cons_advanced\.xml|us_ofac_cons\b.*)$"), in_csl=True),
    Spec("ofac_sdn", "OFAC Specially Designated Nationals (SDN) List", "Treasury OFAC",
         _re(r"\bsdn(?:_advanced|_enhanced)?\.(?:csv|xml|pip|del|ff|json)\b|us_ofac_sdn"
             r"|specially[-_ ]designated[-_ ]nationals"
             r"|ofac/downloads/(?:sdn|sanctions/1\.0/sdn)|exports/sdn|ofac[-_ ]?sdn\b|\bsdn[-_ ]?list\b"
             r"|sanctionslistservice\.ofac\.treas\.gov/entities\b|exports/enhanced_xml"),
         _re(r"specially designated nationals|^\s*sdn\b|\(sdn\)|us_ofac_sdn|^ofac[-_ ]?sdn$"),
         _re(r"^(?:sdn(?:_advanced|_enhanced)?\.(?:csv|xml|json)|us_ofac_sdn\b.*)$"), in_csl=True),
    Spec("dhs_uflpa", "UFLPA Entity List (forced labor import ban)", "DHS FLETF",
         _re(r"uflpa|uyghur[-_ ]forced[-_ ]labor|forced[-_ ]labor[-_ ]entity[-_ ]list"),
         _re(r"uflpa|uyghur forced labor"),
         _re(r"^(?:.*uflpa.*)$"), federal_register=True),
    Spec("bis_meu", "BIS Military End User (MEU) List", "Commerce BIS",
         # case-sensitive MEU: "meu" is Portuguese for "my"
         _re(r"military[-_ ]end[-_ ]user|(?-i:\bMEU\b)|us_bis_meu"),
         _re(r"military end user|\bmeu\b"),
         _re(r"^(?:.*\bmeu\b.*|.*military_end_user.*)$"), in_csl=True),
    Spec("bis_uvl", "BIS Unverified List (UVL)", "Commerce BIS",
         # the list's own capitalised name, or "unverified list" next to BIS / export words
         _re(r"(?-i:\bUnverified List\b|\bUVL\b)|us_bis_uvl"
             r"|unverified[-_ ]list[^\n]{0,40}(?:\bbis\b|export[-_ ]control|commerce|744)"
             r"|(?:\bbis\b|export[-_ ]control|commerce)[^\n]{0,40}unverified[-_ ]list"),
         _re(r"unverified list|\buvl\b"),
         _re(r"^(?:.*\buvl\b.*|.*unverified.*)$"), in_csl=True),
    Spec("bis_entity", "BIS Entity List", "Commerce BIS",
         # the list's own capitalised name, or "entity list" next to BIS / export words ("the raw entity list" is not)
         _re(r"(?-i:\bEntity List\b)|us_bis_entity|bis[-_ ]entity"
             r"|entity[-_ ]list[^\n]{0,40}(?:\bbis\b|export[-_ ]control|commerce|744|\bear\b)"
             r"|(?:\bbis\b|export[-_ ]control|commerce)[^\n]{0,40}entity[-_ ]list"),
         _re(r"\bentity list\b|\(el\)|^\s*el\s*$|bis_entity"),
         _re(r"^(?:bis_entity.*|entity_list\.(?:csv|json|jsonl|txt))$"), in_csl=True),
    Spec("csl", "trade.gov Consolidated Screening List (CSL)", "Commerce ITA",
         _re(r"consolidated[-_ ]screening[-_ ]list|consolidated_screening_list|us_trade_csl"
             r"|api\.trade\.gov[^\s'\"]*consolidated"),
         _re(r"consolidated screening list|us_trade_csl"),
         _re(r"^(?:consolidated_screening_list\b.*|us_trade_csl\b.*|csl\.(?:csv|json|tsv|jsonl))$")),
    Spec("dod_1260h", "DoD Chinese Military Companies (Section 1260H)", "DoD",
         _re(r"1260h|chinese[-_ ]military[-_ ]compan|chinese_milcorps"),
         _re(r"1260h|chinese military compan|chinese_milcorps"),
         _re(r"^(?:.*1260h.*|.*chinese_milcorps.*)$"), federal_register=True),
    Spec("far_889", "Section 889 covered telecom and video surveillance companies", "FAR Council",
         _re(r"section[-_ ]?889|\bfar[-_ ]?889|52\.204-25|us_far_889|ndaa[-_ ]?889"
             r"|889[-_ ](?:covered|prohibit|telecom)"),
         _re(r"889|52\.204-25"),
         _re(r"^(?:.*(?:far|section|ndaa)[-_]?889.*)$"), federal_register=True),
    Spec("fcc_covered", "FCC Covered List", "FCC",
         _re(r"fcc[^\n]{0,30}covered[-_ ]?list|covered[-_ ]?list[^\n]{0,30}\bfcc\b|fcc\.gov/supplychain/coveredlist"
             r"|us_fcc_covered|fcc_covered"),
         _re(r"fcc[^\n]{0,30}covered|us_fcc_covered|fcc_covered"),
         _re(r"^(?:.*fcc_covered.*)$")),
    Spec("biosecure", "BIOSECURE biotechnology companies of concern (OMB list)", "OMB",
         _re(r"biosecure|biotechnology[-_ ]compan(?:y|ies)[-_ ]of[-_ ]concern"),
         _re(r"biosecure|biotechnology compan"),
         _re(r"^(?:.*biosecure.*)$"), federal_register=True),
)
SPEC = {s.id: s for s in SPECS}
# report order
ORDER = ("ofac_sdn", "ofac_nonsdn", "csl", "bis_entity", "bis_meu", "bis_uvl", "dod_1260h", "far_889", "fcc_covered",
         "dhs_uflpa", "biosecure")
CSL_MEMBERS = tuple(s.id for s in SPECS if s.in_csl)
# OpenSanctions source datasets that carry a list; us_trade_csl carries every CSL member list.
OPENSANCTIONS = {
    "us_ofac_sdn": ("ofac_sdn",),
    "us_ofac_cons": ("ofac_nonsdn",),
    "us_trade_csl": ("csl",) + CSL_MEMBERS,
    "us_dod_chinese_milcorps": ("dod_1260h",),
    "us_fcc_covered_list": ("fcc_covered",),
    "us_dhs_uflpa": ("dhs_uflpa",),
}
# The topics OpenSanctions puts on each list's entities (measured 2026-11-20 from entities.ftm.json of each dataset:
# 1260H 207 of 207 "debarment", FCC 13 of 13 "debarment", UFLPA 189 of 189 "sanction", MEU 66 of 70 and UVL 106 of
# 107 "export.control" only). A query filtered to other topics drops the list even though the dataset is in scope.
OPENSANCTIONS_TOPICS = {
    "ofac_sdn": ("sanction",), "ofac_nonsdn": ("sanction",), "csl": ("sanction", "export.control", "debarment"),
    "bis_entity": ("export.control",), "bis_meu": ("export.control",), "bis_uvl": ("export.control",),
    "dod_1260h": ("debarment",), "fcc_covered": ("debarment",), "dhs_uflpa": ("sanction",),
}
# OpenSanctions topic vocabulary (followthemoney topics)
OPENSANCTIONS_TOPIC = re.compile(r"^(?:sanction|debarment|export|trade|role|crime|poi|wanted|reg|corp|fin|gov|asset|"
                                 r"mare|risk|cyber)(?:\.[a-z]+)*$")
# a comment or constant that says the list was copied in on a date
SNAPSHOT = re.compile(r"(?:populated|copied|snapshot|extracted|generated|downloaded|retrieved|taken|exported)"
                      r"[^\n]{0,60}?\b(20\d\d-[01]\d-[0-3]\d)\b", re.I)
REMOVAL_OPS = {"removed", "remove", "deleted", "delete", "delisted"}
# a file whose path says it configures screening (a topics filter there applies to the OpenSanctions query)
SCREEN_PATH = re.compile(r"screen|sanction|kyc|aml|complian|watchlist|polic", re.I)
# This feed's DATA_SOURCE values.
FEED = {"US_DOD_1260H": "dod_1260h", "US_FAR_889": "far_889", "US_FCC_COVERED": "fcc_covered",
        "US_DHS_UFLPA": "dhs_uflpa", "US_BIOSECURE": "biosecure"}
FEED_KEYS = {"us_dod_1260h": "dod_1260h", "us_far_889": "far_889", "us_fcc_covered": "fcc_covered",
             "us_dhs_uflpa": "dhs_uflpa", "us_biosecure": "biosecure"}

CODE_EXT = {".py", ".go", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx", ".rs", ".java", ".kt", ".rb", ".cs", ".php",
            ".scala", ".swift", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf", ".env", ".properties", ".sql",
            ".sh", ".tf", ".json", ".inc", ".ex", ".exs", ".clj", ".dart", ".lua", ".r", ".jl", ".hs", ".ml", ".vue",
            ".svelte"}
DOC_EXT = {".md", ".rst", ".txt", ".adoc", ".html", ".htm", ".org"}
DATA_EXT = {".csv", ".tsv", ".json", ".jsonl", ".ndjson", ".xml", ".txt"}
SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", ".tox", ".mypy_cache", ".pytest_cache", "target",
             ".next", "dist-newstyle", ".gradle", ".idea", "site-packages", "bower_components", ".yarn"}
TEST_PATH = re.compile(r"(?:^|[/\\])(?:tests?|spec|specs|__tests__|testdata|test_data|fixtures?|examples?|e2e|mocks?)"
                       r"(?:[/\\]|$)|(?:^|[/\\])(?:test_[^/\\]*|[^/\\]*_test\.[a-z]+|[^/\\]*\.(?:test|spec)\.[a-z]+)$",
                       re.I)
DOC_PATH = re.compile(r"(?:^|[/\\])(?:docs?|documentation|website|blog)(?:[/\\])", re.I)
AGENT_RULES = re.compile(r"(?:^|[/\\])(?:rules|skills|prompts)[/\\][^/\\]+\.md$|(?:^|[/\\])SKILL\.md$", re.I)
MAX_TEXT = 4 * 1024 * 1024
MAX_DATA = 400 * 1024 * 1024
NAME_KEYS = ("name", "names", "full_name", "fullname", "entity_name", "entityname", "sdn_name", "primary_name",
             "name_org", "name_full", "caption", "org_name", "organization_name", "company_name", "party_name",
             "listed_name", "legal_name", "whole_name", "wholename", "primaryname", "alt_names", "aliases")
LIST_KEYS = ("source", "data_source", "datasource", "list", "list_name", "listname", "source_list", "dataset",
             "datasets", "sanction_list", "sanctions_list", "list_type", "list_id", "program_list", "authority")


def classify_value(value: str) -> Optional[str]:
    """The list id a per-row list field names ("Military End User (MEU) List - Bureau of Industry and Security"
    -> "bis_meu", "US_DOD_1260H" -> "dod_1260h", "us_ofac_cons" -> "ofac_nonsdn"), or None."""
    v = value.strip()
    if not v:
        return None
    if v.upper() in FEED:
        return FEED[v.upper()]
    low = v.lower()
    if low in OPENSANCTIONS:
        return OPENSANCTIONS[low][0] if low != "us_trade_csl" else "csl"
    for s in SPECS:
        if s.id == "far_889" and not re.search(r"section 889|far[-_ ]?889|52\.204-25|us_far_889", low):
            continue
        if s.id == "bis_entity" and "uflpa" in low:
            continue
        if s.value.search(v):
            return s.id
    for s in SPECS:  # a URL or file name in the list field
        if s.id not in ("bis_meu", "bis_uvl", "bis_entity") and s.ref.search(v):
            return s.id
    return None


def _strs(v: Any) -> List[str]:
    if v is None:
        return []
    if isinstance(v, str):
        return [v]
    if isinstance(v, (int, float)):
        return []
    if isinstance(v, list):
        out: List[str] = []
        for x in v:
            out.extend(_strs(x))
        return out
    if isinstance(v, dict):
        out = []
        for k, x in v.items():
            kl = str(k).lower()
            if kl in NAME_KEYS or kl in ("name_last", "name_first", "last_name", "first_name", "lastname",
                                         "firstname", "value", "namepartvalue"):
                out.extend(_strs(x))
        return out
    return []


def row_names(row: Dict[str, Any]) -> List[str]:
    """Every non-empty name a record carries: flat name columns, Senzing NAME_ORG / NAME_FULL / NAME_FIRST +
    NAME_LAST and the NAMES array, FollowTheMoney ``caption`` and ``properties.name``."""
    out: List[str] = []
    for k, v in row.items():
        kl = str(k).lower()
        if kl in NAME_KEYS or kl in ("name_last", "name_first", "last_name", "first_name", "lastname", "firstname"):
            out.extend(_strs(v))
        elif kl == "properties" and isinstance(v, dict):
            for pk in ("name", "alias", "weakAlias", "previousName"):
                out.extend(_strs(v.get(pk)))
    names = [n for n in (x.strip() for x in out) if n]
    if not names:  # change feeds wrap the record: {"op": "changed", "entity": {...}}
        for k in ("entity", "record", "target", "subject", "party", "item", "after", "new"):
            v = row.get(k)
            if isinstance(v, dict) and v is not row:
                names = row_names(v)
                if names:
                    break
    return names


def _row_lists(row: Dict[str, Any], default: Optional[str]) -> List[str]:
    found: List[str] = []
    for k, v in row.items():
        if str(k).lower() in LIST_KEYS:
            for s in _strs(v) if not isinstance(v, str) else [v]:
                lid = classify_value(s)
                if lid and lid not in found:
                    found.append(lid)
        if str(k).lower() == "datasets" and isinstance(v, list):
            for s in v:
                for lid in OPENSANCTIONS.get(str(s).lower(), ())[:1]:
                    if lid not in found:
                        found.append(lid)
    if not found and default:
        found.append(default)
    return found


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_dt(s: Any) -> Optional[datetime]:
    if not s or not isinstance(s, str):
        return None
    s = s.strip()
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S",
                "%Y-%m-%d", "%m/%d/%Y", "%Y%m%d"):
        try:
            dt = datetime.strptime(s.replace("Z", "+0000") if "%z" in fmt else s.rstrip("Z"), fmt)
        except ValueError:
            continue
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    try:
        return parsedate_to_datetime(s)
    except (TypeError, ValueError):
        return None


@dataclass
class Evidence:
    kind: str            # "data", "reference", "opensanctions", "csl"
    path: str
    line: Optional[int] = None
    detail: str = ""
    rows: int = 0
    empty_names: int = 0
    data_date: Optional[str] = None
    date_basis: Optional[str] = None
    test_or_doc: bool = False
    # a CSL reference or CSL rows not split by member list: stands in for the lists inside the CSL
    inherit: bool = False

    def where(self) -> str:
        return f"{self.path}:{self.line}" if self.line else self.path


@dataclass
class ListResult:
    id: str
    title: str
    status: str
    required: bool
    source_updated: Optional[str]
    reason: str
    evidence: List[Evidence] = field(default_factory=list)


@dataclass
class Report:
    inputs: List[str]
    checked_at: str
    sources_generated_at: Optional[str]
    results: List[ListResult]

    @property
    def failed(self) -> List[ListResult]:
        return [r for r in self.results if r.required and r.status in ("missing", "stale", "empty_names")]

    def to_json(self) -> Dict[str, Any]:
        return {
            "inputs": self.inputs, "checked_at": self.checked_at, "sources_generated_at": self.sources_generated_at,
            "ok": not self.failed, "failed": [r.id for r in self.failed],
            "lists": [{
                "id": r.id, "title": r.title, "status": r.status, "required": r.required,
                "source_updated": r.source_updated, "reason": r.reason,
                "evidence": [{k: v for k, v in e.__dict__.items() if v not in (None, "", 0, False)}
                             for e in r.evidence],
            } for r in self.results],
        }


# ---------------------------------------------------------------- source dates

def _http(url: str, method: str = "GET", timeout: float = 90) -> Tuple[bytes, Dict[str, str]]:
    req = urllib.request.Request(url, headers=dict(_UA, **{"User-Agent": "Mozilla/5.0 (compatible; urp-coverage)"}),
                                 method=method)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = b"" if method == "HEAD" else bytes(resp.read())
        return body, {k.lower(): v for k, v in resp.headers.items()}


def _last_modified(url: str) -> Optional[str]:
    _, h = _http(url, "HEAD", timeout=60)
    lm = h.get("last-modified")
    return _iso(parsedate_to_datetime(lm)) if lm else None


def feed_source_dates(man: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """Latest agency change of the five lists this feed rebuilds, from its manifest.json: the newest listing or later
    Federal Register notice (removals included) and, for the FCC, the newest dated entry including categories."""
    out: Dict[str, Dict[str, Any]] = {}
    for key, lid in FEED_KEYS.items():
        m = (man.get("lists") or {}).get(key) or {}
        notes = m.get("notes") or {}
        dates = [m.get("latest_listing"), notes.get("base_date"), notes.get("list_as_of")]
        dates += [n.get("date") for n in notes.get("later_notices") or []]
        dates += [c.get("date") for c in notes.get("category_entries") or []]
        ds = sorted(d for d in dates if isinstance(d, str) and d)
        entry: Dict[str, Any] = {"updated": ds[-1] + "T00:00:00Z" if ds else None, "source": m.get("source"),
                                 "basis": "newest listing or Federal Register notice in the feed manifest"}
        if lid == "biosecure":
            docs = notes.get("omb_list_documents") or []
            entry["published"] = bool(docs)
            if not docs:
                entry.update(updated=None, basis="OMB has not published its list (due Dec 18 2026)")
        out[lid] = entry
    return out


def csl_source_dates(csl: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """Per-list update dates from the CSL's ``sources_used`` (OFAC lists, BIS Entity List, MEU, UVL) and the newest
    of them as the CSL's own date."""
    out: Dict[str, Dict[str, Any]] = {}
    newest: Optional[datetime] = None
    for s in csl.get("sources_used") or []:
        dt = _parse_dt(s.get("source_last_updated"))
        lid = classify_value(str(s.get("source") or ""))
        if dt is None:
            continue
        newest = dt if newest is None or dt > newest else newest
        if lid and lid != "csl":
            prev = _parse_dt((out.get(lid) or {}).get("updated"))
            if prev is None or dt > prev:
                out[lid] = {"updated": _iso(dt), "source": CSL_URL, "basis": f"CSL sources_used: {s.get('source')}"}
    if newest is not None:
        out["csl"] = {"updated": _iso(newest), "source": CSL_URL, "basis": "newest source_last_updated in the CSL"}
    return out


def live_source_dates(manifest: Optional[Dict[str, Any]] = None, csl: Optional[Dict[str, Any]] = None
                      ) -> Dict[str, Any]:
    """Read every list's latest agency update now: OFAC's Sanctions List Service (Last-Modified of SDN.CSV and
    CONSOLIDATED.XML), the trade.gov CSL (``sources_used``) and this feed's manifest.json. The daily build writes this
    as sources.json on the status branch."""
    errors: Dict[str, str] = {}
    lists: Dict[str, Dict[str, Any]] = {}
    if csl is None:
        try:
            csl = json.loads(_http(CSL_URL, timeout=300)[0].decode("utf-8"))
        except (OSError, ValueError) as e:
            errors["csl"] = f"{type(e).__name__}: {e}"
    if csl:
        lists.update(csl_source_dates(csl))
    for lid, url in (("ofac_sdn", OFAC_SDN_URL), ("ofac_nonsdn", OFAC_CONS_URL)):
        try:
            lm = _last_modified(url)
        except (OSError, ValueError) as e:
            errors[lid] = f"{type(e).__name__}: {e}"
            continue
        # OFAC's own file wins over the CSL: the CSL stamps every Treasury list with the newest OFAC publication
        # (non-SDN lists show the SDN's date), while CONSOLIDATED.XML changes only when a non-SDN list does.
        if lm:
            lists[lid] = {"updated": lm, "source": url,
                          "basis": "Last-Modified of the OFAC Sanctions List Service file"}
    if manifest is None:
        try:
            manifest = json.loads(_http(MANIFEST_URL)[0].decode("utf-8"))
        except (OSError, ValueError) as e:
            errors["feed"] = f"{type(e).__name__}: {e}"
    if manifest:
        lists.update(feed_source_dates(manifest))
    return {"schema": 1, "generated_at": _iso(datetime.now(timezone.utc)), "lists": lists, "errors": errors}


def load_source_dates(source: Optional[str] = None, live: bool = False) -> Dict[str, Any]:
    if live:
        return live_source_dates()
    src = source or SOURCES_URL
    if src.startswith(("http://", "https://")):
        data = _http(src)[0]
    else:
        data = Path(src).read_bytes()
    out = json.loads(data.decode("utf-8"))
    if not isinstance(out, dict) or "lists" not in out:
        raise ValueError(f"{src}: expected a sources.json object with 'lists'")
    return out


# ---------------------------------------------------------------- reading inputs

def _git_date(path: Path) -> Optional[str]:
    try:
        p = subprocess.run(["git", "log", "-1", "--format=%cI", "--", path.name], cwd=str(path.parent),
                           capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    if p.returncode != 0 or not p.stdout.strip():
        return None
    try:
        shallow = subprocess.run(["git", "rev-parse", "--is-shallow-repository"], cwd=str(path.parent),
                                 capture_output=True, text=True, timeout=30).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        shallow = ""
    if shallow == "true":
        return "shallow"  # a shallow clone dates every file to its one commit
    dt = _parse_dt(p.stdout.strip())
    return _iso(dt) if dt else None


def _file_date(path: Path) -> Tuple[Optional[str], str]:
    g = _git_date(path)
    if g == "shallow":
        return None, "undated: shallow git clone (check out with fetch-depth: 0)"
    if g:
        return g, "git: last commit touching the file"
    return _iso(datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)), "file modification time"


def _iter_json_rows(obj: Any) -> Iterator[Dict[str, Any]]:
    if isinstance(obj, list):
        for x in obj:
            if isinstance(x, dict):
                yield x
        return
    if isinstance(obj, dict):
        for k in ("results", "entities", "records", "data", "items", "entries", "parties", "sdnEntry", "rows"):
            v = obj.get(k)
            if isinstance(v, list) and v and isinstance(v[0], dict):
                yield from (x for x in v if isinstance(x, dict))
                return
        for v in obj.values():
            if isinstance(v, list) and v and isinstance(v[0], dict) and any(
                    str(kk).lower() in NAME_KEYS for kk in v[0]):
                yield from (x for x in v if isinstance(x, dict))
                return


_OFAC_CSV_COLS = ("ent_num", "SDN_Name", "SDN_Type", "Program", "Title", "Call_Sign", "Vess_type", "Tonnage", "GRT",
                  "Vess_flag", "Vess_owner", "Remarks")


def _csv_rows(text: str) -> Iterator[Dict[str, Any]]:
    sample = text[:20000]
    delim = "\t" if sample.count("\t") > sample.count(",") else ","
    rdr = csv.reader(io.StringIO(text), delimiter=delim)
    try:
        head = next(rdr)
    except StopIteration:
        return
    if head and head[0].strip().isdigit() and len(head) >= 10:  # OFAC legacy sdn.csv / cons_prim.csv: no header
        for r in [head] + list(rdr):
            if len(r) >= 2 and r[0].strip().isdigit():
                yield {"name": "" if r[1].strip() == "-0-" else r[1]}
        return
    cols = [h.strip() for h in head]
    for r in rdr:
        if r:
            yield dict(zip(cols, r))


def _xml_rows(path: Path) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    """OFAC sdn.xml / consolidated.xml (sdnEntry) and sdn_advanced.xml (DistinctParty)."""
    rows: List[Dict[str, Any]] = []
    pub: Optional[str] = None
    for _ev, el in ET.iterparse(str(path)):
        tag = el.tag.rsplit("}", 1)[-1]
        if tag == "Publish_Date" and el.text:
            dt = _parse_dt(el.text)
            pub = _iso(dt) if dt else pub
        elif tag == "DateOfIssue":
            parts = {c.tag.rsplit("}", 1)[-1]: (c.text or "") for c in el}
            if parts.get("Year"):
                try:
                    pub = _iso(datetime(int(parts["Year"]), int(parts.get("Month") or 1), int(parts.get("Day") or 1),
                                        tzinfo=timezone.utc))
                except ValueError:
                    pass
        elif tag == "sdnEntry":
            last = first = ""
            for c in el:
                ct = c.tag.rsplit("}", 1)[-1]
                if ct == "lastName":
                    last = c.text or ""
                elif ct == "firstName":
                    first = c.text or ""
            rows.append({"name": f"{first} {last}".strip()})
            el.clear()
        elif tag == "DistinctParty":
            vals = [x.text or "" for x in el.iter() if x.tag.rsplit("}", 1)[-1] == "NamePartValue"]
            rows.append({"name": " ".join(v.strip() for v in vals).strip()})
            el.clear()
    return rows, pub


@dataclass
class _Found:
    evidence: Dict[str, List[Evidence]] = field(default_factory=dict)
    opensanctions_refs: List[Tuple[str, Evidence]] = field(default_factory=list)
    # dataset names and topic filters seen in config keys; they count once any input talks to OpenSanctions
    opensanctions_weak: List[Tuple[str, Evidence]] = field(default_factory=list)
    topic_filters: List[Tuple[str, Evidence]] = field(default_factory=list)
    opensanctions_seen: bool = False
    filtered: Dict[str, str] = field(default_factory=dict)

    def add(self, lid: str, ev: Evidence) -> None:
        self.evidence.setdefault(lid, []).append(ev)


def _data_file(path: Path, rel: str, found: _Found, aside: bool) -> bool:
    """Read a list data file; True when it carried at least one list."""
    name = path.name.lower()
    by_name = None
    for s in SPECS:
        if s.filename is not None and s.filename.match(name):
            by_name = s.id
            break
    ext = path.suffix.lower()
    if name in ("consolidated.xml",):
        by_name = "ofac_nonsdn"
    size = path.stat().st_size
    if size == 0 or size > MAX_DATA:
        return False
    rows: List[Dict[str, Any]] = []
    embedded: Dict[str, str] = {}
    try:
        if ext == ".xml":
            if not by_name and name not in ("sdn.xml", "sdn_advanced.xml", "cons_advanced.xml"):
                return False
            rows, pub = _xml_rows(path)
            if pub and by_name:
                embedded[by_name] = pub
        else:
            text = path.read_text(encoding="utf-8", errors="replace")
            if not by_name and not re.search(r"source|data_?source|datasets?|list_?name|[\"']list[\"']\s*:",
                                             text[:200000], re.I):
                return False
            if ext in (".jsonl", ".ndjson") or (ext in (".json", ".txt") and text.lstrip().startswith("{")
                                                  and "\n{" in text[:2000000]):
                for line in text.splitlines():
                    line = line.strip()
                    if line.startswith("{"):
                        try:
                            obj = json.loads(line)
                        except ValueError:
                            continue
                        if isinstance(obj, dict):
                            rows.append(obj)
            elif ext == ".json":
                try:
                    obj = json.loads(text)
                except ValueError:
                    return False
                rows = list(_iter_json_rows(obj))
                if isinstance(obj, dict):
                    for s in obj.get("sources_used") or []:  # trade.gov CSL
                        lid = classify_value(str(s.get("source") or ""))
                        dt = _parse_dt(s.get("source_last_updated"))
                        if lid and dt and (lid not in embedded or _iso(dt) < embedded[lid]):
                            embedded[lid] = _iso(dt)
            elif ext in (".csv", ".tsv"):
                rows = list(_csv_rows(text))
            else:
                return False
    except (OSError, ET.ParseError, csv.Error, UnicodeDecodeError):
        return False
    if not rows:
        return False
    is_csl = any(str(k).lower() == "source" for k in rows[0]) and any(
        re.search(r"bureau of industry|treasury department|state department", str(r.get("source", "")), re.I)
        for r in rows[:2000])
    if is_csl and embedded:
        embedded["csl"] = max(embedded.values())
    per: Dict[str, List[int]] = {}
    latest_seen: Dict[str, str] = {}
    for r in rows:
        if str(r.get("op") or r.get("action") or r.get("change") or "").lower() in REMOVAL_OPS:
            continue  # a removal event in a change feed carries no record
        lids = _row_lists(r, by_name)
        empty = 0 if row_names(r) else 1
        seen = r.get("last_seen") or r.get("last_change")
        for lid in lids:
            c = per.setdefault(lid, [0, 0])
            c[0] += 1
            c[1] += empty
            if isinstance(seen, str) and seen > latest_seen.get(lid, ""):
                latest_seen[lid] = seen
    if not per:
        return False
    # this feed's own files: manifest.json next to them carries data_as_of per list
    side: Dict[str, str] = {}
    mpath = path.with_name("manifest.json")
    if mpath.is_file() and mpath != path:
        try:
            man = json.loads(mpath.read_text(encoding="utf-8"))
            for key, lid in FEED_KEYS.items():
                d = ((man.get("lists") or {}).get(key) or {}).get("data_as_of")
                if d:
                    side[lid] = d
        except (OSError, ValueError):
            pass
    fdate: Optional[str] = None
    fbasis = ""
    dated = False
    if is_csl:
        per.setdefault("csl", [len(rows), sum(0 if row_names(r) else 1 for r in rows)])
    for lid, (n, empty) in per.items():
        if lid in embedded:
            d, basis = embedded[lid], "date inside the file"
        elif lid in side:
            d, basis = side[lid], "manifest.json data_as_of"
        elif lid in latest_seen:
            dt = _parse_dt(latest_seen[lid])
            d, basis = (_iso(dt) if dt else latest_seen[lid]), "newest last_seen of its records"
        else:
            if not dated:
                fdate, fbasis = _file_date(path)
                dated = True
            d, basis = fdate, fbasis
        found.add(lid, Evidence("data", rel, rows=n, empty_names=empty, data_date=d, date_basis=basis,
                                detail=f"{n} records" + (" (trade.gov CSL rows)" if lid == "csl" else ""),
                                test_or_doc=aside, inherit=lid == "csl" and not is_csl))
    return True


def _refs(path: Path, rel: str, found: _Found, aside: bool) -> None:
    try:
        if path.stat().st_size > MAX_TEXT:
            return
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return
    lines = text.splitlines()
    os_file = bool(re.search(r"opensanctions|yente", text, re.I))
    for i, line in enumerate(lines, 1):
        if len(line) > 5000:
            line = line[:5000]
        for s in SPECS:
            m = s.ref.search(line)
            if not m:
                continue
            before = line[max(0, m.start() - 40):m.start()]
            if s.id == "bis_entity" and re.search(r"uflpa|forced[-_ ]labor", before, re.I):
                if not s.ref.search(line[m.end():]):
                    continue
            if s.id in ("bis_meu", "bis_uvl") and not re.search(
                    r"bis|end[-_ ]user|unverified|export|sanction|screen|list|csl|commerce", line, re.I):
                continue
            snap = SNAPSHOT.search(line)
            if snap and not aside:
                # a list copied into the code: "RestrictedAddresses were populated from OFAC's 2026-08-28 SDN.XML"
                found.add(s.id, Evidence("snapshot", rel, i, line.strip()[:200], data_date=snap.group(1) + "T23:59:59Z",
                                         date_basis="date of the copy named in the code"))
                continue
            found.add(s.id, Evidence("reference", rel, i, line.strip()[:200], test_or_doc=aside,
                                     inherit=s.id == "csl"))
        ev = Evidence("opensanctions", rel, i, line.strip()[:200], test_or_doc=aside)
        for m in re.finditer(r"data\.opensanctions\.org/datasets/[^/\s'\"]+/([a-z0-9_]+)"
                             r"|api\.opensanctions\.org/(?:match|search)/([a-z0-9_]+)", line, re.I):
            found.opensanctions_refs.append((next(g for g in m.groups() if g).lower(), ev))
        # /match/default, scope: "sanctions", dataset = z.literal('default'), scope = options.scope ?? "default"
        for m in re.finditer(r"(?:/match/|/search/)([a-z0-9_]+)\b"
                             r"|\b(?:scope|dataset|datasets|collection|collections)\b[\"']?\s*[:=]\s*[\[\"']*\s*"
                             r"([a-z][a-z0-9_]+)"
                             r"|\b(?:scope|dataset|collection)\b[^\n\"']{0,40}?(?:\?\?|\|\||\bor\b|literal\()\s*[\"']"
                             r"([a-z][a-z0-9_]+)[\"']", line, re.I):
            if os_file:
                found.opensanctions_refs.append((next(g for g in m.groups() if g).lower(), ev))
            elif SCREEN_PATH.search(rel):  # a screening policy file next to OpenSanctions code elsewhere
                found.opensanctions_weak.append((next(g for g in m.groups() if g).lower(), ev))
        # a topics filter: a literal list of quoted OpenSanctions topics, a topics= query parameter, or
        # searchParams.append("topics", "sanction")
        for m in re.finditer(r"\btopics\b[\"']?\s*[:=]\s*\[((?:\s*[\"'][a-z.]+[\"']\s*,?)+)\s*\]"
                             r"|\btopics=([a-z.]+)|[\"']topics[\"']\s*,\s*[\"']([a-z.]+)[\"']", line):
            for t in re.findall(r"[a-z]+(?:\.[a-z]+)*", " ".join(g for g in m.groups() if g)):
                if OPENSANCTIONS_TOPIC.match(t) and (os_file or SCREEN_PATH.search(rel)):
                    found.topic_filters.append((t, ev))
    if os_file and not aside:
        found.opensanctions_seen = True


def scan(paths: Sequence[str], include_tests: bool = False, include_docs: bool = False) -> _Found:
    found = _Found()
    for p in paths:
        root = Path(p)
        if root.is_file():
            files = [(root, root.name)]
        elif root.is_dir():
            files = []
            for dirpath, dirnames, filenames in os.walk(root):
                dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
                for fn in sorted(filenames):
                    fp = Path(dirpath) / fn
                    files.append((fp, fp.relative_to(root).as_posix()))
        else:
            raise ValueError(f"{p}: no such file or directory")
        for fp, rel in files:
            ext = fp.suffix.lower()
            # an agent's rule or skill file is its configuration, not documentation
            is_doc = (ext in DOC_EXT or bool(DOC_PATH.search(rel))) and not AGENT_RULES.search(rel)
            is_test = bool(TEST_PATH.search(rel))
            aside = (is_test and not include_tests) or (is_doc and not include_docs)
            try:
                if ext in DATA_EXT and _data_file(fp, rel, found, aside=is_test and not include_tests):
                    continue
                if ext in CODE_EXT or ext in DOC_EXT or fp.name.lower() in ("dockerfile", "makefile", ".env.example"):
                    _refs(fp, rel, found, aside)
            except OSError:  # unreadable file (permissions, path too long): skip it, keep scanning
                continue
    return found


def _expand_opensanctions(found: _Found, index: Optional[Dict[str, Any]]) -> None:
    if found.opensanctions_seen:
        found.opensanctions_refs.extend(found.opensanctions_weak)
        found.opensanctions_weak = []
    if not found.opensanctions_refs or index is None:
        return
    topics = sorted({t for t, e in found.topic_filters if not e.test_or_doc}) if found.opensanctions_seen else []
    topic_at = next((e.where() for t, e in found.topic_filters if not e.test_or_doc), "")
    ds = {d["name"]: d for d in index.get("datasets") or [] if isinstance(d, dict) and d.get("name")}

    def leaves(name: str, seen: Set[str]) -> Set[str]:
        d = ds.get(name)
        if d is None or name in seen:
            return set()
        seen.add(name)
        if d.get("type") == "collection" or d.get("children"):
            out: Set[str] = set()
            for c in d.get("children") or d.get("datasets") or []:
                out |= leaves(c, seen)
            return out
        return {name}

    for name, ev in found.opensanctions_refs:
        if name not in ds:
            continue
        for leaf in sorted(leaves(name, set())):
            for i, lid in enumerate(OPENSANCTIONS.get(leaf, ())):
                carried = OPENSANCTIONS_TOPICS.get(lid, ())
                if topics and not set(carried) & set(topics):
                    found.filtered.setdefault(lid, (
                        f"OpenSanctions '{name}' includes {leaf}, but the topics filter {topics} ({topic_at}) "
                        "drops it: "
                        f"OpenSanctions tags these entities {', '.join(repr(t) for t in carried)}"))
                    continue
                last = ds[leaf].get("last_change")
                dt = _parse_dt(last)
                found.add(lid, Evidence("opensanctions", ev.path, ev.line,
                                        f"OpenSanctions '{name}' includes {leaf}" + (" (via the CSL)" if i else ""),
                                        data_date=_iso(dt) if dt else None,
                                        date_basis=f"OpenSanctions {leaf} last_change" if dt else None,
                                        test_or_doc=ev.test_or_doc))


# ---------------------------------------------------------------- the check

def check(paths: Sequence[str], sources: Dict[str, Any], require: Optional[Iterable[str]] = None,
          skip: Iterable[str] = (), opensanctions_index: Optional[Dict[str, Any]] = None,
          include_tests: bool = False, include_docs: bool = False, now: Optional[datetime] = None) -> Report:
    found = scan(paths, include_tests=include_tests, include_docs=include_docs)
    _expand_opensanctions(found, opensanctions_index)
    return evaluate(found, sources, paths, require, skip, now)


def evaluate(found: _Found, sources: Dict[str, Any], paths: Sequence[str], require: Optional[Iterable[str]] = None,
             skip: Iterable[str] = (), now: Optional[datetime] = None) -> Report:
    now = now or datetime.now(timezone.utc)
    src = sources.get("lists") or {}
    skip_set = set(skip)
    req_set = set(require) if require is not None else None
    for lid in (req_set or set()) | skip_set:
        if lid not in SPEC:
            raise ValueError(f"unknown list id {lid!r}; expected one of {', '.join(SPEC)}")
    results: List[ListResult] = []
    # a CSL reference, or CSL rows not split by member list, brings the lists inside the CSL
    csl_ev = [e for e in found.evidence.get("csl", []) if not e.test_or_doc and e.inherit]
    for s in (SPEC[i] for i in ORDER):
        info = src.get(s.id) or {}
        upd = info.get("updated")
        published = info.get("published", True)
        required = (s.id in req_set) if req_set is not None else (s.id not in skip_set)
        if s.id == "biosecure" and not published and (req_set is None or s.id not in req_set):
            required = False
        evs = [e for e in found.evidence.get(s.id, []) if not e.test_or_doc]
        aside = [e for e in found.evidence.get(s.id, []) if e.test_or_doc]
        if s.in_csl and not any(e.kind in ("data", "opensanctions", "snapshot") for e in evs):
            for e in csl_ev:
                evs.append(Evidence(e.kind, e.path, e.line, "via the CSL: " + (e.detail or "CSL"), rows=0,
                                    data_date=e.data_date, date_basis=e.date_basis))
        data = [e for e in evs if e.kind in ("data", "opensanctions", "snapshot")]
        refs = [e for e in evs if e.kind == "reference"]
        status, reason = "missing", "nothing in the input names, references or contains this list"
        if aside and not evs:
            reason += f" (only in tests or docs: {aside[0].where()})"
        if s.id in found.filtered and not evs:
            reason = found.filtered[s.id]
        if s.id == "biosecure" and not published and not evs:
            status, reason = "not_published", info.get("basis") or "OMB has not published its list"
        if data:
            src_dt = _parse_dt(upd)
            grace = timedelta(days=3) if s.federal_register else timedelta(hours=1)
            empty = sum(e.empty_names for e in data)
            rows = sum(e.rows for e in data)
            dated = [e for e in data if _parse_dt(e.data_date)]
            fresh = [e for e in dated if src_dt is None or (_parse_dt(e.data_date) or now) + grace >= src_dt]
            if empty and rows:
                worst = max(data, key=lambda e: e.empty_names)
                status = "empty_names"
                reason = f"{empty} of {rows} records have no name ({worst.where()})"
            elif not dated:
                status = "stale"
                reason = f"no data date for {data[0].where()}: {data[0].date_basis}"
            elif not fresh:
                newest = max(dated, key=lambda e: e.data_date or "")
                status = "stale"
                reason = (f"data as of {newest.data_date} ({newest.date_basis}, {newest.where()}); the agency updated "
                          f"it {upd} ({info.get('basis') or 'sources.json'})")
            else:
                status = "ok"
                best = max(fresh, key=lambda e: e.data_date or "")
                reason = f"data as of {best.data_date} ({best.date_basis}, {best.where()})" + (
                    f"; agency update {upd}" if upd else "; agency update date unknown")
        elif refs:
            status = "declared"
            reason = f"named in {refs[0].where()}; fetched at run time, no local data to check"
        results.append(ListResult(s.id, s.title, status, required, upd, reason, evs[:20] or aside[:3]))
    return Report([str(p) for p in paths], _iso(now), sources.get("generated_at"), results)


# ---------------------------------------------------------------- output

_MARK = {"ok": "ok", "declared": "declared", "missing": "MISSING", "stale": "STALE", "empty_names": "EMPTY NAMES",
         "not_published": "not published"}


def render_text(rep: Report) -> str:
    out = [f"US restricted list coverage of {', '.join(rep.inputs)} (agency dates: sources.json "
           f"{rep.sources_generated_at or 'unknown'})", ""]
    w = max(len(r.id) for r in rep.results)
    for r in rep.results:
        flag = _MARK[r.status] + ("" if r.required or r.status in ("ok", "declared", "not_published") else " (skipped)")
        out.append(f"{r.id:<{w}}  {flag:<14} {r.reason}")
    out.append("")
    if rep.failed:
        out.append(f"FAIL: {len(rep.failed)} required list(s) missing, stale or empty: "
                   + ", ".join(r.id for r in rep.failed))
    else:
        out.append("PASS: every required US restricted list is present, current and has names.")
    return "\n".join(out)


def render_markdown(rep: Report) -> str:
    out = ["### US restricted list coverage", "", "| List | Status | Detail |", "|---|---|---|"]
    for r in rep.results:
        out.append(f"| {r.title} (`{r.id}`) | {_MARK[r.status]}{'' if r.required else ' (not required)'} | "
                   f"{r.reason.replace('|', '/')} |")
    out.append("")
    out.append("**FAIL**: " + ", ".join(f"`{r.id}`" for r in rep.failed) if rep.failed
               else "**PASS**: every required list is present, current and has names.")
    return "\n".join(out) + "\n"


def github_annotations(rep: Report) -> List[str]:
    out = []
    for r in rep.failed:
        ev = next((e for e in r.evidence if e.kind == "data"), None)
        loc = ""
        if ev is not None:
            loc = f" file={ev.path}" + (f",line={ev.line}" if ev.line else "") + ","
        msg = f"{r.title}: {r.reason}".replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
        out.append(f"::error{loc or ' '}title={r.id} {r.status}::{msg}")
    return out


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="us-restricted-parties coverage",
                                 description="Fail when a sanctions screen is missing a US restricted list, runs on "
                                             "stale data, or loaded a list with empty names.")
    ap.add_argument("paths", nargs="*", help="config files, list directories, index dumps or a source tree")
    ap.add_argument("--sources", help="sources.json file or URL with the agencies' update dates "
                                      f"(default {SOURCES_URL})")
    ap.add_argument("--sources-live", action="store_true", help="read the update dates from the agencies now")
    ap.add_argument("--print-sources", action="store_true", help="print the agencies' update dates as sources.json")
    ap.add_argument("--manifest", help="with --print-sources: a local manifest.json of this feed instead of the latest")
    ap.add_argument("--require", help="comma-separated list ids to require instead of all")
    ap.add_argument("--skip", default="", help="comma-separated list ids not to require (e.g. far_889)")
    ap.add_argument("--opensanctions-index", help="OpenSanctions index.json (file or URL) to expand dataset and "
                                                  f"collection names; fetched from {OPENSANCTIONS_INDEX_URL} "
                                                  "when needed")
    ap.add_argument("--include-tests", action="store_true", help="count lists found only in tests and fixtures")
    ap.add_argument("--include-docs", action="store_true", help="count lists named only in documentation")
    ap.add_argument("--json", action="store_true", help="print the report as JSON")
    ap.add_argument("--report", help="also write the JSON report to this file")
    ap.add_argument("--github", action="store_true", help="also write error annotations and $GITHUB_STEP_SUMMARY")
    ap.add_argument("--list-ids", action="store_true", help="print the list ids and exit")
    a = ap.parse_args(argv)
    if a.list_ids:
        for s in (SPEC[i] for i in ORDER):
            print(f"{s.id:<12} {s.title} ({s.agency})")
        return 0
    try:
        if a.print_sources:
            man = json.loads(Path(a.manifest).read_text(encoding="utf-8")) if a.manifest else None
            print(json.dumps(live_source_dates(manifest=man), indent=1, sort_keys=True))
            return 0
        if not a.paths:
            ap.error("give at least one path")
        sources = load_source_dates(a.sources, live=a.sources_live)
        found = scan(a.paths, include_tests=a.include_tests, include_docs=a.include_docs)
        index = None
        if found.opensanctions_refs:
            loc = a.opensanctions_index or OPENSANCTIONS_INDEX_URL
            raw = _http(loc)[0] if loc.startswith(("http://", "https://")) else Path(loc).read_bytes()
            index = json.loads(raw.decode("utf-8"))
        _expand_opensanctions(found, index)
        require = [x for x in a.require.split(",") if x] if a.require else None
        rep = evaluate(found, sources, a.paths, require, [x for x in a.skip.split(",") if x])
    except (OSError, ValueError) as e:
        print(f"us-restricted-parties coverage: {e}", file=sys.stderr)
        return 2
    print(json.dumps(rep.to_json(), indent=1) if a.json else render_text(rep))
    if a.report:
        Path(a.report).write_text(json.dumps(rep.to_json(), indent=1) + "\n", encoding="utf-8")
    if a.github:
        for line in github_annotations(rep):
            print(line)
        summary = os.environ.get("GITHUB_STEP_SUMMARY")
        if summary:
            with open(summary, "a", encoding="utf-8") as f:
                f.write(render_markdown(rep))
    return 1 if rep.failed else 0


if __name__ == "__main__":
    sys.exit(main())

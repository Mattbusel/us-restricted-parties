// Package restrictedparties loads the US Restricted Parties feed: DoD 1260H (with named
// subsidiaries), Section 889, the FCC Covered List, the DHS UFLPA Entity List and the interim
// BIOSECURE list, as published daily in Senzing JSON Lines at
// https://github.com/Mattbusel/us-restricted-parties, and the derived affiliates feed (companies
// the listed parties own or control). Standard library only.
//
//	recs, err := restrictedparties.Load(ctx, restrictedparties.UFLPA)
//	for _, r := range recs {
//		fmt.Println(r.NameOrg, r.Names(), r.ListedOn, r.SourceURL)
//	}
//
// For reproducible screening, pin a data release and check each file against its manifest:
//
//	src := restrictedparties.Source{Release: "data-2026-11-17-2241", Verify: true}
//	recs, err := src.Load(ctx, restrictedparties.UFLPA)
//	affs, err := src.LoadAffiliates(ctx, restrictedparties.AffiliatesCovered)
//
// Data: CC BY 4.0, "US Restricted Parties feed by Tensorust LLC". The fields are described list
// by list in docs/lists.md, the affiliates in docs/affiliates.md.
package restrictedparties

import (
	"bufio"
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"strings"
)

// LatestURL is where each list's latest file is published; append "<list>.jsonl".
const LatestURL = "https://github.com/Mattbusel/us-restricted-parties/releases/latest/download/"

// ReleasesURL + "<tag>/" is where a fixed data release (such as "data-2026-11-17-2241") keeps
// its files. Data releases are kept, so a pinned URL keeps returning the same bytes.
const ReleasesURL = "https://github.com/Mattbusel/us-restricted-parties/releases/download/"

// The lists, as file names (and, upper-cased, as DATA_SOURCE values).
const (
	All       = "all"
	DoD1260H  = "us_dod_1260h"
	FAR889    = "us_far_889"
	FCC       = "us_fcc_covered"
	UFLPA     = "us_dhs_uflpa"
	BIOSECURE = "us_biosecure"
)

// The derived affiliates feed: companies the listed parties own or control under the OFAC 50
// Percent Rule and the BIS Affiliates Rule. Screen against AffiliatesCovered; AffiliatesReview is
// control without a 50 percent share; Affiliates holds both.
const (
	AffiliatesCovered = "affiliates.covered"
	AffiliatesReview  = "affiliates.review"
	Affiliates        = "affiliates"
)

// Lists holds every list name except All.
var Lists = []string{DoD1260H, FAR889, FCC, UFLPA, BIOSECURE}

// Name is one entry of a record's NAMES: PRIMARY or ALIAS.
type Name struct {
	NameType string `json:"NAME_TYPE"`
	NameOrg  string `json:"NAME_ORG"`
}

// Relationship is a Senzing relationship: a named subsidiary points at its parent with
// REL_POINTER_ROLE "SUBSIDIARY_OF"; the parent carries the matching anchor.
type Relationship struct {
	AnchorDomain  string `json:"REL_ANCHOR_DOMAIN,omitempty"`
	AnchorKey     string `json:"REL_ANCHOR_KEY,omitempty"`
	PointerDomain string `json:"REL_POINTER_DOMAIN,omitempty"`
	PointerKey    string `json:"REL_POINTER_KEY,omitempty"`
	PointerRole   string `json:"REL_POINTER_ROLE,omitempty"`
}

// Record is one line of a list file.
type Record struct {
	DataSource         string         `json:"DATA_SOURCE"`
	RecordID           string         `json:"RECORD_ID"`
	RecordType         string         `json:"RECORD_TYPE"`
	NameOrg            string         `json:"NAME_ORG"`
	NamesList          []Name         `json:"NAMES"`
	ListName           string         `json:"LIST_NAME"`
	ListedOn           string         `json:"LISTED_ON"`
	SourceURL          string         `json:"SOURCE_URL"`
	SourceDoc          string         `json:"SOURCE_DOC"`
	SourceText         string         `json:"SOURCE_TEXT"`
	ParentName         string         `json:"PARENT_NAME,omitempty"`
	Relationships      []Relationship `json:"RELATIONSHIPS,omitempty"`
	Basis              string         `json:"BASIS,omitempty"`
	IncludesAffiliates bool           `json:"INCLUDES_AFFILIATES,omitempty"`
}

// Names returns the primary name first, then every alias, without duplicates.
func (r Record) Names() []string {
	return names(r.NameOrg, r.NamesList)
}

func names(primary string, list []Name) []string {
	out := []string{primary}
	seen := map[string]bool{primary: true}
	for _, n := range list {
		if n.NameOrg != "" && !seen[n.NameOrg] {
			seen[n.NameOrg] = true
			out = append(out, n.NameOrg)
		}
	}
	return out
}

// ParentID returns the RECORD_ID of the listed parent for a named subsidiary, or "".
func (r Record) ParentID() string {
	for _, rel := range r.Relationships {
		if rel.PointerRole == "SUBSIDIARY_OF" {
			return rel.PointerKey
		}
	}
	return ""
}

// Affiliate is one record of an affiliates file: a company that listed parties own or control,
// with the verdict and proof path under each rule. It is evidence for a review, not a
// determination.
type Affiliate struct {
	DataSource          string         `json:"DATA_SOURCE"`
	RecordID            string         `json:"RECORD_ID"`
	RecordType          string         `json:"RECORD_TYPE"`
	NameOrg             string         `json:"NAME_ORG"`
	NamesList           []Name         `json:"NAMES"`
	ListName            string         `json:"LIST_NAME"`
	LEINumber           string         `json:"LEI_NUMBER,omitempty"`
	RegistrationCountry string         `json:"REGISTRATION_COUNTRY,omitempty"`
	GLEIFEntityStatus   string         `json:"GLEIF_ENTITY_STATUS,omitempty"`
	EvidenceState       string         `json:"EVIDENCE_STATE"`
	VerdictClass        string         `json:"VERDICT_CLASS"`
	Relationships       []Relationship `json:"RELATIONSHIPS,omitempty"`
	// OwnershipVerdicts maps a rule ("ofac-50", "bis-affiliates", "any-list-50") to its
	// verdict: "definitely", "possibly" or "control_only".
	OwnershipVerdicts map[string]string `json:"OWNERSHIP_VERDICTS"`
	// OwnershipProof maps a rule to its proof path, as ownership-rules writes it.
	OwnershipProof map[string]json.RawMessage `json:"OWNERSHIP_PROOF"`
	// ListedOn names the lists the company is itself on, when a rule reaches it as well.
	ListedOn     []string `json:"LISTED_ON,omitempty"`
	InputsSHA256 string   `json:"INPUTS_SHA256,omitempty"`
}

// Names returns the primary name first, then every alias, without duplicates.
func (a Affiliate) Names() []string {
	return names(a.NameOrg, a.NamesList)
}

func checkList(list string) error {
	if list == All {
		return nil
	}
	for _, l := range Lists {
		if l == list {
			return nil
		}
	}
	return fmt.Errorf("restrictedparties: unknown list %q (want %q or one of %s)", list, All, strings.Join(Lists, ", "))
}

func checkAffiliates(set string) error {
	switch set {
	case AffiliatesCovered, AffiliatesReview, Affiliates:
		return nil
	}
	return fmt.Errorf("restrictedparties: unknown affiliates set %q (want %q, %q or %q)",
		set, AffiliatesCovered, AffiliatesReview, Affiliates)
}

// FileName returns the release asset name of a list ("us_dhs_uflpa.jsonl", "all.jsonl") or of
// an affiliates set ("affiliates.covered.senzing.jsonl").
func FileName(list string) (string, error) {
	if checkAffiliates(list) == nil {
		return list + ".senzing.jsonl", nil
	}
	if err := checkList(list); err != nil {
		return "", err
	}
	return list + ".jsonl", nil
}

func scanLines(r io.Reader, f func(line int, b []byte) error) error {
	sc := bufio.NewScanner(r)
	sc.Buffer(make([]byte, 0, 64*1024), 16*1024*1024)
	line := 0
	for sc.Scan() {
		line++
		b := bytes.TrimSpace(sc.Bytes())
		if len(b) == 0 {
			continue
		}
		if err := f(line, b); err != nil {
			return err
		}
	}
	return sc.Err()
}

// Read parses JSON Lines from r. With a list other than All, records of other lists are
// left out (so a local all.jsonl can be filtered).
func Read(r io.Reader, list string) ([]Record, error) {
	if err := checkList(list); err != nil {
		return nil, err
	}
	want := strings.ToUpper(list)
	var out []Record
	err := scanLines(r, func(line int, b []byte) error {
		var rec Record
		if err := json.Unmarshal(b, &rec); err != nil {
			return fmt.Errorf("restrictedparties: line %d: %w", line, err)
		}
		if list == All || rec.DataSource == want {
			out = append(out, rec)
		}
		return nil
	})
	return out, err
}

// ReadAffiliates parses an affiliates JSON Lines file.
func ReadAffiliates(r io.Reader) ([]Affiliate, error) {
	var out []Affiliate
	err := scanLines(r, func(line int, b []byte) error {
		var a Affiliate
		if err := json.Unmarshal(b, &a); err != nil {
			return fmt.Errorf("restrictedparties: line %d: %w", line, err)
		}
		out = append(out, a)
		return nil
	})
	return out, err
}

// Load downloads the latest release file of one list (or All) with http.DefaultClient.
func Load(ctx context.Context, list string) ([]Record, error) {
	return Source{}.Load(ctx, list)
}

// LoadFrom downloads url with client and parses it as the given list.
func LoadFrom(ctx context.Context, client *http.Client, url, list string) ([]Record, error) {
	if err := checkList(list); err != nil {
		return nil, err
	}
	b, err := get(ctx, client, url)
	if err != nil {
		return nil, err
	}
	return Read(bytes.NewReader(b), list)
}

// FileInfo is a file's record count and SHA-256 in a manifest.
type FileInfo struct {
	Records int    `json:"records"`
	SHA256  string `json:"sha256"`
}

// AffiliatesManifest describes the affiliates build of a release.
type AffiliatesManifest struct {
	Files         map[string]FileInfo `json:"files"`
	DataDate      string              `json:"data_date"`
	Stale         bool                `json:"stale"`
	EvidenceState string              `json:"evidence_state"`
	GLEIFCoverage string              `json:"gleif_coverage"`
	Error         string              `json:"error,omitempty"`
}

// Manifest is the part of a release's manifest.json most callers need.
type Manifest struct {
	BuiltAt       string `json:"built_at"`
	ContentSHA256 string `json:"content_sha256"`
	Lists         map[string]struct {
		DataSource string `json:"data_source"`
		Name       string `json:"name"`
		Operator   string `json:"operator"`
		Source     string `json:"source"`
		Records    int    `json:"records"`
		Aliases    int    `json:"aliases"`
		SHA256     string `json:"sha256"`
		FetchedAt  string `json:"fetched_at"`
	} `json:"lists"`
	Affiliates *AffiliatesManifest `json:"affiliates,omitempty"`
	Errors     json.RawMessage     `json:"errors"`
}

// SHA256 returns the hash the manifest gives for a list or affiliates set. All has none;
// verify the single lists instead.
func (m *Manifest) SHA256(list string) (string, error) {
	name, err := FileName(list)
	if err != nil {
		return "", err
	}
	if l, ok := m.Lists[list]; ok && l.SHA256 != "" {
		return l.SHA256, nil
	}
	if m.Affiliates != nil {
		if f, ok := m.Affiliates.Files[name]; ok && f.SHA256 != "" {
			return f.SHA256, nil
		}
	}
	return "", fmt.Errorf("restrictedparties: manifest.json has no hash for %s", name)
}

// LoadManifest downloads the latest release's manifest.json.
func LoadManifest(ctx context.Context) (*Manifest, error) {
	return Source{}.Manifest(ctx)
}

// Source says where files come from. The zero value reads the latest release with
// http.DefaultClient and checks nothing.
type Source struct {
	// Client is the HTTP client; nil means http.DefaultClient.
	Client *http.Client
	// Release is a data release tag such as "data-2026-11-17-2241"; "" means the latest.
	Release string
	// Verify checks every list or affiliates file against the SHA-256 in the same release's
	// manifest.json before parsing it. All has no hash of its own, so Load(ctx, All) fails
	// with Verify set; load the single lists instead.
	Verify bool
}

// URL returns the download URL of a file of this source's release.
func (s Source) URL(file string) string {
	if s.Release == "" {
		return LatestURL + file
	}
	return ReleasesURL + s.Release + "/" + file
}

func get(ctx context.Context, client *http.Client, url string) ([]byte, error) {
	if client == nil {
		client = http.DefaultClient
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, url, nil)
	if err != nil {
		return nil, err
	}
	req.Header.Set("User-Agent", "us-restricted-parties-go/1.1")
	resp, err := client.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("restrictedparties: GET %s: %s", url, resp.Status)
	}
	return io.ReadAll(resp.Body)
}

// Manifest downloads this source's manifest.json.
func (s Source) Manifest(ctx context.Context) (*Manifest, error) {
	b, err := get(ctx, s.Client, s.URL("manifest.json"))
	if err != nil {
		return nil, err
	}
	var m Manifest
	if err := json.Unmarshal(b, &m); err != nil {
		return nil, fmt.Errorf("restrictedparties: manifest.json: %w", err)
	}
	return &m, nil
}

func (s Source) fetch(ctx context.Context, list string) ([]byte, error) {
	name, err := FileName(list)
	if err != nil {
		return nil, err
	}
	var m *Manifest
	if s.Verify {
		if m, err = s.Manifest(ctx); err != nil {
			return nil, err
		}
		if _, err = m.SHA256(list); err != nil {
			return nil, err
		}
	}
	b, err := get(ctx, s.Client, s.URL(name))
	if err != nil || m == nil {
		return b, err
	}
	want, _ := m.SHA256(list)
	sum := sha256.Sum256(b)
	if got := hex.EncodeToString(sum[:]); got != want {
		return nil, fmt.Errorf("restrictedparties: %s: SHA-256 %s does not match manifest.json (%s)", name, got, want)
	}
	return b, nil
}

// Load downloads one list (or All) from this source.
func (s Source) Load(ctx context.Context, list string) ([]Record, error) {
	if err := checkList(list); err != nil {
		return nil, err
	}
	b, err := s.fetch(ctx, list)
	if err != nil {
		return nil, err
	}
	return Read(bytes.NewReader(b), list)
}

// LoadAffiliates downloads an affiliates set (AffiliatesCovered, AffiliatesReview or Affiliates)
// from this source.
func (s Source) LoadAffiliates(ctx context.Context, set string) ([]Affiliate, error) {
	if err := checkAffiliates(set); err != nil {
		return nil, err
	}
	b, err := s.fetch(ctx, set)
	if err != nil {
		return nil, err
	}
	return ReadAffiliates(bytes.NewReader(b))
}

// Package restrictedparties loads the US Restricted Parties feed: DoD 1260H (with named
// subsidiaries), Section 889, the FCC Covered List, the DHS UFLPA Entity List and the interim
// BIOSECURE list, as published daily in Senzing JSON Lines at
// https://github.com/Mattbusel/us-restricted-parties. Standard library only.
//
//	recs, err := restrictedparties.Load(ctx, restrictedparties.UFLPA)
//	for _, r := range recs {
//		fmt.Println(r.NameOrg, r.Names(), r.ListedOn, r.SourceURL)
//	}
//
// Data: CC BY 4.0, "US Restricted Parties feed by Tensorust LLC". The fields are described list
// by list in docs/lists.md.
package restrictedparties

import (
	"bufio"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"strings"
)

// LatestURL is where each list's latest file is published; append "<list>.jsonl".
const LatestURL = "https://github.com/Mattbusel/us-restricted-parties/releases/latest/download/"

// The lists, as file names (and, upper-cased, as DATA_SOURCE values).
const (
	All       = "all"
	DoD1260H  = "us_dod_1260h"
	FAR889    = "us_far_889"
	FCC       = "us_fcc_covered"
	UFLPA     = "us_dhs_uflpa"
	BIOSECURE = "us_biosecure"
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
	out := []string{r.NameOrg}
	seen := map[string]bool{r.NameOrg: true}
	for _, n := range r.NamesList {
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

// Read parses JSON Lines from r. With a list other than All, records of other lists are
// left out (so a local all.jsonl can be filtered).
func Read(r io.Reader, list string) ([]Record, error) {
	if err := checkList(list); err != nil {
		return nil, err
	}
	want := strings.ToUpper(list)
	var out []Record
	sc := bufio.NewScanner(r)
	sc.Buffer(make([]byte, 0, 64*1024), 16*1024*1024)
	line := 0
	for sc.Scan() {
		line++
		b := strings.TrimSpace(sc.Text())
		if b == "" {
			continue
		}
		var rec Record
		if err := json.Unmarshal([]byte(b), &rec); err != nil {
			return nil, fmt.Errorf("restrictedparties: line %d: %w", line, err)
		}
		if list == All || rec.DataSource == want {
			out = append(out, rec)
		}
	}
	return out, sc.Err()
}

// Load downloads the latest release file of one list (or All) with http.DefaultClient.
func Load(ctx context.Context, list string) ([]Record, error) {
	return LoadFrom(ctx, http.DefaultClient, LatestURL+list+".jsonl", list)
}

// LoadFrom downloads url with client and parses it as the given list.
func LoadFrom(ctx context.Context, client *http.Client, url, list string) ([]Record, error) {
	if err := checkList(list); err != nil {
		return nil, err
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, url, nil)
	if err != nil {
		return nil, err
	}
	req.Header.Set("User-Agent", "us-restricted-parties-go/1.0")
	resp, err := client.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("restrictedparties: GET %s: %s", url, resp.Status)
	}
	return Read(resp.Body, list)
}

// Manifest is the part of a release's manifest.json most callers need.
type Manifest struct {
	BuiltAt string `json:"built_at"`
	Lists   map[string]struct {
		DataSource string `json:"data_source"`
		Name       string `json:"name"`
		Operator   string `json:"operator"`
		Source     string `json:"source"`
		Records    int    `json:"records"`
		Aliases    int    `json:"aliases"`
		SHA256     string `json:"sha256"`
		FetchedAt  string `json:"fetched_at"`
	} `json:"lists"`
	Errors json.RawMessage `json:"errors"`
}

// LoadManifest downloads the latest release's manifest.json.
func LoadManifest(ctx context.Context) (*Manifest, error) {
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, LatestURL+"manifest.json", nil)
	if err != nil {
		return nil, err
	}
	req.Header.Set("User-Agent", "us-restricted-parties-go/1.0")
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("restrictedparties: GET manifest.json: %s", resp.Status)
	}
	var m Manifest
	if err := json.NewDecoder(resp.Body).Decode(&m); err != nil {
		return nil, err
	}
	return &m, nil
}

package restrictedparties

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"net/http"
	"net/http/httptest"
	"os"
	"strings"
	"testing"
	"time"
)

func fixture(t *testing.T) *os.File {
	t.Helper()
	f, err := os.Open("../tests/fixtures/feed_excerpt.jsonl")
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { f.Close() })
	return f
}

func TestReadExcerpt(t *testing.T) {
	recs, err := Read(fixture(t), All)
	if err != nil {
		t.Fatal(err)
	}
	if len(recs) != 6 {
		t.Fatalf("got %d records, want 6", len(recs))
	}
	parent, sub := recs[0], recs[1]
	if parent.ParentID() != "" {
		t.Errorf("parent has parent %q", parent.ParentID())
	}
	if sub.ParentID() != parent.RecordID || sub.ParentName != parent.NameOrg {
		t.Errorf("subsidiary points at %q (%q), want %q", sub.ParentID(), sub.ParentName, parent.RecordID)
	}
	if names := parent.Names(); names[0] != parent.NameOrg || len(names) < 2 {
		t.Errorf("names = %q", names)
	}
	var far *Record
	for i := range recs {
		if recs[i].DataSource == "US_FAR_889" {
			far = &recs[i]
		}
	}
	if far == nil || !far.IncludesAffiliates {
		t.Errorf("FAR 889 record without INCLUDES_AFFILIATES: %+v", far)
	}
}

func TestReadFiltersOneList(t *testing.T) {
	recs, err := Read(fixture(t), DoD1260H)
	if err != nil {
		t.Fatal(err)
	}
	if len(recs) != 2 {
		t.Fatalf("got %d 1260H records, want 2", len(recs))
	}
	if _, err := Read(strings.NewReader(""), "ofac_sdn"); err == nil {
		t.Error("unknown list accepted")
	}
	if _, err := Read(strings.NewReader("{not json}\n"), All); err == nil {
		t.Error("bad line accepted")
	}
}

// URP_LIVE=1 go test ./... reads the latest release assets.
func TestLatestReleaseMatchesManifest(t *testing.T) {
	if os.Getenv("URP_LIVE") != "1" {
		t.Skip("set URP_LIVE=1 to read the latest release")
	}
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Minute)
	defer cancel()
	m, err := LoadManifest(ctx)
	if err != nil {
		t.Fatal(err)
	}
	total := 0
	for _, l := range Lists {
		recs, err := Load(ctx, l)
		if err != nil {
			t.Fatal(err)
		}
		if want := m.Lists[l].Records; len(recs) != want {
			t.Errorf("%s: %d records, manifest says %d", l, len(recs), want)
		}
		total += len(recs)
	}
	all, err := Load(ctx, All)
	if err != nil {
		t.Fatal(err)
	}
	if len(all) != total {
		t.Errorf("all.jsonl has %d records, the lists %d", len(all), total)
	}
}

func TestReadAffiliatesExcerpt(t *testing.T) {
	f, err := os.Open("../tests/fixtures/affiliates_excerpt.senzing.jsonl")
	if err != nil {
		t.Fatal(err)
	}
	defer f.Close()
	affs, err := ReadAffiliates(f)
	if err != nil {
		t.Fatal(err)
	}
	if len(affs) != 2 {
		t.Fatalf("got %d affiliates, want 2", len(affs))
	}
	for _, a := range affs {
		if a.VerdictClass != "covered" || a.DataSource != "US_RESTRICTED_AFFILIATES" || a.EvidenceState == "" {
			t.Errorf("unexpected affiliate %+v", a)
		}
		v := a.OwnershipVerdicts["ofac-50"] + a.OwnershipVerdicts["bis-affiliates"]
		if !strings.Contains(v, "definitely") && !strings.Contains(v, "possibly") {
			t.Errorf("%s: verdicts %v", a.RecordID, a.OwnershipVerdicts)
		}
		if len(a.OwnershipProof) == 0 || a.Names()[0] != a.NameOrg {
			t.Errorf("%s: no proof or names", a.RecordID)
		}
	}
}

func TestFileNamesAndURLs(t *testing.T) {
	for list, want := range map[string]string{
		UFLPA: "us_dhs_uflpa.jsonl", All: "all.jsonl", AffiliatesCovered: "affiliates.covered.senzing.jsonl",
	} {
		if got, err := FileName(list); err != nil || got != want {
			t.Errorf("FileName(%q) = %q, %v", list, got, err)
		}
	}
	if _, err := FileName("ofac_sdn"); err == nil {
		t.Error("unknown list accepted")
	}
	if got := (Source{Release: "data-2026-11-17-2241"}).URL("all.jsonl"); got != ReleasesURL+"data-2026-11-17-2241/all.jsonl" {
		t.Errorf("pinned URL %q", got)
	}
	if got := (Source{}).URL("all.jsonl"); got != LatestURL+"all.jsonl" {
		t.Errorf("latest URL %q", got)
	}
	if _, err := (Source{}).LoadAffiliates(context.Background(), UFLPA); err == nil {
		t.Error("a list name accepted as an affiliates set")
	}
}

// A local server stands in for a release: Verify accepts the file whose hash the manifest gives
// and rejects a changed one.
func TestVerifyAgainstManifest(t *testing.T) {
	feed, err := os.ReadFile("../tests/fixtures/feed_excerpt.jsonl")
	if err != nil {
		t.Fatal(err)
	}
	aff, err := os.ReadFile("../tests/fixtures/affiliates_excerpt.senzing.jsonl")
	if err != nil {
		t.Fatal(err)
	}
	hash := func(b []byte) string { s := sha256.Sum256(b); return hex.EncodeToString(s[:]) }
	manifest := `{"lists": {"us_dod_1260h": {"records": 2, "sha256": "` + hash(feed) + `"}},
		"affiliates": {"files": {"affiliates.covered.senzing.jsonl": {"records": 2, "sha256": "` + hash(aff) + `"}}}}`
	files := map[string][]byte{
		"/download/data-x/manifest.json":                    []byte(manifest),
		"/download/data-x/us_dod_1260h.jsonl":               feed,
		"/download/data-x/affiliates.covered.senzing.jsonl": aff,
		"/download/data-x/us_far_889.jsonl":                 feed,
	}
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		b, ok := files[r.URL.Path]
		if !ok {
			http.NotFound(w, r)
			return
		}
		w.Write(b)
	}))
	defer srv.Close()
	// The client sends the release URLs to the test server.
	src := Source{Client: &http.Client{Transport: rewrite{srv.URL}}, Release: "data-x", Verify: true}
	ctx := context.Background()
	recs, err := src.Load(ctx, DoD1260H)
	if err != nil || len(recs) != 2 {
		t.Fatalf("verified load: %d records, %v", len(recs), err)
	}
	affs, err := src.LoadAffiliates(ctx, AffiliatesCovered)
	if err != nil || len(affs) != 2 {
		t.Fatalf("verified affiliates: %d records, %v", len(affs), err)
	}
	if _, err := src.Load(ctx, FAR889); err == nil {
		t.Error("a list with no hash in the manifest passed verification")
	}
	if _, err := src.Load(ctx, All); err == nil {
		t.Error("all.jsonl passed verification without a hash")
	}
	files["/download/data-x/us_dod_1260h.jsonl"] = append([]byte(nil), aff...)
	if _, err := src.Load(ctx, DoD1260H); err == nil || !strings.Contains(err.Error(), "does not match") {
		t.Errorf("changed file accepted: %v", err)
	}
}

type rewrite struct{ base string }

func (rw rewrite) RoundTrip(r *http.Request) (*http.Response, error) {
	u := rw.base + strings.TrimPrefix(r.URL.String(), "https://github.com/Mattbusel/us-restricted-parties/releases")
	req, err := http.NewRequestWithContext(r.Context(), r.Method, u, nil)
	if err != nil {
		return nil, err
	}
	return http.DefaultTransport.RoundTrip(req)
}

// URP_LIVE=1: a pinned data release still serves its files, and they match its manifest.
func TestPinnedReleaseVerifies(t *testing.T) {
	if os.Getenv("URP_LIVE") != "1" {
		t.Skip("set URP_LIVE=1 to read a published release")
	}
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Minute)
	defer cancel()
	src := Source{Release: "data-2026-11-17-2241", Verify: true}
	if recs, err := src.Load(ctx, DoD1260H); err != nil || len(recs) < 100 {
		t.Fatalf("pinned 1260H: %d records, %v", len(recs), err)
	}
	m, err := Source{}.Manifest(ctx)
	if err != nil {
		t.Fatal(err)
	}
	if m.Affiliates == nil {
		t.Skip("latest release has no affiliates build")
	}
	affs, err := Source{Verify: true}.LoadAffiliates(ctx, AffiliatesCovered)
	if err != nil {
		t.Fatal(err)
	}
	if want := m.Affiliates.Files["affiliates.covered.senzing.jsonl"].Records; len(affs) != want {
		t.Errorf("affiliates.covered: %d records, manifest says %d", len(affs), want)
	}
}

package restrictedparties

import (
	"context"
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

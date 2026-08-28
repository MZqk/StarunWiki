package main

import (
	"encoding/json"
	"path/filepath"
	"strings"
	"testing"
)

func TestCommittedLegacyV2ManifestMatchesBFFContract(t *testing.T) {
	manifest, err := loadManifest(filepath.Join("..", "..", "knowledge-packs", "deep-sky", "releases", "public-de219d707e39", "manifest.json"))
	if err != nil {
		t.Fatal(err)
	}
	if manifest.SchemaVersion != 2 || manifest.ReleaseMode != legacyReleaseMode || manifest.Counts.Pages != 51 || manifest.Counts.Unreviewed != 51 || manifest.CorpusVerified {
		t.Fatalf("committed legacy manifest=%#v", manifest)
	}
	if len(manifest.PublicProfile.Suggestions) != 2 || manifest.PublicProfile.Suggestions[0].Question == "" {
		t.Fatalf("committed legacy suggestions=%#v", manifest.PublicProfile.Suggestions)
	}
}

func TestParseManifestV1NormalizesLegacyRelease(t *testing.T) {
	value := map[string]any{
		"schema_version":   1,
		"release_id":       "public-legacy",
		"corpus_path":      "/old/absolute/path/retrieval-corpus.jsonl",
		"corpus_sha256":    strings.Repeat("c", 64),
		"page_count":       1,
		"draft_count":      0,
		"stable_count":     1,
		"unreviewed_count": 1,
		"pages": []any{
			manifestTestPage("concept/legacy", "stable", "needs-human-review", "a"),
		},
	}
	manifest := mustParseManifest(t, value)
	if manifest.SchemaVersion != 1 || manifest.PackID != "deep-sky" || manifest.ReleaseID != "public-legacy" || manifest.ReleaseMode != "legacy-manifest-only" {
		t.Fatalf("legacy identity=%#v", manifest)
	}
	if manifest.Locale != "zh-CN" || manifest.PublicProfile.Brand != "StarunWiki" || manifest.PublicProfile.Title != "深空知识问答" {
		t.Fatalf("legacy profile=%#v locale=%q", manifest.PublicProfile, manifest.Locale)
	}
	if manifest.CorpusVerified || manifest.Corpus.Available || manifest.Corpus.SHA256 != strings.Repeat("c", 64) {
		t.Fatalf("legacy corpus=%#v verified=%v", manifest.Corpus, manifest.CorpusVerified)
	}
	if manifest.Counts != (ManifestCounts{Pages: 1, Draft: 0, Stable: 1, Unreviewed: 1}) {
		t.Fatalf("legacy counts=%#v", manifest.Counts)
	}
	if manifest.Pages["concept/legacy"].ReleaseID != "public-legacy" {
		t.Fatalf("legacy page release=%q", manifest.Pages["concept/legacy"].ReleaseID)
	}
	meta := manifest.Meta()
	if meta.Description == "" || meta.Suggestions == nil || len(meta.Suggestions) != 0 || meta.CorpusVerified {
		t.Fatalf("legacy meta=%#v", meta)
	}
}

func TestParseManifestV2PreservesPublicContract(t *testing.T) {
	value := map[string]any{
		"schema_version": "starunwiki.public-manifest/v2",
		"pack_id":        "deep-sky",
		"release_id":     "public-v2",
		"release_mode":   "full",
		"bundle_sha256":  strings.Repeat("b", 64),
		"locale":         "zh-CN",
		"corpus": map[string]any{
			"logical_uri": "pack://deep-sky/retrieval-corpus.jsonl",
			"sha256":      strings.Repeat("c", 64),
			"available":   true,
		},
		"counts": map[string]any{"pages": 2, "draft": 1, "stable": 1, "unreviewed": 1},
		"public_profile": map[string]any{
			"brand": "StarunWiki", "title": "深空知识问答", "description": "只读问答", "suggestions": []any{
				map[string]any{"category": "A", "title": "A", "question": "Question A"},
				map[string]any{"category": "B", "title": "B", "question": "Question B"},
			},
		},
		"pages": []any{
			manifestTestPage("concept/stable", "stable", "needs-human-review", "a"),
			manifestTestPage("concept/draft", "draft", "reviewed", "d"),
		},
	}
	manifest := mustParseManifest(t, value)
	if manifest.SchemaVersion != 2 || manifest.PackID != "deep-sky" || manifest.ReleaseMode != "full" || !manifest.CorpusVerified {
		t.Fatalf("v2 identity=%#v", manifest)
	}
	meta := manifest.Meta()
	if meta.Brand != "StarunWiki" || meta.Title != "深空知识问答" {
		t.Fatalf("v2 meta profile=%#v", meta)
	}
	if meta.PageCount != 2 || meta.DraftCount != 1 || meta.UnreviewedCount != 1 || len(meta.Suggestions) != 2 {
		t.Fatalf("v2 meta=%#v", meta)
	}
}

func TestParseManifestRejectsUnsupportedAndInconsistentSchemas(t *testing.T) {
	tests := []struct {
		name  string
		value map[string]any
	}{
		{name: "unsupported", value: map[string]any{"schema_version": 3}},
		{name: "v1 count mismatch", value: map[string]any{
			"schema_version": 1, "release_id": "r", "corpus_sha256": strings.Repeat("c", 64), "page_count": 2, "draft_count": 0, "unreviewed_count": 1,
			"pages": []any{manifestTestPage("concept/a", "stable", "needs-human-review", "a")},
		}},
		{name: "v2 invalid bundle digest", value: map[string]any{
			"schema_version": "starunwiki.public-manifest/v2", "pack_id": "p", "release_id": "r", "release_mode": "full", "bundle_sha256": "short", "locale": "zh-CN",
			"corpus":         map[string]any{"logical_uri": "pack://p/corpus", "sha256": strings.Repeat("c", 64), "available": true},
			"counts":         map[string]any{"pages": 1, "draft": 0, "stable": 1, "unreviewed": 1},
			"public_profile": map[string]any{"brand": "B", "title": "T", "description": "D", "suggestions": []any{}},
			"pages":          []any{manifestTestPage("concept/a", "stable", "needs-human-review", "a")},
		}},
		{name: "duplicate slug", value: map[string]any{
			"schema_version": 1, "release_id": "r", "corpus_sha256": strings.Repeat("c", 64), "page_count": 2, "draft_count": 0, "unreviewed_count": 2,
			"pages": []any{manifestTestPage("concept/a", "stable", "needs-human-review", "a"), manifestTestPage("concept/a", "stable", "needs-human-review", "b")},
		}},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			data, err := json.Marshal(test.value)
			if err != nil {
				t.Fatal(err)
			}
			if _, err := parseManifest(data); err == nil {
				t.Fatal("invalid manifest was accepted")
			}
		})
	}
}

func mustParseManifest(t *testing.T, value any) RuntimeManifest {
	t.Helper()
	data, err := json.Marshal(value)
	if err != nil {
		t.Fatal(err)
	}
	manifest, err := parseManifest(data)
	if err != nil {
		t.Fatal(err)
	}
	return manifest
}

func manifestTestPage(slug, status, reviewState, digestCharacter string) map[string]any {
	return map[string]any{
		"entry_name":          slug + ".md",
		"slug":                slug,
		"title":               slug,
		"summary":             "summary",
		"content":             "content",
		"page_type":           "concept",
		"status":              "published",
		"source_status":       status,
		"source_access":       "public_candidate",
		"source_review_state": reviewState,
		"source_verified":     false,
		"tags":                []string{},
		"stale_after":         "2027-01-01",
		"payload_sha256":      strings.Repeat(digestCharacter, 64),
	}
}

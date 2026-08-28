package main

import (
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"strings"
)

const (
	legacyPackID      = "deep-sky"
	legacyReleaseMode = "legacy-manifest-only"
	legacyBrand       = "StarunWiki"
	legacyTitle       = "深空知识问答"
	legacyDescription = "搜索并读取当前批准的深空摄影 Wiki 页面，提供带原页引用的只读回答。"
	legacyLocale      = "zh-CN"
)

type ManifestPage struct {
	Slug           string `json:"slug"`
	Title          string `json:"title"`
	Summary        string `json:"summary"`
	Content        string `json:"content"`
	SourceStatus   string `json:"source_status"`
	SourceAccess   string `json:"source_access"`
	ReviewState    string `json:"source_review_state"`
	SourceVerified bool   `json:"source_verified"`
	PayloadSHA256  string `json:"payload_sha256"`
	ReleaseID      string `json:"-"`
}

type ManifestCorpus struct {
	LogicalURI string `json:"logical_uri"`
	SHA256     string `json:"sha256"`
	Available  bool   `json:"available"`
}

type ManifestCounts struct {
	Pages      int `json:"pages"`
	Draft      int `json:"draft"`
	Stable     int `json:"stable"`
	Unreviewed int `json:"unreviewed"`
}

type MetaSuggestion struct {
	Category string `json:"category"`
	Title    string `json:"title"`
	Question string `json:"question"`
}

type PublicProfile struct {
	Brand       string           `json:"brand"`
	Title       string           `json:"title"`
	Description string           `json:"description"`
	Suggestions []MetaSuggestion `json:"suggestions"`
}

type manifestV1 struct {
	SchemaVersion   int            `json:"schema_version"`
	ReleaseID       string         `json:"release_id"`
	CorpusSHA256    string         `json:"corpus_sha256"`
	PageCount       int            `json:"page_count"`
	DraftCount      int            `json:"draft_count"`
	StableCount     int            `json:"stable_count"`
	UnreviewedCount int            `json:"unreviewed_count"`
	Pages           []ManifestPage `json:"pages"`
}

type manifestV2 struct {
	SchemaVersion json.RawMessage `json:"schema_version"`
	PackID        string          `json:"pack_id"`
	ReleaseID     string          `json:"release_id"`
	ReleaseMode   string          `json:"release_mode"`
	BundleSHA256  string          `json:"bundle_sha256"`
	Locale        string          `json:"locale"`
	Corpus        ManifestCorpus  `json:"corpus"`
	Counts        ManifestCounts  `json:"counts"`
	PublicProfile PublicProfile   `json:"public_profile"`
	Pages         []ManifestPage  `json:"pages"`
}

// RuntimeManifest is the schema-independent manifest consumed by the BFF.
// Legacy v1 manifests remain usable without their original corpus.
type RuntimeManifest struct {
	SchemaVersion  int
	PackID         string
	ReleaseID      string
	ReleaseMode    string
	BundleSHA256   string
	Locale         string
	Corpus         ManifestCorpus
	Counts         ManifestCounts
	PublicProfile  PublicProfile
	CorpusVerified bool
	Pages          map[string]ManifestPage
}

type PublicMeta struct {
	Brand           string           `json:"brand"`
	PackID          string           `json:"pack_id"`
	Title           string           `json:"title"`
	Description     string           `json:"description"`
	Locale          string           `json:"locale"`
	ReleaseID       string           `json:"release_id"`
	ReleaseMode     string           `json:"release_mode"`
	PageCount       int              `json:"page_count"`
	DraftCount      int              `json:"draft_count"`
	UnreviewedCount int              `json:"unreviewed_count"`
	CorpusVerified  bool             `json:"corpus_verified"`
	Suggestions     []MetaSuggestion `json:"suggestions"`
}

func loadManifest(path string) (RuntimeManifest, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return RuntimeManifest{}, err
	}
	return parseManifest(data)
}

func parseManifest(data []byte) (RuntimeManifest, error) {
	var envelope struct {
		SchemaVersion json.RawMessage `json:"schema_version"`
	}
	if err := json.Unmarshal(data, &envelope); err != nil {
		return RuntimeManifest{}, err
	}
	switch string(envelope.SchemaVersion) {
	case "1":
		var manifest manifestV1
		if err := json.Unmarshal(data, &manifest); err != nil {
			return RuntimeManifest{}, err
		}
		return normalizeManifestV1(manifest)
	case "2", `"starunwiki.public-manifest/v2"`:
		var manifest manifestV2
		if err := json.Unmarshal(data, &manifest); err != nil {
			return RuntimeManifest{}, err
		}
		return normalizeManifestV2(manifest)
	default:
		return RuntimeManifest{}, fmt.Errorf("unsupported public manifest schema_version %s", envelope.SchemaVersion)
	}
}

func normalizeManifestV1(manifest manifestV1) (RuntimeManifest, error) {
	if !validSHA256(manifest.CorpusSHA256) {
		return RuntimeManifest{}, errors.New("public manifest v1 corpus_sha256 is invalid")
	}
	stableCount := manifest.StableCount
	if stableCount == 0 {
		stableCount = manifest.PageCount - manifest.DraftCount
	}
	counts := ManifestCounts{Pages: manifest.PageCount, Draft: manifest.DraftCount, Stable: stableCount, Unreviewed: manifest.UnreviewedCount}
	pages, err := normalizePages(manifest.ReleaseID, counts, manifest.Pages)
	if err != nil {
		return RuntimeManifest{}, err
	}
	return RuntimeManifest{
		SchemaVersion: 1,
		PackID:        legacyPackID,
		ReleaseID:     manifest.ReleaseID,
		ReleaseMode:   legacyReleaseMode,
		Locale:        legacyLocale,
		Corpus:        ManifestCorpus{SHA256: manifest.CorpusSHA256, Available: false},
		Counts:        counts,
		PublicProfile: PublicProfile{Brand: legacyBrand, Title: legacyTitle, Description: legacyDescription, Suggestions: []MetaSuggestion{}},
		// A v1 release is intentionally normalized as manifest-only. Merely
		// carrying a historical corpus digest does not prove corpus availability.
		CorpusVerified: false,
		Pages:          pages,
	}, nil
}

func normalizeManifestV2(manifest manifestV2) (RuntimeManifest, error) {
	if strings.TrimSpace(manifest.PackID) == "" || strings.TrimSpace(manifest.ReleaseMode) == "" || strings.TrimSpace(manifest.Locale) == "" {
		return RuntimeManifest{}, errors.New("public manifest v2 pack_id, release_mode, and locale are required")
	}
	if !validSHA256(manifest.BundleSHA256) {
		return RuntimeManifest{}, errors.New("public manifest v2 bundle_sha256 is invalid")
	}
	if !validSHA256(manifest.Corpus.SHA256) {
		return RuntimeManifest{}, errors.New("public manifest v2 corpus is invalid")
	}
	if manifest.ReleaseMode == "full" {
		if !manifest.Corpus.Available || !strings.HasPrefix(manifest.Corpus.LogicalURI, "pack://") {
			return RuntimeManifest{}, errors.New("full public manifest v2 requires an available pack corpus")
		}
	} else if manifest.ReleaseMode == legacyReleaseMode {
		if manifest.Corpus.Available || strings.TrimSpace(manifest.Corpus.LogicalURI) != "" {
			return RuntimeManifest{}, errors.New("legacy manifest-only v2 must not claim an available corpus")
		}
	} else {
		return RuntimeManifest{}, fmt.Errorf("unsupported public manifest release_mode %q", manifest.ReleaseMode)
	}
	if strings.TrimSpace(manifest.PublicProfile.Brand) == "" || strings.TrimSpace(manifest.PublicProfile.Title) == "" || strings.TrimSpace(manifest.PublicProfile.Description) == "" {
		return RuntimeManifest{}, errors.New("public manifest v2 public_profile brand, title, and description are required")
	}
	for _, suggestion := range manifest.PublicProfile.Suggestions {
		if strings.TrimSpace(suggestion.Category) == "" || strings.TrimSpace(suggestion.Title) == "" || strings.TrimSpace(suggestion.Question) == "" {
			return RuntimeManifest{}, errors.New("public manifest v2 suggestions must contain category, title, and question")
		}
	}
	pages, err := normalizePages(manifest.ReleaseID, manifest.Counts, manifest.Pages)
	if err != nil {
		return RuntimeManifest{}, err
	}
	suggestions := append([]MetaSuggestion{}, manifest.PublicProfile.Suggestions...)
	return RuntimeManifest{
		SchemaVersion:  2,
		PackID:         manifest.PackID,
		ReleaseID:      manifest.ReleaseID,
		ReleaseMode:    manifest.ReleaseMode,
		BundleSHA256:   manifest.BundleSHA256,
		Locale:         manifest.Locale,
		Corpus:         manifest.Corpus,
		Counts:         manifest.Counts,
		PublicProfile:  PublicProfile{Brand: manifest.PublicProfile.Brand, Title: manifest.PublicProfile.Title, Description: manifest.PublicProfile.Description, Suggestions: suggestions},
		CorpusVerified: manifest.ReleaseMode == "full" && manifest.Corpus.Available,
		Pages:          pages,
	}, nil
}

func normalizePages(releaseID string, counts ManifestCounts, ordered []ManifestPage) (map[string]ManifestPage, error) {
	if strings.TrimSpace(releaseID) == "" || counts.Pages <= 0 || len(ordered) != counts.Pages {
		return nil, errors.New("public manifest page count is invalid")
	}
	if counts.Draft < 0 || counts.Draft > counts.Pages || counts.Stable < 0 || counts.Stable > counts.Pages || counts.Unreviewed < 0 || counts.Unreviewed > counts.Pages {
		return nil, errors.New("public manifest review counts are invalid")
	}
	pages := make(map[string]ManifestPage, len(ordered))
	draftCount := 0
	stableCount := 0
	unreviewedCount := 0
	for _, page := range ordered {
		if strings.TrimSpace(page.Slug) == "" || !validSHA256(page.PayloadSHA256) {
			return nil, errors.New("manifest page is incomplete")
		}
		if _, exists := pages[page.Slug]; exists {
			return nil, fmt.Errorf("duplicate manifest slug %q", page.Slug)
		}
		if page.SourceStatus == "draft" {
			draftCount++
		}
		if page.SourceStatus == "stable" {
			stableCount++
		}
		if page.ReviewState == "needs-human-review" {
			unreviewedCount++
		}
		page.ReleaseID = releaseID
		pages[page.Slug] = page
	}
	if draftCount != counts.Draft || stableCount != counts.Stable || unreviewedCount != counts.Unreviewed {
		return nil, fmt.Errorf("public manifest counts do not match pages: draft=%d/%d stable=%d/%d unreviewed=%d/%d", draftCount, counts.Draft, stableCount, counts.Stable, unreviewedCount, counts.Unreviewed)
	}
	return pages, nil
}

func validSHA256(value string) bool {
	if len(value) != 64 {
		return false
	}
	_, err := hex.DecodeString(value)
	return err == nil
}

func (manifest RuntimeManifest) Meta() PublicMeta {
	suggestions := append([]MetaSuggestion{}, manifest.PublicProfile.Suggestions...)
	return PublicMeta{
		Brand:           manifest.PublicProfile.Brand,
		PackID:          manifest.PackID,
		Title:           manifest.PublicProfile.Title,
		Description:     manifest.PublicProfile.Description,
		Locale:          manifest.Locale,
		ReleaseID:       manifest.ReleaseID,
		ReleaseMode:     manifest.ReleaseMode,
		PageCount:       manifest.Counts.Pages,
		DraftCount:      manifest.Counts.Draft,
		UnreviewedCount: manifest.Counts.Unreviewed,
		CorpusVerified:  manifest.CorpusVerified,
		Suggestions:     suggestions,
	}
}

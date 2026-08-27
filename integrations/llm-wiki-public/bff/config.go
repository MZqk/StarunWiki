package main

import (
	"crypto/hmac"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"net"
	"os"
	"strconv"
	"strings"
	"time"
)

type Config struct {
	ListenAddr        string
	Origin            string
	CookieSecure      bool
	CookieSecret      string
	ConversationTTL   time.Duration
	StreamTimeout     time.Duration
	GlobalConcurrency int
	DatabasePath      string
	WeKnoraBaseURL    string
	WeKnoraAPIKey     string
	ExternalSecret    string
	TenantID          uint64
	AgentID           string
	KnowledgeBaseID   string
	ManifestPath      string
	TrustedProxies    []*net.IPNet
}

func loadConfig() (Config, error) {
	var c Config
	c.ListenAddr = env("PUBLIC_LISTEN_ADDR", ":8091")
	c.Origin = env("PUBLIC_ORIGIN", "http://127.0.0.1:8090")
	c.CookieSecure = env("PUBLIC_COOKIE_SECURE", "false") == "true"
	c.CookieSecret = os.Getenv("PUBLIC_COOKIE_SECRET")
	c.ConversationTTL = durationEnv("PUBLIC_CONVERSATION_TTL", 7*24*time.Hour)
	c.StreamTimeout = durationEnv("PUBLIC_STREAM_TIMEOUT", 120*time.Second)
	c.GlobalConcurrency = intEnv("PUBLIC_GLOBAL_CONCURRENCY", 20)
	c.DatabasePath = env("PUBLIC_DB_PATH", "/data/public-bff/public-bff.db")
	c.WeKnoraBaseURL = strings.TrimRight(env("WEKNORA_BASE_URL", "http://app:8080"), "/")
	c.WeKnoraAPIKey = os.Getenv("WEKNORA_CHAT_API_KEY")
	c.ExternalSecret = os.Getenv("WEKNORA_EXTERNAL_HMAC_SECRET")
	c.AgentID = os.Getenv("WEKNORA_AGENT_ID")
	c.KnowledgeBaseID = os.Getenv("WEKNORA_KB_ID")
	c.ManifestPath = env("PUBLIC_MANIFEST_PATH", "/app/public-manifest.json")
	for _, value := range strings.Split(os.Getenv("PUBLIC_TRUSTED_PROXY_CIDRS"), ",") {
		value = strings.TrimSpace(value)
		if value == "" {
			continue
		}
		_, network, parseErr := net.ParseCIDR(value)
		if parseErr != nil {
			return c, fmt.Errorf("invalid PUBLIC_TRUSTED_PROXY_CIDRS entry %q", value)
		}
		c.TrustedProxies = append(c.TrustedProxies, network)
	}
	var err error
	c.TenantID, err = strconv.ParseUint(os.Getenv("WEKNORA_TENANT_ID"), 10, 64)
	if err != nil {
		return c, errors.New("WEKNORA_TENANT_ID must be an unsigned integer")
	}
	for name, value := range map[string]string{
		"PUBLIC_COOKIE_SECRET":         c.CookieSecret,
		"WEKNORA_CHAT_API_KEY":         c.WeKnoraAPIKey,
		"WEKNORA_EXTERNAL_HMAC_SECRET": c.ExternalSecret,
		"WEKNORA_AGENT_ID":             c.AgentID,
		"WEKNORA_KB_ID":                c.KnowledgeBaseID,
	} {
		if len(value) < 16 || strings.Contains(value, "CHANGE_ME") {
			return c, fmt.Errorf("%s is missing or too short", name)
		}
	}
	return c, nil
}

func env(name, fallback string) string {
	if value := os.Getenv(name); value != "" {
		return value
	}
	return fallback
}

func intEnv(name string, fallback int) int {
	value, err := strconv.Atoi(os.Getenv(name))
	if err != nil || value <= 0 {
		return fallback
	}
	return value
}

func durationEnv(name string, fallback time.Duration) time.Duration {
	value, err := time.ParseDuration(os.Getenv(name))
	if err != nil || value <= 0 {
		return fallback
	}
	return value
}

type Manifest struct {
	ReleaseID string         `json:"release_id"`
	PageCount int            `json:"page_count"`
	Pages     []ManifestPage `json:"pages"`
}

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

func loadManifest(path string) (map[string]ManifestPage, string, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, "", err
	}
	var manifest Manifest
	if err := json.Unmarshal(data, &manifest); err != nil {
		return nil, "", err
	}
	if manifest.ReleaseID == "" || manifest.PageCount <= 0 || len(manifest.Pages) != manifest.PageCount {
		return nil, "", errors.New("public manifest page count is invalid")
	}
	pages := make(map[string]ManifestPage, len(manifest.Pages))
	for _, page := range manifest.Pages {
		if page.Slug == "" || page.PayloadSHA256 == "" {
			return nil, "", errors.New("manifest page is incomplete")
		}
		if _, exists := pages[page.Slug]; exists {
			return nil, "", fmt.Errorf("duplicate manifest slug %q", page.Slug)
		}
		page.ReleaseID = manifest.ReleaseID
		pages[page.Slug] = page
	}
	return pages, manifest.ReleaseID, nil
}

func visitorID(secret, cookie string) string {
	mac := hmac.New(sha256.New, []byte(secret))
	mac.Write([]byte(cookie))
	return hex.EncodeToString(mac.Sum(nil))
}

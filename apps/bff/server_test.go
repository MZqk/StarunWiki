package main

import (
	"bytes"
	"encoding/json"
	"errors"
	"net"
	"net/http"
	"net/http/httptest"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/golang-jwt/jwt/v5"
)

func TestReadQueryIsStrict(t *testing.T) {
	recorder := httptest.NewRecorder()
	request := httptest.NewRequest("POST", "/", strings.NewReader(`{"query":"星系拍摄","agent_id":"forged"}`))
	if _, err := readQuery(recorder, request); err == nil {
		t.Fatal("readQuery accepted a browser-supplied agent_id")
	}
	request = httptest.NewRequest("POST", "/", strings.NewReader(`{"query":"  星系拍摄  "}`))
	query, err := readQuery(recorder, request)
	if err != nil || query != "星系拍摄" {
		t.Fatalf("query=%q err=%v", query, err)
	}
}

func TestExtractCitationsUsesManifestOnlyAndMarksDraft(t *testing.T) {
	pages := map[string]ManifestPage{
		"concept/a": {Slug: "concept/a", Title: "A", SourceStatus: "draft", ReleaseID: "release-1"},
	}
	citations, draft, unreviewed := extractCitations("[[concept/a|A]] [[concept/private|Private]] [[concept/a|Again]]", pages)
	if len(citations) != 1 || !draft || !unreviewed || !citations[0].IsDraft || !citations[0].NeedsReview {
		t.Fatalf("citations=%#v draft=%v unreviewed=%v", citations, draft, unreviewed)
	}
}

func TestExtractCitationsMarksStableUnreviewedPage(t *testing.T) {
	pages := map[string]ManifestPage{
		"concept/astro": {Slug: "concept/astro", Title: "Astro", SourceStatus: "stable", ReviewState: "needs-human-review"},
	}
	citations, draft, unreviewed := extractCitations("[[concept/astro|Astro]]", pages)
	if len(citations) != 1 || draft || !unreviewed || citations[0].IsDraft || !citations[0].NeedsReview {
		t.Fatalf("citations=%#v draft=%v unreviewed=%v", citations, draft, unreviewed)
	}
}

func TestMetaReturnsOnlyNormalizedPublicFields(t *testing.T) {
	store, _ := testStore(t, time.Hour)
	manifest := RuntimeManifest{
		SchemaVersion:  2,
		PackID:         "deep-sky",
		ReleaseID:      "public-release",
		ReleaseMode:    "full",
		Locale:         "zh-CN",
		BundleSHA256:   strings.Repeat("b", 64),
		Counts:         ManifestCounts{Pages: 2, Draft: 1, Stable: 1, Unreviewed: 2},
		PublicProfile:  PublicProfile{Brand: "StarunWiki", Title: "深空知识问答", Description: "只读问答", Suggestions: []MetaSuggestion{{Category: "A", Title: "A", Question: "question one"}, {Category: "B", Title: "B", Question: "question two"}}},
		CorpusVerified: true,
		Pages: map[string]ManifestPage{
			"concept/a": {Slug: "concept/a", Content: "must not leak"},
			"concept/b": {Slug: "concept/b", Content: "must not leak"},
		},
	}
	server := NewServer(testConfig(filepath.Join(t.TempDir(), "unused.db"), "http://127.0.0.1:1"), store, nil, manifest)
	result := httptest.NewRecorder()
	server.ServeHTTP(result, httptest.NewRequest(http.MethodGet, "/qa/v1/meta", nil))
	if result.Code != http.StatusOK {
		t.Fatalf("meta code=%d body=%s", result.Code, result.Body.String())
	}
	if result.Header().Get("Set-Cookie") != "" {
		t.Fatalf("meta unexpectedly set a visitor cookie: %q", result.Header().Get("Set-Cookie"))
	}
	var meta PublicMeta
	if err := json.Unmarshal(result.Body.Bytes(), &meta); err != nil {
		t.Fatal(err)
	}
	if meta.Brand != "StarunWiki" || meta.PackID != "deep-sky" || meta.Title != "深空知识问答" || meta.Description != "只读问答" || meta.Locale != "zh-CN" {
		t.Fatalf("meta profile=%#v", meta)
	}
	if meta.ReleaseID != "public-release" || meta.ReleaseMode != "full" || meta.PageCount != 2 || meta.DraftCount != 1 || meta.UnreviewedCount != 2 || !meta.CorpusVerified {
		t.Fatalf("meta release=%#v", meta)
	}
	if len(meta.Suggestions) != 2 || meta.Suggestions[0].Question != "question one" || meta.Suggestions[1].Question != "question two" {
		t.Fatalf("meta suggestions=%#v", meta.Suggestions)
	}
	for _, forbidden := range []string{"must not leak", "bundle_sha256", "corpus_sha256", "logical_uri", "agent_id", "knowledge_base_id"} {
		if strings.Contains(result.Body.String(), forbidden) {
			t.Fatalf("meta leaked %q: %s", forbidden, result.Body.String())
		}
	}
}

func TestMetaUsesExistingOriginPolicy(t *testing.T) {
	store, _ := testStore(t, time.Hour)
	config := testConfig(filepath.Join(t.TempDir(), "unused.db"), "http://127.0.0.1:1")
	server := NewServer(config, store, nil, testManifest("release", nil))
	request := httptest.NewRequest(http.MethodGet, "/qa/v1/meta", nil)
	request.Header.Set("Origin", "https://not-allowed.example")
	result := httptest.NewRecorder()
	server.ServeHTTP(result, request)
	if result.Code != http.StatusForbidden {
		t.Fatalf("meta origin code=%d body=%s", result.Code, result.Body.String())
	}
}

func TestHealthIncludesPackAndReleaseMode(t *testing.T) {
	store, _ := testStore(t, time.Hour)
	server := NewServer(testConfig(filepath.Join(t.TempDir(), "unused.db"), "http://127.0.0.1:1"), store, nil, testManifest("release-1", nil))
	result := httptest.NewRecorder()
	server.ServeHTTP(result, httptest.NewRequest(http.MethodGet, "/healthz", nil))
	if result.Code != http.StatusOK {
		t.Fatalf("health code=%d body=%s", result.Code, result.Body.String())
	}
	var health map[string]any
	if err := json.Unmarshal(result.Body.Bytes(), &health); err != nil {
		t.Fatal(err)
	}
	if health["pack_id"] != "test-pack" || health["release_mode"] != "test" || health["release_id"] != "release-1" {
		t.Fatalf("health=%#v", health)
	}
}

func TestParseSSEDropsNonDataAndReadsFrames(t *testing.T) {
	input := "event: message\ndata: {\"response_type\":\"thinking\",\"content\":\"secret\"}\n\n" +
		"data: {\"response_type\":\"answer\",\"content\":\"答\"}\n\n"
	var received []streamMessage
	if err := parseSSE(bytes.NewBufferString(input), func(message streamMessage) error { received = append(received, message); return nil }); err != nil {
		t.Fatal(err)
	}
	if len(received) != 2 || received[1].Content != "答" {
		t.Fatalf("received=%#v", received)
	}
}

func TestPublicStreamDoesNotEmitDoneBeforeLaterError(t *testing.T) {
	upstreamServer := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch {
		case r.Method == http.MethodPost && r.URL.Path == "/api/v1/sessions":
			w.WriteHeader(http.StatusCreated)
			_, _ = w.Write([]byte(`{"success":true,"data":{"id":"upstream-session"}}`))
		case r.Method == http.MethodPost && r.URL.Path == "/api/v1/agent-chat/upstream-session":
			w.Header().Set("Content-Type", "text/event-stream")
			_, _ = w.Write([]byte("data: {\"response_type\":\"complete\"}\n\n" +
				"data: {\"response_type\":\"error\",\"content\":\"private upstream detail\"}\n\n" +
				"data: {\"response_type\":\"error\"}\n\n"))
		default:
			t.Fatalf("unexpected upstream request %s %s", r.Method, r.URL.Path)
		}
	}))
	defer upstreamServer.Close()
	config := testConfig(filepath.Join(t.TempDir(), "state.db"), upstreamServer.URL)
	store, err := NewStore(config)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = store.Close() })
	server := NewServer(config, store, NewUpstream(config), testManifest("release", nil))

	created := httptest.NewRecorder()
	server.ServeHTTP(created, httptest.NewRequest(http.MethodPost, "/qa/v1/conversations", nil))
	var conversation map[string]any
	if err := json.Unmarshal(created.Body.Bytes(), &conversation); err != nil {
		t.Fatal(err)
	}
	request := httptest.NewRequest(http.MethodPost, "/qa/v1/conversations/"+conversation["id"].(string)+"/messages:stream", strings.NewReader(`{"query":"test"}`))
	request.Header.Set("Content-Type", "application/json")
	request.Header.Set("Idempotency-Key", "error-after-complete")
	request.AddCookie(created.Result().Cookies()[0])
	result := httptest.NewRecorder()
	server.ServeHTTP(result, request)
	body := result.Body.String()
	if strings.Contains(body, "event: done") || strings.Contains(body, "event: citations") {
		t.Fatalf("success terminal event leaked before error: %s", body)
	}
	if strings.Count(body, "event: error") != 1 || strings.Contains(body, "private upstream detail") {
		t.Fatalf("error framing or redaction failed: %s", body)
	}
}

func TestExternalTokenHasBoundedPrincipalClaims(t *testing.T) {
	upstream := NewUpstream(Config{TenantID: 7, ExternalSecret: strings.Repeat("s", 32)})
	tokenString, err := upstream.externalToken("visitor-1")
	if err != nil {
		t.Fatal(err)
	}
	parsed, err := jwt.Parse(tokenString, func(token *jwt.Token) (any, error) { return []byte(strings.Repeat("s", 32)), nil }, jwt.WithAudience("weknora"))
	if err != nil || !parsed.Valid {
		t.Fatalf("token invalid: %v", err)
	}
	claims := parsed.Claims.(jwt.MapClaims)
	if claims["sub"] != "visitor-1" || claims["tenant_id"] != float64(7) {
		t.Fatalf("claims=%#v", claims)
	}
	exp, _ := claims.GetExpirationTime()
	if time.Until(exp.Time) > 16*time.Minute {
		t.Fatalf("token expiry too long: %v", exp.Time)
	}
}

func TestAnonymousPrincipalIsReleaseScoped(t *testing.T) {
	config := testConfig(filepath.Join(t.TempDir(), "state.db"), "http://127.0.0.1:1")
	store, err := NewStore(config)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = store.Close() })
	cookie := &http.Cookie{Name: "llmwiki-anon", Value: strings.Repeat("C", 44)}
	requestA := httptest.NewRequest(http.MethodGet, "/healthz", nil)
	requestA.AddCookie(cookie)
	requestB := httptest.NewRequest(http.MethodGet, "/healthz", nil)
	requestB.AddCookie(cookie)
	serverA := NewServer(config, store, NewUpstream(config), testManifest("release-a", nil))
	serverB := NewServer(config, store, NewUpstream(config), testManifest("release-b", nil))
	visitorA, _ := serverA.ensureVisitor(httptest.NewRecorder(), requestA)
	visitorB, _ := serverB.ensureVisitor(httptest.NewRecorder(), requestB)
	if visitorA == visitorB {
		t.Fatal("anonymous principal was reused across corpus releases")
	}
}

func TestSanitizeHistoryDropsInternalFields(t *testing.T) {
	raw, _ := json.Marshal(map[string]any{"success": true, "data": []map[string]any{{"role": "assistant", "content": "[[concept/a|A]]", "agent_steps": []any{"secret"}, "agent_id": "internal"}, {"role": "system", "content": "hidden"}}})
	result := sanitizeHistory(raw, map[string]ManifestPage{"concept/a": {Slug: "concept/a", Title: "A"}})
	messages := result["messages"].([]map[string]any)
	if len(messages) != 1 {
		t.Fatalf("messages=%#v", messages)
	}
	encoded, _ := json.Marshal(messages)
	if bytes.Contains(encoded, []byte("agent_steps")) || bytes.Contains(encoded, []byte("internal")) {
		t.Fatalf("internal fields leaked: %s", encoded)
	}
}

func TestRateLimitRejectsFourthImmediateQuestion(t *testing.T) {
	store, _ := testStore(t, time.Hour)
	for index := 0; index < 3; index++ {
		retry, err := store.RateLimit(t.Context(), "visitor", "192.0.2.0/24")
		if err != nil || retry != 0 {
			t.Fatalf("request %d retry=%v err=%v", index+1, retry, err)
		}
	}
	retry, err := store.RateLimit(t.Context(), "visitor", "192.0.2.0/24")
	if err != nil || retry <= 0 {
		t.Fatalf("fourth request retry=%v err=%v", retry, err)
	}
}

func TestRateLimitFixedWindows(t *testing.T) {
	store, _ := testStore(t, time.Hour)
	now := time.Now().UTC()
	cases := []struct {
		name       string
		visitor    string
		ip         string
		counterKey string
		limit      int
		expires    time.Time
	}{
		{name: "visitor hour", visitor: "hour-visitor", ip: "192.0.2.0/24", counterKey: "visitor-hour:hour-visitor:" + now.Format("2006010215"), limit: 30, expires: now.Truncate(time.Hour).Add(time.Hour)},
		{name: "visitor day", visitor: "day-visitor", ip: "198.51.100.0/24", counterKey: "visitor-day:day-visitor:" + now.Format("20060102"), limit: 100, expires: time.Date(now.Year(), now.Month(), now.Day()+1, 0, 0, 0, 0, time.UTC)},
		{name: "ip minute", visitor: "ip-visitor", ip: "203.0.113.0/24", counterKey: "ip-minute:203.0.113.0/24:" + now.Format("200601021504"), limit: 10, expires: now.Truncate(time.Minute).Add(time.Minute)},
	}
	for _, test := range cases {
		t.Run(test.name, func(t *testing.T) {
			if _, err := store.db.Exec(`INSERT INTO rate_counters(counter_key, count, expires_at) VALUES(?, ?, ?)`, test.counterKey, test.limit, test.expires.Unix()); err != nil {
				t.Fatal(err)
			}
			retry, err := store.RateLimit(t.Context(), test.visitor, test.ip)
			if err != nil || retry <= 0 {
				t.Fatalf("retry=%v err=%v", retry, err)
			}
		})
	}
}

func TestDifferentVisitorCannotReadConversation(t *testing.T) {
	upstreamServer := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/api/v1/sessions" {
			w.WriteHeader(http.StatusCreated)
			_, _ = w.Write([]byte(`{"success":true,"data":{"id":"upstream-session"}}`))
			return
		}
		t.Fatalf("unexpected upstream request %s", r.URL.Path)
	}))
	defer upstreamServer.Close()
	config := testConfig(filepath.Join(t.TempDir(), "state.db"), upstreamServer.URL)
	store, err := NewStore(config)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = store.Close() })
	server := NewServer(config, store, NewUpstream(config), testManifest("release", nil))

	created := httptest.NewRecorder()
	server.ServeHTTP(created, httptest.NewRequest(http.MethodPost, "/qa/v1/conversations", nil))
	if created.Code != http.StatusCreated {
		t.Fatalf("create code=%d body=%s", created.Code, created.Body.String())
	}
	var body map[string]string
	_ = json.Unmarshal(created.Body.Bytes(), &body)
	request := httptest.NewRequest(http.MethodGet, "/qa/v1/conversations/"+body["id"]+"/messages", nil)
	request.AddCookie(&http.Cookie{Name: "llmwiki-anon", Value: strings.Repeat("B", 44)})
	result := httptest.NewRecorder()
	server.ServeHTTP(result, request)
	if result.Code != http.StatusNotFound {
		t.Fatalf("cross-visitor code=%d body=%s", result.Code, result.Body.String())
	}
}

func TestTrustedProxyUsesOnlyXRealIP(t *testing.T) {
	_, network, _ := net.ParseCIDR("172.16.0.0/12")
	request := httptest.NewRequest(http.MethodGet, "/", nil)
	request.RemoteAddr = "172.20.0.3:1234"
	request.Header.Set("X-Real-IP", "203.0.113.44")
	request.Header.Set("X-Forwarded-For", "198.51.100.7")
	if got := clientIPPrefix(request, []*net.IPNet{network}); got != "203.0.113.0/24" {
		t.Fatalf("prefix=%q", got)
	}
	request.RemoteAddr = "198.51.100.9:4321"
	if got := clientIPPrefix(request, []*net.IPNet{network}); got != "198.51.100.0/24" {
		t.Fatalf("untrusted proxy prefix=%q", got)
	}
}

func TestSQLiteStoreSurvivesRestart(t *testing.T) {
	databasePath := filepath.Join(t.TempDir(), "state.db")
	config := Config{DatabasePath: databasePath, ConversationTTL: time.Hour}
	store, err := NewStore(config)
	if err != nil {
		t.Fatal(err)
	}
	conversation := Conversation{UpstreamSessionID: "upstream", VisitorID: "visitor", CreatedAt: time.Now().Unix()}
	if err := store.PutConversation(t.Context(), "opaque", conversation); err != nil {
		t.Fatal(err)
	}
	if _, started, err := store.IdempotencyStart(t.Context(), "visitor", "opaque", "idem-key", "hash"); err != nil || !started {
		t.Fatalf("start idempotency started=%v err=%v", started, err)
	}
	if err := store.IdempotencyDone(t.Context(), "visitor", "opaque", "idem-key", "hash", "events"); err != nil {
		t.Fatal(err)
	}
	if err := store.Close(); err != nil {
		t.Fatal(err)
	}
	reopened, err := NewStore(config)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = reopened.Close() })
	got, err := reopened.Conversation(t.Context(), "opaque", "visitor")
	if err != nil || got.UpstreamSessionID != "upstream" {
		t.Fatalf("conversation=%#v err=%v", got, err)
	}
	previous, started, err := reopened.IdempotencyStart(t.Context(), "visitor", "opaque", "idem-key", "hash")
	if err != nil || started || previous != "done:hash:events" {
		t.Fatalf("previous=%q started=%v err=%v", previous, started, err)
	}
}

func TestVisitorConcurrencyIsAtomic(t *testing.T) {
	store, _ := testStore(t, time.Hour)
	const attempts = 8
	type result struct {
		release func()
		err     error
	}
	results := make(chan result, attempts)
	start := make(chan struct{})
	var group sync.WaitGroup
	for index := 0; index < attempts; index++ {
		group.Add(1)
		go func() {
			defer group.Done()
			<-start
			release, err := store.AcquireVisitor(t.Context(), "visitor", time.Minute)
			results <- result{release: release, err: err}
		}()
	}
	close(start)
	group.Wait()
	close(results)
	successes := 0
	for item := range results {
		if item.err == nil {
			successes++
			defer item.release()
			continue
		}
		if !errors.Is(item.err, ErrLimitExceeded) {
			t.Fatalf("unexpected acquire error: %v", item.err)
		}
	}
	if successes != 2 {
		t.Fatalf("successful leases=%d want=2", successes)
	}
}

func TestExpiredLeaseCanBeReacquired(t *testing.T) {
	store, _ := testStore(t, time.Hour)
	_, err := store.db.Exec(`INSERT INTO leases(resource_kind, resource_key, lease_id, expires_at) VALUES('conversation', 'c1', 'stale', ?)`, time.Now().Add(-time.Minute).UnixMilli())
	if err != nil {
		t.Fatal(err)
	}
	release, err := store.LockConversation(t.Context(), "c1", time.Minute)
	if err != nil {
		t.Fatal(err)
	}
	release()
}

func TestDueCleanupPersistsUntilDeleted(t *testing.T) {
	store, _ := testStore(t, time.Hour)
	if err := store.PutConversation(t.Context(), "expired", Conversation{UpstreamSessionID: "up", VisitorID: "visitor", CreatedAt: time.Now().Unix()}); err != nil {
		t.Fatal(err)
	}
	if _, err := store.db.Exec(`UPDATE conversations SET expires_at = ? WHERE id = 'expired'`, time.Now().Add(-time.Minute).Unix()); err != nil {
		t.Fatal(err)
	}
	for attempt := 0; attempt < 2; attempt++ {
		ids, err := store.DueCleanup(t.Context(), 100)
		if err != nil || len(ids) != 1 || ids[0] != "expired" {
			t.Fatalf("attempt=%d ids=%v err=%v", attempt, ids, err)
		}
	}
	if err := store.DeleteConversation(t.Context(), "expired"); err != nil {
		t.Fatal(err)
	}
	ids, err := store.DueCleanup(t.Context(), 100)
	if err != nil || len(ids) != 0 {
		t.Fatalf("after delete ids=%v err=%v", ids, err)
	}
}

func TestPruneExpiredRemovesEphemeralRowsOnly(t *testing.T) {
	store, _ := testStore(t, time.Hour)
	if err := store.PutConversation(t.Context(), "conversation", Conversation{UpstreamSessionID: "up", VisitorID: "visitor", CreatedAt: time.Now().Unix()}); err != nil {
		t.Fatal(err)
	}
	past := time.Now().Add(-time.Minute)
	statements := []struct {
		query string
		args  []any
	}{
		{`INSERT INTO turns(conversation_id, turn_id, assistant_message_id, expires_at) VALUES('conversation', 'turn', 'message', ?)`, []any{past.Unix()}},
		{`INSERT INTO rate_buckets(bucket_key, tokens, updated_at, expires_at) VALUES('bucket', 0, 0, ?)`, []any{past.Unix()}},
		{`INSERT INTO rate_counters(counter_key, count, expires_at) VALUES('counter', 1, ?)`, []any{past.Unix()}},
		{`INSERT INTO leases(resource_kind, resource_key, lease_id, expires_at) VALUES('visitor', 'visitor', 'lease', ?)`, []any{past.UnixMilli()}},
	}
	for _, statement := range statements {
		if _, err := store.db.Exec(statement.query, statement.args...); err != nil {
			t.Fatal(err)
		}
	}
	if err := store.PruneExpired(t.Context()); err != nil {
		t.Fatal(err)
	}
	for _, table := range []string{"turns", "rate_buckets", "rate_counters", "leases"} {
		var count int
		if err := store.db.QueryRow("SELECT COUNT(*) FROM " + table).Scan(&count); err != nil || count != 0 {
			t.Fatalf("table=%s count=%d err=%v", table, count, err)
		}
	}
	if _, err := store.Conversation(t.Context(), "conversation", "visitor"); err != nil {
		t.Fatalf("active conversation was pruned: %v", err)
	}
}

func testStore(t *testing.T, ttl time.Duration) (*Store, string) {
	t.Helper()
	path := filepath.Join(t.TempDir(), "state.db")
	store, err := NewStore(Config{DatabasePath: path, ConversationTTL: ttl})
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = store.Close() })
	return store, path
}

func testConfig(databasePath, upstreamURL string) Config {
	return Config{
		Origin: "http://127.0.0.1:8090", CookieSecret: strings.Repeat("c", 32), ConversationTTL: 7 * 24 * time.Hour,
		StreamTimeout: 5 * time.Second, GlobalConcurrency: 20, DatabasePath: databasePath,
		WeKnoraBaseURL: upstreamURL, WeKnoraAPIKey: strings.Repeat("k", 32), ExternalSecret: strings.Repeat("h", 32),
		TenantID: 7, AgentID: strings.Repeat("a", 32), KnowledgeBaseID: strings.Repeat("b", 32),
	}
}

func testManifest(releaseID string, pages map[string]ManifestPage) RuntimeManifest {
	if pages == nil {
		pages = map[string]ManifestPage{}
	}
	return RuntimeManifest{
		SchemaVersion: 2,
		PackID:        "test-pack",
		ReleaseID:     releaseID,
		ReleaseMode:   "test",
		Locale:        "zh-CN",
		Counts:        ManifestCounts{Pages: len(pages)},
		PublicProfile: PublicProfile{Brand: "Test", Title: "Test Q&A", Description: "Test description", Suggestions: []MetaSuggestion{}},
		Pages:         pages,
	}
}

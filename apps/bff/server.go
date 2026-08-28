package main

import (
	"context"
	"crypto/rand"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log/slog"
	"net"
	"net/http"
	"regexp"
	"strconv"
	"strings"
	"time"
)

const maxBodyBytes = 8 * 1024

var citationPattern = regexp.MustCompile(`\[\[([^\]|]+)(?:\|([^\]]+))?\]\]`)

type PublicCitation struct {
	Slug         string `json:"slug"`
	Title        string `json:"title"`
	Summary      string `json:"summary"`
	SourceStatus string `json:"source_status"`
	ReviewState  string `json:"review_state"`
	ReleaseID    string `json:"release_id"`
	IsDraft      bool   `json:"is_draft"`
	NeedsReview  bool   `json:"needs_review"`
}

type publicEvent struct {
	Event string `json:"event"`
	Data  any    `json:"data"`
}

type Server struct {
	config    Config
	store     *Store
	upstream  *Upstream
	manifest  RuntimeManifest
	pages     map[string]ManifestPage
	releaseID string
	global    chan struct{}
	mux       *http.ServeMux
}

func NewServer(config Config, store *Store, upstream *Upstream, manifest RuntimeManifest) *Server {
	s := &Server{config: config, store: store, upstream: upstream, manifest: manifest, pages: manifest.Pages, releaseID: manifest.ReleaseID, global: make(chan struct{}, config.GlobalConcurrency), mux: http.NewServeMux()}
	s.routes()
	return s
}

func (s *Server) routes() {
	s.mux.HandleFunc("GET /healthz", s.health)
	s.mux.HandleFunc("GET /qa/v1/meta", s.meta)
	s.mux.HandleFunc("POST /qa/v1/conversations", s.createConversation)
	s.mux.HandleFunc("POST /qa/v1/conversations/{id}/messages:stream", s.streamMessage)
	s.mux.HandleFunc("GET /qa/v1/conversations/{id}/messages", s.history)
	s.mux.HandleFunc("POST /qa/v1/conversations/{id}/turns/{turn}/stop", s.stop)
	s.mux.HandleFunc("DELETE /qa/v1/conversations/{id}", s.deleteConversation)
	s.mux.HandleFunc("GET /qa/v1/citations/{slug...}", s.citation)
}

func (s *Server) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("X-Content-Type-Options", "nosniff")
	w.Header().Set("Referrer-Policy", "no-referrer")
	w.Header().Set("Cache-Control", "no-store")
	w.Header().Set("Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'")
	if origin := r.Header.Get("Origin"); origin != "" && origin != s.config.Origin {
		writeError(w, http.StatusForbidden, "origin_not_allowed", "请求来源不允许", 0)
		return
	}
	s.mux.ServeHTTP(w, r)
}

func (s *Server) health(w http.ResponseWriter, r *http.Request) {
	ctx, cancel := context.WithTimeout(r.Context(), time.Second)
	defer cancel()
	if err := s.store.Ping(ctx); err != nil {
		writeError(w, http.StatusServiceUnavailable, "dependency_unavailable", "服务暂不可用", 0)
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{
		"ok":           true,
		"pack_id":      s.manifest.PackID,
		"release_id":   s.releaseID,
		"release_mode": s.manifest.ReleaseMode,
		"page_count":   len(s.pages),
	})
}

func (s *Server) meta(w http.ResponseWriter, r *http.Request) {
	writeJSON(w, http.StatusOK, s.manifest.Meta())
}

func (s *Server) createConversation(w http.ResponseWriter, r *http.Request) {
	visitor, _ := s.ensureVisitor(w, r)
	sessionID, err := s.upstream.CreateSession(r.Context(), visitor)
	if err != nil {
		slog.Warn("create upstream session failed", "error", err)
		writeError(w, http.StatusBadGateway, "upstream_unavailable", "知识问答服务暂时不可用", 0)
		return
	}
	opaque := randomToken(24)
	if err := s.store.PutConversation(r.Context(), opaque, Conversation{UpstreamSessionID: sessionID, VisitorID: visitor, CreatedAt: time.Now().Unix()}); err != nil {
		_ = s.upstream.DeleteSession(context.Background(), visitor, sessionID)
		writeError(w, http.StatusServiceUnavailable, "state_unavailable", "会话状态暂时不可用", 0)
		return
	}
	writeJSON(w, http.StatusCreated, map[string]any{"id": opaque, "expires_in": int64(s.config.ConversationTTL.Seconds())})
}

func (s *Server) streamMessage(w http.ResponseWriter, r *http.Request) {
	visitor, _ := s.ensureVisitor(w, r)
	conversationID := r.PathValue("id")
	conversation, err := s.store.Conversation(r.Context(), conversationID, visitor)
	if err != nil {
		writeError(w, http.StatusNotFound, "conversation_not_found", "会话不存在", 0)
		return
	}

	idempotencyKey := r.Header.Get("Idempotency-Key")
	if len(idempotencyKey) < 8 || len(idempotencyKey) > 128 {
		writeError(w, http.StatusBadRequest, "invalid_idempotency_key", "Idempotency-Key 必填且长度须在 8–128 字符", 0)
		return
	}
	query, err := readQuery(w, r)
	if err != nil {
		writeError(w, http.StatusBadRequest, "invalid_query", err.Error(), 0)
		return
	}
	queryDigest := sha256.Sum256([]byte(query))
	queryHash := hex.EncodeToString(queryDigest[:])
	previous, started, err := s.store.IdempotencyStart(r.Context(), visitor, conversationID, idempotencyKey, queryHash)
	if err != nil {
		writeError(w, http.StatusServiceUnavailable, "state_unavailable", "会话状态暂时不可用", 0)
		return
	}
	if !started {
		if strings.HasPrefix(previous, "done:"+queryHash+":") {
			replayEvents(w, strings.TrimPrefix(previous, "done:"+queryHash+":"))
			return
		}
		writeError(w, http.StatusConflict, "idempotency_conflict", "该请求正在处理或键已用于其他问题", 0)
		return
	}
	idempotencyCompleted := false
	defer func() {
		if !idempotencyCompleted {
			s.store.IdempotencyAbort(context.Background(), visitor, conversationID, idempotencyKey)
		}
	}()

	ip := clientIPPrefix(r, s.config.TrustedProxies)
	if retry, err := s.store.RateLimit(r.Context(), visitor, ip); err != nil {
		writeError(w, 503, "rate_limit_unavailable", "限流服务暂时不可用", 0)
		return
	} else if retry > 0 {
		writeError(w, 429, "rate_limited", "提问过于频繁，请稍后再试", retry)
		return
	}
	unlock, err := s.store.LockConversation(r.Context(), conversationID, s.config.StreamTimeout)
	if err != nil {
		if !errors.Is(err, ErrBusy) {
			writeError(w, 503, "state_unavailable", "会话状态暂时不可用", 0)
			return
		}
		writeError(w, 409, "conversation_busy", "当前会话已有问题在处理", 0)
		return
	}
	defer unlock()
	releaseVisitor, err := s.store.AcquireVisitor(r.Context(), visitor, s.config.StreamTimeout)
	if err != nil {
		if !errors.Is(err, ErrLimitExceeded) {
			writeError(w, 503, "state_unavailable", "会话状态暂时不可用", 0)
			return
		}
		writeError(w, 429, "visitor_concurrency", "同一访客同时问答过多", time.Minute)
		return
	}
	defer releaseVisitor()
	releaseIP, err := s.store.AcquireIP(r.Context(), ip, s.config.StreamTimeout)
	if err != nil {
		if !errors.Is(err, ErrLimitExceeded) {
			writeError(w, 503, "state_unavailable", "会话状态暂时不可用", 0)
			return
		}
		writeError(w, 429, "ip_concurrency", "当前网络同时问答过多", time.Minute)
		return
	}
	defer releaseIP()
	select {
	case s.global <- struct{}{}:
		defer func() { <-s.global }()
	default:
		writeError(w, 429, "site_busy", "站点当前正忙，请稍后重试", time.Minute)
		return
	}

	ctx, cancel := context.WithTimeout(r.Context(), s.config.StreamTimeout)
	defer cancel()
	response, err := s.upstream.Stream(ctx, visitor, conversation.UpstreamSessionID, query)
	if err != nil {
		writeError(w, 502, "upstream_unavailable", "知识问答服务暂时不可用", 0)
		return
	}
	defer response.Body.Close()
	if response.StatusCode != http.StatusOK {
		writeError(w, 502, "upstream_rejected", "知识问答请求未能启动", 0)
		return
	}

	flusher, ok := w.(http.Flusher)
	if !ok {
		writeError(w, 500, "stream_unsupported", "流式传输不可用", 0)
		return
	}
	w.Header().Set("Content-Type", "text/event-stream; charset=utf-8")
	w.Header().Set("X-Accel-Buffering", "no")
	w.WriteHeader(http.StatusOK)
	turnID := randomToken(18)
	events := []publicEvent{{Event: "turn", Data: map[string]string{"turn_id": turnID}}}
	writeSSE(w, events[0])
	flusher.Flush()
	var answer, assistantID string
	terminal := false
	sawComplete := false
	err = parseSSE(response.Body, func(message streamMessage) error {
		if message.AssistantMessageID != "" && assistantID == "" {
			assistantID = message.AssistantMessageID
			_ = s.store.PutTurn(context.Background(), conversationID, turnID, assistantID)
		}
		switch message.ResponseType {
		case "answer":
			if message.Content != "" {
				answer += message.Content
				event := publicEvent{Event: "delta", Data: map[string]string{"text": message.Content}}
				events = append(events, event)
				writeSSE(w, event)
				flusher.Flush()
			}
		case "complete":
			// WeKnora can emit a completion marker before a later terminal error.
			// Delay the public done event until the upstream stream has closed so
			// browsers never observe done followed by error.
			sawComplete = true
		case "stop":
			doneEvent := publicEvent{Event: "done", Data: map[string]any{"stopped": true}}
			events = append(events, doneEvent)
			writeSSE(w, doneEvent)
			flusher.Flush()
			terminal = true
		case "error":
			if terminal {
				return nil
			}
			errorEvent := publicEvent{Event: "error", Data: map[string]string{"code": "generation_failed", "message": "回答生成失败，请重试"}}
			events = append(events, errorEvent)
			writeSSE(w, errorEvent)
			flusher.Flush()
			terminal = true
		}
		return nil
	})
	if err != nil && ctx.Err() == nil {
		slog.Warn("upstream stream ended unexpectedly", "error", err)
	}
	if !terminal && sawComplete && err == nil {
		citations, draft, unreviewed := extractCitations(answer, s.pages)
		citationEvent := publicEvent{Event: "citations", Data: map[string]any{"items": citations, "contains_draft": draft, "contains_unreviewed": unreviewed}}
		doneEvent := publicEvent{Event: "done", Data: map[string]any{"stopped": false, "contains_draft": draft, "contains_unreviewed": unreviewed}}
		events = append(events, citationEvent, doneEvent)
		writeSSE(w, citationEvent)
		writeSSE(w, doneEvent)
		flusher.Flush()
		terminal = true
	}
	if !terminal && r.Context().Err() == nil {
		event := publicEvent{Event: "error", Data: map[string]string{"code": "stream_ended", "message": "回答流意外中断"}}
		events = append(events, event)
		writeSSE(w, event)
		flusher.Flush()
	}
	if r.Context().Err() != nil && assistantID != "" {
		stopCtx, stopCancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer stopCancel()
		_ = s.upstream.Stop(stopCtx, visitor, conversation.UpstreamSessionID, assistantID)
	}
	if terminal {
		encoded, _ := json.Marshal(events)
		if s.store.IdempotencyDone(context.Background(), visitor, conversationID, idempotencyKey, queryHash, base64.RawURLEncoding.EncodeToString(encoded)) == nil {
			idempotencyCompleted = true
		}
	}
}

func (s *Server) history(w http.ResponseWriter, r *http.Request) {
	visitor, _ := s.ensureVisitor(w, r)
	conversation, err := s.store.Conversation(r.Context(), r.PathValue("id"), visitor)
	if err != nil {
		writeError(w, 404, "conversation_not_found", "会话不存在", 0)
		return
	}
	raw, err := s.upstream.History(r.Context(), visitor, conversation.UpstreamSessionID)
	if err != nil {
		writeError(w, 502, "history_unavailable", "历史暂时不可用", 0)
		return
	}
	writeJSON(w, 200, sanitizeHistory(raw, s.pages))
}

func (s *Server) stop(w http.ResponseWriter, r *http.Request) {
	visitor, _ := s.ensureVisitor(w, r)
	conversationID := r.PathValue("id")
	conversation, err := s.store.Conversation(r.Context(), conversationID, visitor)
	if err != nil {
		writeError(w, 404, "conversation_not_found", "会话不存在", 0)
		return
	}
	messageID, err := s.store.Turn(r.Context(), conversationID, r.PathValue("turn"))
	if err != nil {
		writeError(w, 404, "turn_not_found", "回合不存在", 0)
		return
	}
	if err := s.upstream.Stop(r.Context(), visitor, conversation.UpstreamSessionID, messageID); err != nil {
		writeError(w, 502, "stop_failed", "暂时无法停止生成", 0)
		return
	}
	w.WriteHeader(http.StatusNoContent)
}

func (s *Server) deleteConversation(w http.ResponseWriter, r *http.Request) {
	visitor, _ := s.ensureVisitor(w, r)
	id := r.PathValue("id")
	conversation, err := s.store.Conversation(r.Context(), id, visitor)
	if err != nil {
		writeError(w, 404, "conversation_not_found", "会话不存在", 0)
		return
	}
	if err := s.upstream.DeleteSession(r.Context(), visitor, conversation.UpstreamSessionID); err != nil {
		writeError(w, 502, "delete_failed", "暂时无法删除会话", 0)
		return
	}
	_ = s.store.DeleteConversation(r.Context(), id)
	w.WriteHeader(http.StatusNoContent)
}

func (s *Server) citation(w http.ResponseWriter, r *http.Request) {
	_, _ = s.ensureVisitor(w, r)
	slug := strings.TrimPrefix(r.PathValue("slug"), "/")
	page, ok := s.pages[slug]
	if !ok {
		writeError(w, 404, "citation_not_found", "引用页不在当前公开清单中", 0)
		return
	}
	writeJSON(w, 200, map[string]any{"slug": page.Slug, "title": page.Title, "summary": page.Summary, "content": page.Content, "source_status": page.SourceStatus, "source_access": page.SourceAccess, "review_state": page.ReviewState, "source_verified": page.SourceVerified, "release_id": page.ReleaseID, "payload_sha256": page.PayloadSHA256, "is_draft": page.SourceStatus == "draft", "needs_review": page.ReviewState == "needs-human-review" || !page.SourceVerified})
}

func (s *Server) ensureVisitor(w http.ResponseWriter, r *http.Request) (string, string) {
	name := "llmwiki-anon"
	if s.config.CookieSecure {
		name = "__Host-llmwiki-anon"
	}
	cookie, err := r.Cookie(name)
	value := ""
	if err == nil && len(cookie.Value) >= 40 {
		value = cookie.Value
	}
	if value == "" {
		value = randomToken(32)
		http.SetCookie(w, &http.Cookie{Name: name, Value: value, Path: "/", MaxAge: int((7 * 24 * time.Hour).Seconds()), Secure: s.config.CookieSecure, HttpOnly: true, SameSite: http.SameSiteLaxMode})
	}
	// Namespace anonymous principals by the immutable release so conversations
	// from an older corpus can never appear under the newly active Wiki.
	return visitorID(s.config.CookieSecret, value+"\x00"+s.releaseID), value
}

func readQuery(w http.ResponseWriter, r *http.Request) (string, error) {
	r.Body = http.MaxBytesReader(w, r.Body, maxBodyBytes)
	decoder := json.NewDecoder(r.Body)
	decoder.DisallowUnknownFields()
	var body struct {
		Query string `json:"query"`
	}
	if err := decoder.Decode(&body); err != nil {
		return "", errors.New("请求仅允许 query 字段，且请求体不得超过 8KiB")
	}
	if err := decoder.Decode(&struct{}{}); err != io.EOF {
		return "", errors.New("请求体必须只包含一个 JSON 对象")
	}
	body.Query = strings.TrimSpace(body.Query)
	if len([]rune(body.Query)) == 0 || len([]rune(body.Query)) > 2000 {
		return "", errors.New("问题长度必须在 1–2000 个 Unicode 字符之间")
	}
	return body.Query, nil
}

func extractCitations(answer string, pages map[string]ManifestPage) ([]PublicCitation, bool, bool) {
	seen := map[string]bool{}
	result := []PublicCitation{}
	containsDraft := false
	containsUnreviewed := false
	for _, match := range citationPattern.FindAllStringSubmatch(answer, -1) {
		page, ok := pages[match[1]]
		if !ok || seen[page.Slug] {
			continue
		}
		seen[page.Slug] = true
		draft := page.SourceStatus == "draft"
		needsReview := page.ReviewState == "needs-human-review" || !page.SourceVerified
		containsDraft = containsDraft || draft
		containsUnreviewed = containsUnreviewed || needsReview
		result = append(result, PublicCitation{Slug: page.Slug, Title: page.Title, Summary: page.Summary, SourceStatus: page.SourceStatus, ReviewState: page.ReviewState, ReleaseID: page.ReleaseID, IsDraft: draft, NeedsReview: needsReview})
	}
	return result, containsDraft, containsUnreviewed
}

func sanitizeHistory(raw []byte, pages map[string]ManifestPage) map[string]any {
	var envelope struct {
		Data []struct{ Role, Content, CreatedAt string } `json:"data"`
	}
	if json.Unmarshal(raw, &envelope) != nil {
		return map[string]any{"messages": []any{}}
	}
	messages := make([]map[string]any, 0, len(envelope.Data))
	for _, message := range envelope.Data {
		if message.Role != "user" && message.Role != "assistant" {
			continue
		}
		citations, draft, unreviewed := extractCitations(message.Content, pages)
		messages = append(messages, map[string]any{"role": message.Role, "content": message.Content, "created_at": message.CreatedAt, "citations": citations, "contains_draft": draft, "contains_unreviewed": unreviewed})
	}
	return map[string]any{"messages": messages}
}

func clientIPPrefix(request *http.Request, trusted []*net.IPNet) string {
	remote := request.RemoteAddr
	host, _, err := net.SplitHostPort(remote)
	if err != nil {
		host = remote
	}
	ip := net.ParseIP(host)
	for _, network := range trusted {
		if ip != nil && network.Contains(ip) {
			if reported := net.ParseIP(strings.TrimSpace(request.Header.Get("X-Real-IP"))); reported != nil {
				ip = reported
			}
			break
		}
	}
	if ip == nil {
		return "unknown"
	}
	if v4 := ip.To4(); v4 != nil {
		return net.IP(v4).Mask(net.CIDRMask(24, 32)).String() + "/24"
	}
	return ip.Mask(net.CIDRMask(56, 128)).String() + "/56"
}

func randomToken(bytesCount int) string {
	buffer := make([]byte, bytesCount)
	if _, err := rand.Read(buffer); err != nil {
		panic(err)
	}
	return base64.RawURLEncoding.EncodeToString(buffer)
}

func writeSSE(w io.Writer, event publicEvent) {
	data, _ := json.Marshal(event.Data)
	fmt.Fprintf(w, "event: %s\ndata: %s\n\n", event.Event, data)
}
func writeJSON(w http.ResponseWriter, status int, value any) {
	w.Header().Set("Content-Type", "application/json; charset=utf-8")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(value)
}
func writeError(w http.ResponseWriter, status int, code, message string, retry time.Duration) {
	if retry > 0 {
		seconds := int(retry.Seconds())
		if seconds < 1 {
			seconds = 1
		}
		w.Header().Set("Retry-After", strconv.Itoa(seconds))
	}
	writeJSON(w, status, map[string]any{"error": map[string]string{"code": code, "message": message}})
}
func replayEvents(w http.ResponseWriter, encoded string) {
	raw, err := base64.RawURLEncoding.DecodeString(encoded)
	if err != nil {
		writeError(w, 409, "idempotency_unavailable", "无法重放上次响应", 0)
		return
	}
	var events []publicEvent
	if json.Unmarshal(raw, &events) != nil {
		writeError(w, 409, "idempotency_unavailable", "无法重放上次响应", 0)
		return
	}
	w.Header().Set("Content-Type", "text/event-stream; charset=utf-8")
	w.Header().Set("X-Accel-Buffering", "no")
	for _, event := range events {
		writeSSE(w, event)
	}
}

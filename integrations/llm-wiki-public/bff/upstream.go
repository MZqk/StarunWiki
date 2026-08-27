package main

import (
	"bufio"
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"strconv"
	"strings"
	"time"

	"github.com/golang-jwt/jwt/v5"
)

type Upstream struct {
	config Config
	client *http.Client
}

type upstreamEnvelope struct {
	Success bool            `json:"success"`
	Data    json.RawMessage `json:"data"`
}

type streamMessage struct {
	ResponseType       string `json:"response_type"`
	Content            string `json:"content"`
	Done               bool   `json:"done"`
	AssistantMessageID string `json:"assistant_message_id"`
}

func NewUpstream(config Config) *Upstream {
	// Streaming is bounded by the caller's PUBLIC_STREAM_TIMEOUT context.
	return &Upstream{config: config, client: &http.Client{}}
}

func (u *Upstream) externalToken(visitor string) (string, error) {
	now := time.Now()
	claims := jwt.MapClaims{
		"sub": visitor, "tenant_id": u.config.TenantID, "aud": "weknora",
		"iat": now.Unix(), "exp": now.Add(15 * time.Minute).Unix(),
	}
	return jwt.NewWithClaims(jwt.SigningMethodHS256, claims).SignedString([]byte(u.config.ExternalSecret))
}

func (u *Upstream) request(ctx context.Context, visitor, method, path string, body any) (*http.Response, error) {
	var reader io.Reader
	if body != nil {
		data, err := json.Marshal(body)
		if err != nil {
			return nil, err
		}
		reader = bytes.NewReader(data)
	}
	req, err := http.NewRequestWithContext(ctx, method, u.config.WeKnoraBaseURL+"/api/v1"+path, reader)
	if err != nil {
		return nil, err
	}
	token, err := u.externalToken(visitor)
	if err != nil {
		return nil, err
	}
	req.Header.Set("X-API-Key", u.config.WeKnoraAPIKey)
	req.Header.Set("X-External-User-Token", token)
	req.Header.Set("Content-Type", "application/json")
	return u.client.Do(req)
}

func (u *Upstream) CreateSession(ctx context.Context, visitor string) (string, error) {
	response, err := u.request(ctx, visitor, http.MethodPost, "/sessions", map[string]string{"title": "匿名知识问答"})
	if err != nil {
		return "", err
	}
	defer response.Body.Close()
	if response.StatusCode != http.StatusCreated {
		return "", fmt.Errorf("create session returned %d", response.StatusCode)
	}
	var envelope upstreamEnvelope
	if err := json.NewDecoder(io.LimitReader(response.Body, 1<<20)).Decode(&envelope); err != nil {
		return "", err
	}
	var session struct {
		ID string `json:"id"`
	}
	if err := json.Unmarshal(envelope.Data, &session); err != nil || session.ID == "" {
		return "", errors.New("invalid create-session response")
	}
	return session.ID, nil
}

func (u *Upstream) Stream(ctx context.Context, visitor, sessionID, query string) (*http.Response, error) {
	body := map[string]any{
		"query":              query,
		"agent_enabled":      true,
		"agent_id":           u.config.AgentID,
		"knowledge_base_ids": []string{u.config.KnowledgeBaseID},
		"disable_title":      true,
		"web_search_enabled": false,
		"mcp_service_ids":    []string{},
		"skill_names":        []string{},
		"images":             []any{},
		"attachment_ids":     []string{},
		"attachment_uploads": []any{},
		"channel":            "public-web",
	}
	return u.request(ctx, visitor, http.MethodPost, "/agent-chat/"+url.PathEscape(sessionID), body)
}

func (u *Upstream) Stop(ctx context.Context, visitor, sessionID, messageID string) error {
	response, err := u.request(ctx, visitor, http.MethodPost, "/sessions/"+url.PathEscape(sessionID)+"/stop", map[string]string{"message_id": messageID})
	if err != nil {
		return err
	}
	defer response.Body.Close()
	if response.StatusCode/100 != 2 {
		return fmt.Errorf("stop returned %d", response.StatusCode)
	}
	return nil
}

func (u *Upstream) DeleteSession(ctx context.Context, visitor, sessionID string) error {
	response, err := u.request(ctx, visitor, http.MethodDelete, "/sessions/"+url.PathEscape(sessionID), nil)
	if err != nil {
		return err
	}
	defer response.Body.Close()
	if response.StatusCode/100 != 2 && response.StatusCode != http.StatusNotFound {
		return fmt.Errorf("delete returned %d", response.StatusCode)
	}
	return nil
}

func (u *Upstream) History(ctx context.Context, visitor, sessionID string) (json.RawMessage, error) {
	response, err := u.request(ctx, visitor, http.MethodGet, "/messages/"+url.PathEscape(sessionID)+"/load?limit=100", nil)
	if err != nil {
		return nil, err
	}
	defer response.Body.Close()
	if response.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("history returned %d", response.StatusCode)
	}
	return io.ReadAll(io.LimitReader(response.Body, 4<<20))
}

func parseSSE(body io.Reader, handle func(streamMessage) error) error {
	scanner := bufio.NewScanner(body)
	scanner.Buffer(make([]byte, 64*1024), 2*1024*1024)
	var data strings.Builder
	flush := func() error {
		if data.Len() == 0 {
			return nil
		}
		var message streamMessage
		err := json.Unmarshal([]byte(data.String()), &message)
		data.Reset()
		if err != nil {
			return nil
		}
		return handle(message)
	}
	for scanner.Scan() {
		line := scanner.Text()
		if line == "" {
			if err := flush(); err != nil {
				return err
			}
			continue
		}
		if strings.HasPrefix(line, "data:") {
			if data.Len() > 0 {
				data.WriteByte('\n')
			}
			data.WriteString(strings.TrimSpace(strings.TrimPrefix(line, "data:")))
		}
	}
	if err := flush(); err != nil {
		return err
	}
	return scanner.Err()
}

func parseTenantID(value string) (uint64, error) { return strconv.ParseUint(value, 10, 64) }

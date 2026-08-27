package main

import (
	"context"
	"database/sql"
	"embed"
	"errors"
	"fmt"
	"math"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"time"

	_ "modernc.org/sqlite"
)

var (
	ErrNotFound      = errors.New("state not found")
	ErrBusy          = errors.New("state resource busy")
	ErrLimitExceeded = errors.New("state limit exceeded")
)

//go:embed migrations/*.sql
var migrationFiles embed.FS

type Conversation struct {
	UpstreamSessionID string `json:"upstream_session_id"`
	VisitorID         string `json:"visitor_id"`
	CreatedAt         int64  `json:"created_at"`
	ExpiresAt         int64  `json:"expires_at"`
}

type Store struct {
	db  *sql.DB
	ttl time.Duration
}

func NewStore(c Config) (*Store, error) {
	path := c.DatabasePath
	if path == "" {
		path = "/data/public-bff/public-bff.db"
	}
	if path != ":memory:" {
		if err := os.MkdirAll(filepath.Dir(path), 0o700); err != nil {
			return nil, fmt.Errorf("create state directory: %w", err)
		}
	}
	db, err := sql.Open("sqlite", path)
	if err != nil {
		return nil, fmt.Errorf("open state database: %w", err)
	}
	db.SetMaxOpenConns(1)
	db.SetMaxIdleConns(1)
	store := &Store{db: db, ttl: c.ConversationTTL}
	if err := store.initialize(context.Background()); err != nil {
		_ = db.Close()
		return nil, err
	}
	if path != ":memory:" {
		_ = os.Chmod(path, 0o600)
	}
	return store, nil
}

func (s *Store) initialize(ctx context.Context) error {
	for _, statement := range []string{
		"PRAGMA journal_mode=WAL",
		"PRAGMA foreign_keys=ON",
		"PRAGMA busy_timeout=5000",
		"PRAGMA synchronous=NORMAL",
	} {
		if _, err := s.db.ExecContext(ctx, statement); err != nil {
			return fmt.Errorf("configure state database: %w", err)
		}
	}
	if _, err := s.db.ExecContext(ctx, `
		CREATE TABLE IF NOT EXISTS schema_migrations (
			version TEXT PRIMARY KEY,
			applied_at INTEGER NOT NULL
		)`); err != nil {
		return fmt.Errorf("create migration table: %w", err)
	}
	entries, err := migrationFiles.ReadDir("migrations")
	if err != nil {
		return fmt.Errorf("read migrations: %w", err)
	}
	sort.Slice(entries, func(i, j int) bool { return entries[i].Name() < entries[j].Name() })
	for _, entry := range entries {
		if entry.IsDir() || !strings.HasSuffix(entry.Name(), ".sql") {
			continue
		}
		var applied int
		err := s.db.QueryRowContext(ctx, "SELECT 1 FROM schema_migrations WHERE version = ?", entry.Name()).Scan(&applied)
		if err == nil {
			continue
		}
		if !errors.Is(err, sql.ErrNoRows) {
			return fmt.Errorf("check migration %s: %w", entry.Name(), err)
		}
		body, err := migrationFiles.ReadFile("migrations/" + entry.Name())
		if err != nil {
			return fmt.Errorf("read migration %s: %w", entry.Name(), err)
		}
		tx, err := s.db.BeginTx(ctx, nil)
		if err != nil {
			return fmt.Errorf("begin migration %s: %w", entry.Name(), err)
		}
		if _, err = tx.ExecContext(ctx, string(body)); err == nil {
			_, err = tx.ExecContext(ctx, "INSERT INTO schema_migrations(version, applied_at) VALUES(?, ?)", entry.Name(), time.Now().Unix())
		}
		if err != nil {
			_ = tx.Rollback()
			return fmt.Errorf("apply migration %s: %w", entry.Name(), err)
		}
		if err := tx.Commit(); err != nil {
			return fmt.Errorf("commit migration %s: %w", entry.Name(), err)
		}
	}
	return nil
}

func (s *Store) Ping(ctx context.Context) error {
	var one int
	return s.db.QueryRowContext(ctx, "SELECT 1").Scan(&one)
}

func (s *Store) Close() error { return s.db.Close() }

func (s *Store) PutConversation(ctx context.Context, opaque string, conversation Conversation) error {
	conversation.ExpiresAt = time.Now().Add(s.ttl).Unix()
	_, err := s.db.ExecContext(ctx, `
		INSERT INTO conversations(id, upstream_session_id, visitor_id, created_at, expires_at)
		VALUES(?, ?, ?, ?, ?)
		ON CONFLICT(id) DO UPDATE SET
			upstream_session_id=excluded.upstream_session_id,
			visitor_id=excluded.visitor_id,
			created_at=excluded.created_at,
			expires_at=excluded.expires_at`,
		opaque, conversation.UpstreamSessionID, conversation.VisitorID, conversation.CreatedAt, conversation.ExpiresAt)
	return err
}

func (s *Store) Conversation(ctx context.Context, opaque, visitor string) (Conversation, error) {
	conversation, err := s.rawConversation(ctx, opaque)
	if err != nil {
		return Conversation{}, err
	}
	if conversation.ExpiresAt <= time.Now().Unix() || conversation.VisitorID != visitor {
		return Conversation{}, ErrNotFound
	}
	return conversation, nil
}

func (s *Store) rawConversation(ctx context.Context, opaque string) (Conversation, error) {
	var conversation Conversation
	err := s.db.QueryRowContext(ctx, `
		SELECT upstream_session_id, visitor_id, created_at, expires_at
		FROM conversations WHERE id = ?`, opaque).
		Scan(&conversation.UpstreamSessionID, &conversation.VisitorID, &conversation.CreatedAt, &conversation.ExpiresAt)
	if errors.Is(err, sql.ErrNoRows) {
		return Conversation{}, ErrNotFound
	}
	return conversation, err
}

func (s *Store) DeleteConversation(ctx context.Context, opaque string) error {
	_, err := s.db.ExecContext(ctx, "DELETE FROM conversations WHERE id = ?", opaque)
	return err
}

func (s *Store) PutTurn(ctx context.Context, conversation, turn, assistantMessageID string) error {
	_, err := s.db.ExecContext(ctx, `
		INSERT INTO turns(conversation_id, turn_id, assistant_message_id, expires_at)
		VALUES(?, ?, ?, ?)
		ON CONFLICT(conversation_id, turn_id) DO UPDATE SET
			assistant_message_id=excluded.assistant_message_id,
			expires_at=excluded.expires_at`,
		conversation, turn, assistantMessageID, time.Now().Add(s.ttl).Unix())
	return err
}

func (s *Store) Turn(ctx context.Context, conversation, turn string) (string, error) {
	var messageID string
	err := s.db.QueryRowContext(ctx, `
		SELECT assistant_message_id FROM turns
		WHERE conversation_id = ? AND turn_id = ? AND expires_at > ?`,
		conversation, turn, time.Now().Unix()).Scan(&messageID)
	if errors.Is(err, sql.ErrNoRows) {
		return "", ErrNotFound
	}
	return messageID, err
}

func (s *Store) LockConversation(ctx context.Context, conversation string, timeout time.Duration) (func(), error) {
	return s.acquireLease(ctx, "conversation", conversation, 1, timeout, ErrBusy)
}

func (s *Store) AcquireVisitor(ctx context.Context, visitor string, timeout time.Duration) (func(), error) {
	return s.acquireLease(ctx, "visitor", visitor, 2, timeout, ErrLimitExceeded)
}

func (s *Store) AcquireIP(ctx context.Context, ipPrefix string, timeout time.Duration) (func(), error) {
	return s.acquireLease(ctx, "ip", ipPrefix, 4, timeout, ErrLimitExceeded)
}

func (s *Store) acquireLease(ctx context.Context, kind, key string, limit int, timeout time.Duration, limitErr error) (func(), error) {
	now := time.Now().UnixMilli()
	leaseID := randomToken(18)
	tx, err := s.db.BeginTx(ctx, nil)
	if err != nil {
		return nil, err
	}
	rollback := true
	defer func() {
		if rollback {
			_ = tx.Rollback()
		}
	}()
	if _, err := tx.ExecContext(ctx, "DELETE FROM leases WHERE resource_kind = ? AND resource_key = ? AND expires_at <= ?", kind, key, now); err != nil {
		return nil, err
	}
	var count int
	if err := tx.QueryRowContext(ctx, "SELECT COUNT(*) FROM leases WHERE resource_kind = ? AND resource_key = ?", kind, key).Scan(&count); err != nil {
		return nil, err
	}
	if count >= limit {
		return nil, limitErr
	}
	if _, err := tx.ExecContext(ctx, `
		INSERT INTO leases(resource_kind, resource_key, lease_id, expires_at)
		VALUES(?, ?, ?, ?)`, kind, key, leaseID, time.Now().Add(timeout+time.Minute).UnixMilli()); err != nil {
		return nil, err
	}
	if err := tx.Commit(); err != nil {
		return nil, err
	}
	rollback = false
	return func() {
		releaseCtx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
		defer cancel()
		_, _ = s.db.ExecContext(releaseCtx, "DELETE FROM leases WHERE lease_id = ?", leaseID)
	}, nil
}

func (s *Store) RateLimit(ctx context.Context, visitor, ipPrefix string) (time.Duration, error) {
	now := time.Now().UTC()
	nowMS := now.UnixMilli()
	tx, err := s.db.BeginTx(ctx, nil)
	if err != nil {
		return 0, err
	}
	rollback := true
	defer func() {
		if rollback {
			_ = tx.Rollback()
		}
	}()

	bucketKey := "visitor:" + visitor
	tokens := 3.0
	updated := nowMS
	err = tx.QueryRowContext(ctx, "SELECT tokens, updated_at FROM rate_buckets WHERE bucket_key = ?", bucketKey).Scan(&tokens, &updated)
	if err != nil && !errors.Is(err, sql.ErrNoRows) {
		return 0, err
	}
	refillPerMS := 3.0 / 60000.0
	tokens = math.Min(3, tokens+float64(maxInt64(0, nowMS-updated))*refillPerMS)
	retry := time.Duration(0)
	if tokens >= 1 {
		tokens--
	} else {
		retry = time.Duration(math.Ceil((1-tokens)/refillPerMS)) * time.Millisecond
	}
	if _, err := tx.ExecContext(ctx, `
		INSERT INTO rate_buckets(bucket_key, tokens, updated_at, expires_at)
		VALUES(?, ?, ?, ?)
		ON CONFLICT(bucket_key) DO UPDATE SET tokens=excluded.tokens, updated_at=excluded.updated_at, expires_at=excluded.expires_at`,
		bucketKey, tokens, nowMS, now.Add(3*time.Minute).Unix()); err != nil {
		return 0, err
	}
	if retry > 0 {
		if err := tx.Commit(); err != nil {
			return 0, err
		}
		rollback = false
		return retry, nil
	}

	type fixedRule struct {
		key     string
		limit   int
		expires time.Time
	}
	minuteEnd := now.Truncate(time.Minute).Add(time.Minute)
	hourEnd := now.Truncate(time.Hour).Add(time.Hour)
	dayStart := time.Date(now.Year(), now.Month(), now.Day(), 0, 0, 0, 0, time.UTC)
	rules := []fixedRule{
		{key: "visitor-hour:" + visitor + ":" + now.Format("2006010215"), limit: 30, expires: hourEnd},
		{key: "visitor-day:" + visitor + ":" + now.Format("20060102"), limit: 100, expires: dayStart.Add(24 * time.Hour)},
		{key: "ip-minute:" + ipPrefix + ":" + now.Format("200601021504"), limit: 10, expires: minuteEnd},
	}
	for _, rule := range rules {
		var count int
		err := tx.QueryRowContext(ctx, "SELECT count FROM rate_counters WHERE counter_key = ? AND expires_at > ?", rule.key, now.Unix()).Scan(&count)
		if err != nil && !errors.Is(err, sql.ErrNoRows) {
			return 0, err
		}
		count++
		if _, err := tx.ExecContext(ctx, `
			INSERT INTO rate_counters(counter_key, count, expires_at)
			VALUES(?, ?, ?)
			ON CONFLICT(counter_key) DO UPDATE SET count=excluded.count, expires_at=excluded.expires_at`,
			rule.key, count, rule.expires.Unix()); err != nil {
			return 0, err
		}
		if count > rule.limit && retry == 0 {
			retry = time.Until(rule.expires)
		}
	}
	if err := tx.Commit(); err != nil {
		return 0, err
	}
	rollback = false
	return retry, nil
}

func (s *Store) IdempotencyStart(ctx context.Context, visitor, conversation, key, queryHash string) (string, bool, error) {
	now := time.Now().Unix()
	tx, err := s.db.BeginTx(ctx, nil)
	if err != nil {
		return "", false, err
	}
	rollback := true
	defer func() {
		if rollback {
			_ = tx.Rollback()
		}
	}()
	var storedHash, state, events string
	var expiresAt int64
	err = tx.QueryRowContext(ctx, `
		SELECT query_hash, state, encoded_events, expires_at
		FROM idempotency WHERE visitor_id = ? AND conversation_id = ? AND idempotency_key = ?`,
		visitor, conversation, key).Scan(&storedHash, &state, &events, &expiresAt)
	if err == nil && expiresAt > now {
		if err := tx.Commit(); err != nil {
			return "", false, err
		}
		rollback = false
		if state == "done" {
			return "done:" + storedHash + ":" + events, false, nil
		}
		return "running:" + storedHash, false, nil
	}
	if err != nil && !errors.Is(err, sql.ErrNoRows) {
		return "", false, err
	}
	if _, err := tx.ExecContext(ctx, `
		INSERT INTO idempotency(visitor_id, conversation_id, idempotency_key, query_hash, state, encoded_events, expires_at)
		VALUES(?, ?, ?, ?, 'running', '', ?)
		ON CONFLICT(visitor_id, conversation_id, idempotency_key) DO UPDATE SET
			query_hash=excluded.query_hash, state='running', encoded_events='', expires_at=excluded.expires_at`,
		visitor, conversation, key, queryHash, time.Now().Add(s.ttl).Unix()); err != nil {
		return "", false, err
	}
	if err := tx.Commit(); err != nil {
		return "", false, err
	}
	rollback = false
	return "", true, nil
}

func (s *Store) IdempotencyDone(ctx context.Context, visitor, conversation, key, queryHash, encodedEvents string) error {
	result, err := s.db.ExecContext(ctx, `
		UPDATE idempotency SET state='done', encoded_events=?, expires_at=?
		WHERE visitor_id=? AND conversation_id=? AND idempotency_key=? AND query_hash=? AND state='running'`,
		encodedEvents, time.Now().Add(s.ttl).Unix(), visitor, conversation, key, queryHash)
	if err != nil {
		return err
	}
	rows, err := result.RowsAffected()
	if err == nil && rows == 0 {
		return ErrNotFound
	}
	return err
}

func (s *Store) IdempotencyAbort(ctx context.Context, visitor, conversation, key string) {
	_, _ = s.db.ExecContext(ctx, `
		DELETE FROM idempotency
		WHERE visitor_id=? AND conversation_id=? AND idempotency_key=? AND state='running'`,
		visitor, conversation, key)
}

func (s *Store) DueCleanup(ctx context.Context, limit int64) ([]string, error) {
	rows, err := s.db.QueryContext(ctx, `
		SELECT id FROM conversations WHERE expires_at <= ? ORDER BY expires_at ASC LIMIT ?`,
		time.Now().Unix(), limit)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var ids []string
	for rows.Next() {
		var id string
		if err := rows.Scan(&id); err != nil {
			return nil, err
		}
		ids = append(ids, id)
	}
	return ids, rows.Err()
}

func (s *Store) PruneExpired(ctx context.Context) error {
	now := time.Now()
	tx, err := s.db.BeginTx(ctx, nil)
	if err != nil {
		return err
	}
	for _, statement := range []struct {
		query string
		arg   int64
	}{
		{query: "DELETE FROM turns WHERE expires_at <= ?", arg: now.Unix()},
		{query: "DELETE FROM idempotency WHERE expires_at <= ?", arg: now.Unix()},
		{query: "DELETE FROM rate_buckets WHERE expires_at <= ?", arg: now.Unix()},
		{query: "DELETE FROM rate_counters WHERE expires_at <= ?", arg: now.Unix()},
		{query: "DELETE FROM leases WHERE expires_at <= ?", arg: now.UnixMilli()},
	} {
		if _, err := tx.ExecContext(ctx, statement.query, statement.arg); err != nil {
			_ = tx.Rollback()
			return err
		}
	}
	return tx.Commit()
}

func maxInt64(a, b int64) int64 {
	if a > b {
		return a
	}
	return b
}

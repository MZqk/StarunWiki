package main

import (
	"context"
	"log/slog"
	"net/http"
	"os"
	"os/signal"
	"syscall"
	"time"
)

func main() {
	config, err := loadConfig()
	if err != nil {
		slog.Error("invalid configuration", "error", err)
		os.Exit(2)
	}
	pages, releaseID, err := loadManifest(config.ManifestPath)
	if err != nil {
		slog.Error("invalid public manifest", "error", err)
		os.Exit(2)
	}
	store, err := NewStore(config)
	if err != nil {
		slog.Error("state database unavailable", "error", err)
		os.Exit(2)
	}
	defer store.Close()
	server := NewServer(config, store, NewUpstream(config), pages, releaseID)

	httpServer := &http.Server{Addr: config.ListenAddr, Handler: server, ReadHeaderTimeout: 5 * time.Second, ReadTimeout: 15 * time.Second, IdleTimeout: 60 * time.Second, MaxHeaderBytes: 16 * 1024}
	go cleanupLoop(store, server.upstream)
	go func() {
		slog.Info("public wiki BFF listening", "addr", config.ListenAddr, "release", releaseID, "pages", len(pages))
		if err := httpServer.ListenAndServe(); err != nil && err != http.ErrServerClosed {
			slog.Error("BFF stopped", "error", err)
			os.Exit(1)
		}
	}()

	stop := make(chan os.Signal, 1)
	signal.Notify(stop, syscall.SIGINT, syscall.SIGTERM)
	<-stop
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	_ = httpServer.Shutdown(ctx)
}

func cleanupLoop(store *Store, upstream *Upstream) {
	ticker := time.NewTicker(time.Minute)
	defer ticker.Stop()
	for range ticker.C {
		ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
		_ = store.PruneExpired(ctx)
		ids, err := store.DueCleanup(ctx, 100)
		if err == nil {
			for _, id := range ids {
				conversation, getErr := store.rawConversation(ctx, id)
				if getErr == nil {
					if upstream.DeleteSession(ctx, conversation.VisitorID, conversation.UpstreamSessionID) == nil {
						_ = store.DeleteConversation(ctx, id)
					}
				}
			}
		}
		cancel()
	}
}

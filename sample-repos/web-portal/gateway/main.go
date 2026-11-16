// Backend-for-frontend: proxies /bff/* to internal services, enforces sessions and rate limits.
package main

import (
	"log/slog"
	"net/http"
	"net/http/httputil"
	"net/url"
	"os"
	"sync"
	"time"
)

type tokenBucket struct {
	mu       sync.Mutex
	tokens   map[string]float64
	last     map[string]time.Time
	rate     float64 // tokens per second
	capacity float64
}

func newTokenBucket(rate, capacity float64) *tokenBucket {
	return &tokenBucket{tokens: map[string]float64{}, last: map[string]time.Time{}, rate: rate, capacity: capacity}
}

// Allow reports whether the session may make another request now.
func (b *tokenBucket) Allow(key string) bool {
	b.mu.Lock()
	defer b.mu.Unlock()
	now := time.Now()
	tokens, ok := b.tokens[key]
	if !ok {
		tokens = b.capacity
	}
	if last, seen := b.last[key]; seen {
		tokens = min(b.capacity, tokens+now.Sub(last).Seconds()*b.rate)
	}
	b.last[key] = now
	if tokens < 1 {
		b.tokens[key] = tokens
		return false
	}
	b.tokens[key] = tokens - 1
	return true
}

func withSession(next http.Handler, limiter *tokenBucket) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		cookie, err := r.Cookie("portal_session")
		if err != nil || cookie.Value == "" {
			http.Error(w, "unauthenticated", http.StatusUnauthorized)
			return
		}
		if !limiter.Allow(cookie.Value) {
			w.Header().Set("Retry-After", "1")
			http.Error(w, "rate limited", http.StatusTooManyRequests)
			return
		}
		next.ServeHTTP(w, r)
	})
}

func main() {
	target, err := url.Parse(os.Getenv("INTERNAL_API_URL"))
	if err != nil || target.Host == "" {
		slog.Error("INTERNAL_API_URL must be set")
		os.Exit(1)
	}
	proxy := httputil.NewSingleHostReverseProxy(target)
	limiter := newTokenBucket(5, 20)

	mux := http.NewServeMux()
	mux.Handle("/bff/", http.StripPrefix("/bff", withSession(proxy, limiter)))
	mux.HandleFunc("/healthz", func(w http.ResponseWriter, _ *http.Request) { w.WriteHeader(http.StatusOK) })

	slog.Info("gateway listening", "addr", ":8081")
	if err := http.ListenAndServe(":8081", mux); err != nil {
		slog.Error("server stopped", "err", err)
	}
}

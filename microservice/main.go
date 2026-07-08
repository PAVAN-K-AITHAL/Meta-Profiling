package main

import (
	"context"
	"log"
	"net/http"
	_ "net/http/pprof"
	"os"
	"os/signal"
	"syscall"
	"time"

	"github.com/go-chi/chi/v5"
	"github.com/prometheus/client_golang/prometheus/promhttp"

	"github.com/perf-observatory/microservice/database"
	"github.com/perf-observatory/microservice/handlers"
	"github.com/perf-observatory/microservice/middleware"
)

func main() {
	log.Println("🚀 Starting Performance Observatory API...")

	dsn := os.Getenv("DATABASE_URL")
	if dsn == "" {
		dsn = "postgres://perfobs:perfobs@localhost:5432/perfobs?sslmode=disable"
		log.Println("⚠️  DATABASE_URL not set, using default:", dsn)
	}

	db, err := database.InitDB(dsn)
	if err != nil {
		log.Fatalf("❌ Database initialization failed: %v", err)
	}
	defer db.Close()

	r := chi.NewRouter()

	r.Use(middleware.MetricsMiddleware)

	r.Get("/healthz", handlers.Healthz)
	r.Get("/users", handlers.Users(db))
	r.Post("/compute", handlers.Compute)
	r.Get("/mixed", handlers.Mixed(db))

	r.Handle("/metrics", promhttp.Handler())

	r.Mount("/debug", http.DefaultServeMux)

	server := &http.Server{
		Addr:    ":8080",
		Handler: r,
	}

	go func() {
		log.Println("✅ API server listening on :8080")
		if err := server.ListenAndServe(); err != nil && err != http.ErrServerClosed {
			log.Fatalf("❌ Server failed: %v", err)
		}
	}()

	quit := make(chan os.Signal, 1)
	signal.Notify(quit, syscall.SIGINT, syscall.SIGTERM)
	<-quit

	log.Println("🛑 Shutting down gracefully...")
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()

	if err := server.Shutdown(ctx); err != nil {
		log.Fatalf("❌ Server forced to shutdown: %v", err)
	}
	log.Println("👋 Server stopped")
}

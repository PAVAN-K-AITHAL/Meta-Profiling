package handlers

import (
	"database/sql"
	"encoding/json"
	"net/http"
	"strconv"
	"time"

	"github.com/perf-observatory/microservice/database"
	"github.com/perf-observatory/microservice/middleware"
)

func Users(db *sql.DB) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		limitStr := r.URL.Query().Get("n")
		limit := 10
		if limitStr != "" {
			parsed, err := strconv.Atoi(limitStr)
			if err == nil && parsed > 0 && parsed <= 100 {
				limit = parsed
			}
		}

		start := time.Now()
		users, err := database.GetUsers(db, limit)
		duration := time.Since(start).Seconds()

		middleware.DBQueryDuration.WithLabelValues("select_users").Observe(duration)

		if err != nil {
			http.Error(w, `{"error": "database query failed"}`, http.StatusInternalServerError)
			return
		}

		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(users)
	}
}

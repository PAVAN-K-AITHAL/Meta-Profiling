package handlers

import (
	"crypto/sha256"
	"database/sql"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"net/http"
	"time"

	"github.com/perf-observatory/microservice/database"
	"github.com/perf-observatory/microservice/middleware"
)

type MixedResult struct {
	Users   []database.User `json:"users"`
	Compute ComputeResult   `json:"compute"`
}

func Mixed(db *sql.DB) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		start := time.Now()
		users, err := database.GetUsers(db, 5)
		duration := time.Since(start).Seconds()
		middleware.DBQueryDuration.WithLabelValues("select_users_mixed").Observe(duration)

		if err != nil {
			http.Error(w, `{"error": "database query failed"}`, http.StatusInternalServerError)
			return
		}

		hashCount := 1000
		var lastHash string
		for i := 0; i < hashCount; i++ {
			data := fmt.Sprintf("mixed-%d-%d", i, time.Now().UnixNano())
			hash := sha256.Sum256([]byte(data))
			lastHash = hex.EncodeToString(hash[:])
		}

		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(MixedResult{
			Users: users,
			Compute: ComputeResult{
				HashesComputed: hashCount,
				LastHash:       lastHash,
			},
		})
	}
}

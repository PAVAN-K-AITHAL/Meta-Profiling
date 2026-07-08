package handlers

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"net/http"
	"strconv"
	"time"
)

type ComputeResult struct {
	HashesComputed int    `json:"hashes_computed"`
	LastHash       string `json:"last_hash"`
}

func Compute(w http.ResponseWriter, r *http.Request) {
	nStr := r.URL.Query().Get("n")
	n := 5000
	if nStr != "" {
		parsed, err := strconv.Atoi(nStr)
		if err == nil && parsed > 0 && parsed <= 50000 {
			n = parsed
		}
	}

	var lastHash string
	for i := 0; i < n; i++ {
		data := fmt.Sprintf("iteration-%d-%d", i, time.Now().UnixNano())

		hash := sha256.Sum256([]byte(data))

		lastHash = hex.EncodeToString(hash[:])
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(ComputeResult{
		HashesComputed: n,
		LastHash:       lastHash,
	})
}

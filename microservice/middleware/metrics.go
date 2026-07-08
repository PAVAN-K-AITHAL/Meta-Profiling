package middleware

import (
	"net/http"
	"strconv"
	"time"

	"github.com/go-chi/chi/v5"
	"github.com/prometheus/client_golang/prometheus"
)

var HttpRequestsTotal = prometheus.NewCounterVec(
	prometheus.CounterOpts{
		Name: "http_requests_total",
		Help: "Total number of HTTP requests",
	},
	[]string{"method", "endpoint", "status_code"},
)

var HttpRequestDuration = prometheus.NewHistogramVec(
	prometheus.HistogramOpts{
		Name:    "http_request_duration_seconds",
		Help:    "HTTP request latency in seconds",
		Buckets: prometheus.ExponentialBuckets(0.001, 2, 15),
	},
	[]string{"method", "endpoint"},
)

var HttpRequestsInFlight = prometheus.NewGauge(
	prometheus.GaugeOpts{
		Name: "http_requests_in_flight",
		Help: "Number of HTTP requests currently being processed",
	},
)

var DBQueryDuration = prometheus.NewHistogramVec(
	prometheus.HistogramOpts{
		Name:    "db_query_duration_seconds",
		Help:    "Database query latency in seconds",
		Buckets: prometheus.ExponentialBuckets(0.0005, 2, 12),
	},
	[]string{"query_name"},
)

func init() {
	prometheus.MustRegister(HttpRequestsTotal)
	prometheus.MustRegister(HttpRequestDuration)
	prometheus.MustRegister(HttpRequestsInFlight)
	prometheus.MustRegister(DBQueryDuration)
}

type wrappedResponseWriter struct {
	http.ResponseWriter
	statusCode int
	written    bool
}

func (w *wrappedResponseWriter) WriteHeader(code int) {
	if !w.written {
		w.statusCode = code
		w.written = true
	}
	w.ResponseWriter.WriteHeader(code)
}

func (w *wrappedResponseWriter) Write(b []byte) (int, error) {
	if !w.written {
		w.statusCode = http.StatusOK
		w.written = true
	}
	return w.ResponseWriter.Write(b)
}

func MetricsMiddleware(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		HttpRequestsInFlight.Inc()
		defer HttpRequestsInFlight.Dec()

		start := time.Now()

		wrapped := &wrappedResponseWriter{ResponseWriter: w, statusCode: http.StatusOK}

		next.ServeHTTP(wrapped, r)

		duration := time.Since(start).Seconds()

		routePattern := chi.RouteContext(r.Context()).RoutePattern()
		if routePattern == "" {
			routePattern = r.URL.Path
		}

		status := strconv.Itoa(wrapped.statusCode)
		HttpRequestsTotal.WithLabelValues(r.Method, routePattern, status).Inc()
		HttpRequestDuration.WithLabelValues(r.Method, routePattern).Observe(duration)
	})
}

/**
 * highload.js — High CPU Stress Load Test for Meta-Profiling
 *
 * Purpose: Push the Go API to high CPU utilization so the profiling
 *          overhead measurement has a strong "signal" against a busy
 *          application (unlike baseline.js which left the API mostly idle).
 *
 * Strategy:
 *   - 80% POST /compute  (CPU-bound: SHA-256 hashing)
 *   - 20% GET  /mixed    (CPU + DB)
 *   - 0%  GET  /users    (excluded — purely I/O-bound, wastes CPU time on Postgres)
 *
 * Load: 10,000 req/s sustained using constant-arrival-rate.
 *       Pre-allocates 500 VUs, allows up to 2000 to maintain rate.
 *
 * Run:  k6 run --quiet --duration 600m loadtest/highload.js
 * Target: http://localhost (Nginx reverse proxy → Go API)
 */

import http from 'k6/http';
import { check } from 'k6';
import { Rate, Trend } from 'k6/metrics';

const errorRate = new Rate('error_rate');
const latency   = new Trend('request_latency', true);

export const options = {
    scenarios: {
        high_cpu_load: {
            executor: 'constant-arrival-rate',
            rate: 10000,               // 10,000 iterations per timeUnit
            timeUnit: '1s',            // → 10,000 req/s
            duration: '600m',          // Run for up to 10 hours (killed by Makefile)
            preAllocatedVUs: 500,      // Pre-warm VU pool
            maxVUs: 2000,              // Scale up aggressively to maintain rate
        },
    },
    thresholds: {
        // Relaxed — we WANT to saturate the CPU, not meet SLOs
        'http_req_duration': ['p(99)<5000'],   // 5s max
        'error_rate': ['rate<0.10'],           // 10% errors tolerated
    },
};

const BASE = __ENV.BASE_URL || 'http://localhost';

export default function () {
    const rand = Math.random() * 100;
    let res;

    if (rand < 80) {
        // 80% — CPU-bound: SHA-256 hashing (this is what generates cycles)
        res = http.post(`${BASE}/compute`, null, {
            tags: { endpoint: '/compute' },
        });
    } else {
        // 20% — Mixed: users + hashes (some CPU + some DB)
        res = http.get(`${BASE}/mixed`, {
            tags: { endpoint: '/mixed' },
        });
    }

    const ok = check(res, {
        'status is 200': (r) => r.status === 200,
    });

    errorRate.add(!ok);
    latency.add(res.timings.duration);
}

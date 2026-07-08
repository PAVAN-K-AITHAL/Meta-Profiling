/**
 * baseline.js — Steady-State Load Test
 * Worker 4: Orchestration / Load Testing
 *
 * Purpose: Establish a stable baseline at 5000 req/s for 5 minutes.
 * Mirrors the production-like weighted traffic mix:
 *   50% GET /users?n=10   (DB-bound)
 *   30% POST /compute     (CPU-bound)
 *   20% GET /mixed        (DB + CPU combined)
 *
 * Run: k6 run loadtest/baseline.js
 * Target: http://localhost (via Nginx reverse proxy on port 80)
 *
 * Thresholds:
 *   p99 latency < 200ms  (alert rule HighP99Latency fires at 200ms)
 *   error rate  < 1%     (alert rule HighErrorRate fires at 1%)
 */

import http from 'k6/http';
import { check, sleep } from 'k6';
import { Rate, Trend } from 'k6/metrics';

// Custom metrics for reporting
const errorRate = new Rate('error_rate');
const latency   = new Trend('request_latency', true);  // true = ms

export const options = {
    scenarios: {
        steady_state: {
            executor: 'constant-arrival-rate',
            rate: 5000,                // 5000 iterations per timeUnit
            timeUnit: '1s',            // → 5000 req/s
            duration: '5m',            // Run for 5 minutes
            preAllocatedVUs: 200,      // Pre-warm VU pool
            maxVUs: 500,               // Scale up if needed to maintain rate
        },
    },
    thresholds: {
        // p99 must stay below 200ms (matches alert_rules.yml HighP99Latency)
        'http_req_duration': ['p(99)<200'],
        // Error rate must stay below 1% (matches alert_rules.yml HighErrorRate)
        'error_rate': ['rate<0.01'],
    },
};

// Target: Nginx on port 80 (proxies to Go API)
// In CI or Docker: change to http://nginx or the container IP
const BASE = __ENV.BASE_URL || 'http://localhost';

export default function () {
    const rand = Math.random() * 100;
    let res;

    if (rand < 50) {
        // 50% — DB-bound: random 10 users from PostgreSQL
        res = http.get(`${BASE}/users?n=10`, {
            tags: { endpoint: '/users' },
        });
    } else if (rand < 80) {
        // 30% — CPU-bound: 5000 SHA-256 hashes (default n)
        res = http.post(`${BASE}/compute`, null, {
            tags: { endpoint: '/compute' },
        });
    } else {
        // 20% — Mixed: 5 users + 1000 hashes
        res = http.get(`${BASE}/mixed`, {
            tags: { endpoint: '/mixed' },
        });
    }

    // Validate response
    const ok = check(res, {
        'status is 200': (r) => r.status === 200,
        'response has body': (r) => r.body && r.body.length > 0,
    });

    errorRate.add(!ok);
    latency.add(res.timings.duration);
}

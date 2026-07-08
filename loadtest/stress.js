/**
 * stress.js — Stress / Ramp-Up Load Test
 * Worker 4: Orchestration / Load Testing
 *
 * Purpose: Ramp from 1000 req/s to 20000 req/s over 10 minutes,
 *          then hold at 20000 for 2 minutes, then ramp back down.
 *          Finds the saturation point — where p99 latency or error
 *          rate breaches acceptable thresholds.
 *
 * Run: k6 run loadtest/stress.js
 * Target: http://localhost (via Nginx reverse proxy on port 80)
 *
 * Thresholds (deliberately relaxed for stress — we WANT to find limits):
 *   p99 latency < 1s   (soft limit, we record what happens above it)
 *   error rate  < 5%   (5% is our "system broken" threshold)
 */

import http from 'k6/http';
import { check } from 'k6';
import { Rate, Trend } from 'k6/metrics';

const errorRate = new Rate('error_rate');
const latency   = new Trend('request_latency', true);

export const options = {
    scenarios: {
        stress_ramp: {
            executor: 'ramping-arrival-rate',
            startRate: 1000,           // Start at 1000 req/s
            timeUnit: '1s',
            preAllocatedVUs: 200,
            maxVUs: 1000,              // Allow up to 1000 VUs to maintain rate
            stages: [
                { target: 1000,  duration: '1m'  },  // Warm-up at 1000 req/s
                { target: 5000,  duration: '2m'  },  // Ramp to 5K
                { target: 10000, duration: '2m'  },  // Ramp to 10K
                { target: 15000, duration: '2m'  },  // Ramp to 15K
                { target: 20000, duration: '2m'  },  // Ramp to 20K (saturation point)
                { target: 20000, duration: '2m'  },  // Hold at 20K — observe degradation
                { target: 0,     duration: '1m'  },  // Ramp down (recovery test)
            ],
        },
    },
    thresholds: {
        // Relaxed thresholds — stress test INTENTIONALLY pushes past limits
        'http_req_duration': ['p(99)<1000'],    // 1s max — we want to see what happens
        'error_rate': ['rate<0.05'],            // 5% errors = system overloaded
    },
};

const BASE = __ENV.BASE_URL || 'http://localhost';

export default function () {
    const rand = Math.random() * 100;
    let res;

    if (rand < 50) {
        res = http.get(`${BASE}/users?n=10`, {
            tags: { endpoint: '/users' },
        });
    } else if (rand < 80) {
        res = http.post(`${BASE}/compute`, null, {
            tags: { endpoint: '/compute' },
        });
    } else {
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

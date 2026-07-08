/**
 * spike.js — Spike Load Test
 * Worker 4: Orchestration / Load Testing
 *
 * Purpose: Simulate sudden traffic spikes — 30s burst at 15000 req/s,
 *          then 2 minutes of cooldown at 1000 req/s. Repeated 3 times.
 *          Tests elasticity and recovery: does the system recover after a spike,
 *          or does it remain degraded?
 *
 * Run: k6 run loadtest/spike.js
 * Target: http://localhost (via Nginx reverse proxy on port 80)
 *
 * Thresholds:
 *   p99 latency < 500ms  (spike can briefly exceed 200ms, but must recover)
 *   error rate  < 2%
 */

import http from 'k6/http';
import { check } from 'k6';
import { Rate, Trend } from 'k6/metrics';

const errorRate = new Rate('error_rate');
const latency   = new Trend('request_latency', true);

export const options = {
    scenarios: {
        spike_test: {
            executor: 'ramping-arrival-rate',
            startRate: 1000,
            timeUnit: '1s',
            preAllocatedVUs: 300,
            maxVUs: 800,
            stages: [
                // Warm-up
                { target: 1000,  duration: '30s' },  // Baseline warm-up

                // Spike 1
                { target: 15000, duration: '5s'  },  // Sudden spike to 15K req/s
                { target: 15000, duration: '30s' },  // Hold spike
                { target: 1000,  duration: '5s'  },  // Drop back
                { target: 1000,  duration: '2m'  },  // Cooldown — observe recovery

                // Spike 2
                { target: 15000, duration: '5s'  },
                { target: 15000, duration: '30s' },
                { target: 1000,  duration: '5s'  },
                { target: 1000,  duration: '2m'  },  // Cooldown

                // Spike 3
                { target: 15000, duration: '5s'  },
                { target: 15000, duration: '30s' },
                { target: 1000,  duration: '5s'  },
                { target: 1000,  duration: '2m'  },  // Final cooldown

                // Ramp down
                { target: 0,     duration: '30s' },
            ],
        },
    },
    thresholds: {
        // p99 can spike briefly, but should recover to <500ms during cooldown
        'http_req_duration': ['p(99)<500'],
        'error_rate': ['rate<0.02'],
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

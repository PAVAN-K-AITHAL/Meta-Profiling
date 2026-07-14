import http from 'k6/http';
import { check } from 'k6';
import { Rate, Trend } from 'k6/metrics';

const errorRate = new Rate('error_rate');
const latency   = new Trend('request_latency', true);

export const options = {
    scenarios: {
        constant_work: {
            executor: 'shared-iterations',
            vus: 50,
            iterations: __ENV.ITERATIONS || 100000,
            maxDuration: '30m', // Give it up to 30 minutes to complete
        },
    },
    thresholds: {
        'http_req_duration': ['p(99)<5000'],
        'error_rate': ['rate<0.10'],
    },
};

const BASE = __ENV.BASE_URL || 'http://localhost';

export default function () {
    const rand = Math.random() * 100;
    let res;

    if (rand < 80) {
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

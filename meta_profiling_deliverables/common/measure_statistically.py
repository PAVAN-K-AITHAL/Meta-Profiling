import argparse
import subprocess
import time
import warnings
import math
import pandas as pd
from scipy import stats
import os

EVENTS = "cycles,page-faults,branch-misses,context-switches,cache-misses"

def run_cmd(cmd):
    res = subprocess.run(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if res.returncode != 0 and "kill" not in cmd and "pgrep" not in cmd:
        print(f"Command failed: {cmd}\nError: {res.stderr}")
    return res

def parse_perf_csv(csv_path):
    """Parse perf stat CSV output. Returns (results_dict, multiplexed_flag).
    
    perf stat -x ',' format: value,unit,event,runtime,pcnt-running,...
    If pcnt-running < 1.00, multiplexing occurred for that event.
    """
    results = {}
    multiplexed = False
    try:
        with open(csv_path, 'r') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                parts = line.split(',')
                if len(parts) >= 3:
                    val = parts[0]
                    metric = parts[2]
                    if val != '<not counted>' and val.isdigit():
                        results[metric] = int(val)
                    else:
                        results[metric] = 0
                    # Check for multiplexing: pcnt-running is in column 4
                    if len(parts) >= 5 and parts[4]:
                        try:
                            pcnt = float(parts[4])
                            if pcnt < 1.00:
                                multiplexed = True
                        except ValueError:
                            pass
    except Exception as e:
        print(f"Error parsing {csv_path}: {e}")
    return results, multiplexed

def get_api_pid():
    """Find the Go API PID on the host.
    
    With pid:host in docker-compose, the /api process is visible on the host directly.
    Falls back to docker inspect if pgrep fails.
    """
    # Try pgrep first (works when container uses pid:host)
    res = run_cmd("pgrep -f '^/api$'")
    pid = res.stdout.strip().split('\n')[0] if res.stdout.strip() else ""
    if pid.isdigit():
        return pid
    # Fallback: docker inspect
    res = run_cmd("docker inspect --format '{{.State.Pid}}' perf-go-api")
    pid = res.stdout.strip()
    if pid.isdigit() and pid != "0":
        return pid
    raise Exception("Could not find Go API PID. Is perf-go-api running? Alternatively, provide a PID using --pid.")

def cohens_d(group1, group2):
    """Calculate Cohen's d effect size between two groups."""
    n1, n2 = len(group1), len(group2)
    var1 = sum((x - sum(group1)/n1)**2 for x in group1) / (n1 - 1) if n1 > 1 else 0
    var2 = sum((x - sum(group2)/n2)**2 for x in group2) / (n2 - 1) if n2 > 1 else 0
    pooled_std = math.sqrt(((n1 - 1) * var1 + (n2 - 1) * var2) / (n1 + n2 - 2))
    if pooled_std == 0:
        return float('inf') if sum(group1)/n1 != sum(group2)/n2 else 0.0
    return abs(sum(group1)/n1 - sum(group2)/n2) / pooled_std

def main():
    parser = argparse.ArgumentParser(description="Statistical Measurement of Profiling Overhead")
    parser.add_argument("--iterations", type=int, default=100, help="Number of times to run the test")
    parser.add_argument("--duration", type=int, default=2, help="Duration in seconds for each measurement window")
    parser.add_argument("--pid", type=str, help="Optional specific PID to trace. Overrides default docker target.")
    parser.add_argument("--output", type=str, default="/tmp/overhead_results.csv", help="Path to save raw results CSV")
    args = parser.parse_args()

    api_pid = args.pid if args.pid else get_api_pid()
    print(f"Found API PID: {api_pid}")
    
    baseline_samples = {metric: [] for metric in EVENTS.split(',')}
    tool_samples = {metric: [] for metric in EVENTS.split(',')}

    print(f"Starting {args.iterations} iterations (Window: {args.duration}s)...")
    print(f"  Monitoring for PMU multiplexing...")
    
    multiplex_warnings = 0
    
    for i in range(args.iterations):
        print(f"  Iteration {i+1}/{args.iterations}...  (multiplex warnings: {multiplex_warnings})", end='\r')
        
        # 1. Baseline
        run_cmd(f"sudo taskset -c 7,15 perf stat -x ',' -e {EVENTS} -p {api_pid} -o /tmp/base_metrics.csv -- sleep {args.duration}")
        base_res, base_mux = parse_perf_csv("/tmp/base_metrics.csv")
        if base_mux:
            multiplex_warnings += 1
        for m, v in base_res.items():
            for exp_m in baseline_samples.keys():
                if exp_m.strip('/') in m.strip('/') or m.strip('/') in exp_m.strip('/'):
                    baseline_samples[exp_m].append(v)
                    break
            
        # 2. Tool overhead
        run_cmd("sudo pkill -9 -x perf 2>/dev/null")
        run_cmd(f"sudo taskset -c 6,14 perf record -g -e cycles:k -c 100000 -p {api_pid} -o /tmp/perf-experiment.data >/dev/null 2>&1 &")
        time.sleep(0.5) # Let it start
        perf_pid = run_cmd("pgrep -x perf | head -n 1").stdout.strip()
        
        if perf_pid.isdigit():
            run_cmd(f"sudo taskset -c 7,15 perf stat -x ',' -e {EVENTS} -p {perf_pid} -o /tmp/tool_metrics.csv -- sleep {args.duration}")
            tool_res, tool_mux = parse_perf_csv("/tmp/tool_metrics.csv")
            if tool_mux:
                multiplex_warnings += 1
            for m, v in tool_res.items():
                for exp_m in tool_samples.keys():
                    if exp_m.strip('/') in m.strip('/') or m.strip('/') in exp_m.strip('/'):
                        tool_samples[exp_m].append(v)
                        break
        
        run_cmd("sudo pkill -9 -x perf 2>/dev/null")

    # --- Multiplexing Report ---
    print(f"\n")
    if multiplex_warnings > 0:
        print(f"  ⚠️  WARNING: PMU multiplexing detected in {multiplex_warnings}/{args.iterations * 2} measurements!")
        print(f"  ⚠️  Results may be inaccurate. Reduce the number of hardware events.")
    else:
        print(f"  ✅ No PMU multiplexing detected across {args.iterations * 2} measurements.")

    # --- Analysis ---
    results = []
    for metric in EVENTS.split(','):
        if not baseline_samples[metric] or not tool_samples[metric]:
            continue
            
        b_data = baseline_samples[metric]
        t_data = tool_samples[metric]
        
        b_mean = sum(b_data) / len(b_data)
        t_mean = sum(t_data) / len(t_data)
        b_std = (sum((x - b_mean)**2 for x in b_data) / (len(b_data) - 1)) ** 0.5 if len(b_data) > 1 else 0
        t_std = (sum((x - t_mean)**2 for x in t_data) / (len(t_data) - 1)) ** 0.5 if len(t_data) > 1 else 0

        # Overhead percentage: how much does the tool consume relative to the app
        if b_mean > 0:
            overhead_pct = f"{(t_mean / b_mean) * 100:.5f}%"
        else:
            overhead_pct = "N/A"

        # Handle edge cases that cause catastrophic cancellation
        if b_std == 0 and t_std == 0:
            # Both groups have zero variance (e.g., always 0 vs always 32)
            if b_mean == t_mean:
                p_val_str = "1.0000e+00"
                is_sig = "NO"
            else:
                p_val_str = "<1e-300"
                is_sig = "YES"
        else:
            # Suppress scipy precision warning - we handle edge cases above
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", RuntimeWarning)
                t_stat, p_val = stats.ttest_ind(b_data, t_data, equal_var=False)
            
            if math.isnan(p_val):
                p_val_str = "NaN"
                is_sig = "NO"
            else:
                p_val_str = f"{p_val:.4e}"
                is_sig = "YES" if p_val < 0.05 else "NO"
        
        d = cohens_d(b_data, t_data)
        d_str = f"{d:.2f}" if not math.isinf(d) else "∞"

        results.append({
            "Metric": metric,
            "Base Mean": int(b_mean),
            "Base Std": int(b_std),
            "Tool Mean": int(t_mean),
            "Tool Std": int(t_std),
            "Overhead %": overhead_pct,
            "P-Value": p_val_str,
            "Cohen's d": d_str,
            "Significant?": is_sig
        })
        
    print(f"\n\n{'='*90}")
    print(f"  Statistical Results — Welch's t-test ({args.iterations} iterations, {args.duration}s window)")
    print(f"{'='*90}")
    if results:
        df = pd.DataFrame(results)
        print(df.to_string(index=False))
        
        # Save raw data to CSV
        df.to_csv(args.output, index=False)
        print(f"\n  Results saved to: {args.output}")
    else:
        print("  No data collected.")
    print(f"{'='*90}")

if __name__ == "__main__":
    main()

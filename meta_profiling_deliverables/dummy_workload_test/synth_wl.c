/*
 * synth_wl.c — controllable synthetic workload for the cross-workload test.
 *
 *   synth_wl <stack_depth> <working_set_MB> [steps]
 *
 * With [steps], exits after steps × 1024 dependent loads (fixed work, for
 * wall-clock cross-checks); without it, runs forever.
 *
 * Recurses <stack_depth> frames deep, then chases pointers forever through a
 * random cyclic permutation of <working_set_MB> MB:
 *   - stack depth sets how many frames perf record -g must unwind per sample
 *     (frame-pointer unwinding walks one frame per step);
 *   - working-set size sets how memory-bound the workload is
 *     (1 MB fits in L2/L3, 256 MB is DRAM-bound on a 20 MB LLC).
 * Both stay constant for the whole run, so every measurement window sees the
 * same behaviour (unlike lat_mem_rd, which sweeps array sizes).
 *
 * Build (the runner does this):
 *   gcc -O2 -fno-omit-frame-pointer -fno-optimize-sibling-calls synth_wl.c -o synth_wl
 * Frame pointers keep -g unwinding exact; no sibling calls keeps every
 * recursion frame on the stack.
 *
 * Writes /tmp/synth_wl.ready from the innermost frame once set-up is
 * finished, so the runner never measures the initialisation phase. The file
 * holds the measured stack span (stack_bytes), which grows with depth.
 */
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>

#define READY_FILE "/tmp/synth_wl.ready"

static size_t *next_idx;
static volatile size_t sink;
static long steps_arg = -1;             /* < 0: run forever */
static char *stack_top;                 /* address of a local in main() */
static int depth_arg;
static long mb_arg;

static void __attribute__((noinline)) chase_forever(void)
{
    char here;
    /* Report the real stack span so the runner can verify the depth */
    FILE *f = fopen(READY_FILE, "w");
    if (f) {
        fprintf(f, "depth=%d working_set_mb=%ld stack_bytes=%ld\n",
                depth_arg, mb_arg, (long)(stack_top - &here));
        fclose(f);
    }
    size_t p = 0;
    for (long s = 0; steps_arg < 0 || s < steps_arg; s++) {
        /* 1024 dependent loads per outer step; the store keeps the loop alive */
        for (int i = 0; i < 1024; i++)
            p = next_idx[p];
        sink = p;
    }
}

static int __attribute__((noinline)) descend(int depth)
{
    if (depth <= 1) {
        chase_forever();
        return 1;
    }
    /* Work after the call: prevents the compiler from collapsing the frame */
    int r = descend(depth - 1);
    sink += (size_t)r;
    return r + 1;
}

static uint64_t rng_state = 0x9E3779B97F4A7C15ull;
static uint64_t xorshift64(void)
{
    rng_state ^= rng_state << 13;
    rng_state ^= rng_state >> 7;
    rng_state ^= rng_state << 17;
    return rng_state;
}

int main(int argc, char **argv)
{
    char top;
    stack_top = &top;
    if (argc != 3 && argc != 4) {
        fprintf(stderr, "usage: %s <stack_depth 1..120> <working_set_MB> [steps]\n", argv[0]);
        return 2;
    }
    if (argc == 4)
        steps_arg = atol(argv[3]);
    int depth = atoi(argv[1]);
    long mb = atol(argv[2]);
    if (depth < 1 || depth > 120 || mb < 1) {
        fprintf(stderr, "stack_depth must be 1..120 (perf_event_max_stack is 127), "
                        "working_set_MB >= 1\n");
        return 2;
    }

    size_t n = (size_t)mb * 1024 * 1024 / sizeof(size_t);
    next_idx = malloc(n * sizeof(size_t));
    size_t *order = malloc(n * sizeof(size_t));
    if (!next_idx || !order) {
        perror("malloc");
        return 1;
    }

    /* Random single cycle (Sattolo's algorithm): every load misses the
     * prefetcher, and the chase visits the whole working set. */
    for (size_t i = 0; i < n; i++)
        order[i] = i;
    for (size_t i = n - 1; i > 0; i--) {
        size_t j = xorshift64() % i;
        size_t t = order[i]; order[i] = order[j]; order[j] = t;
    }
    for (size_t i = 0; i < n; i++)
        next_idx[order[i]] = order[(i + 1) % n];
    free(order);

    depth_arg = depth;
    mb_arg = mb;
    descend(depth);   /* writes READY_FILE from the innermost frame */
    return 0;
}

#include <stdio.h>
#include <stdlib.h>
#include <time.h>
#include <unistd.h>

#define ARRAY_SIZE 10000000

int main() {
    printf("Dummy workload started. PID: %d\n", getpid());
    
    int *data = malloc(ARRAY_SIZE * sizeof(int));
    if (!data) return 1;

    // Fill array
    for (int i = 0; i < ARRAY_SIZE; i++) {
        data[i] = rand() % 100;
    }

    long long sum = 0;
    while (1) {
        // Generate memory access (cache misses) and branching
        for (int i = 0; i < ARRAY_SIZE; i += (rand() % 10 + 1)) {
            if (data[i] > 50) {
                sum += data[i];
            } else {
                sum -= data[i];
            }
        }
    }

    free(data);
    return 0;
}

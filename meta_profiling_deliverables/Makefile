.PHONY: all compile test-single test-stat clean

all: compile test-single test-stat

# Compile the dummy workload
compile:
	@echo "Compiling dummy workload..."
	gcc dummy_workload.c -o dummy_workload

# Run the single overhead test
test-single: compile
	@echo "========================================="
	@echo "Running single overhead measurement test"
	@echo "========================================="
	@# Start the dummy workload in the background and save its PID
	@taskset -c 4-5 ./dummy_workload > /dev/null & \
	PID=$$!; \
	echo "Started dummy workload with PID: $$PID (pinned to CPUs 4-5)"; \
	sleep 1; \
	sudo ./measure_overhead.sh $$PID; \
	kill -9 $$PID; \
	echo "Killed dummy workload."

# Run the statistical overhead test
test-stat: compile
	@echo "========================================="
	@echo "Running statistical measurement test"
	@echo "========================================="
	@# Start the dummy workload in the background and save its PID
	@taskset -c 4-5 ./dummy_workload > /dev/null & \
	PID=$$!; \
	echo "Started dummy workload with PID: $$PID (pinned to CPUs 4-5)"; \
	sleep 1; \
	sudo python3 measure_statistically.py --iterations 1000 --duration 2 --pid $$PID; \
	kill -9 $$PID; \
	echo "Killed dummy workload."

# Clean compiled binaries and temp files
clean:
	@echo "Cleaning up..."
	rm -f dummy_workload
	sudo rm -f /tmp/base_metrics.csv /tmp/tool_metrics.csv /tmp/perf-experiment.data

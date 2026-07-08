#!/bin/bash

# setup_isolcpus.sh - Configure GRUB for Core Isolation
#
# This script configures GRUB to completely isolate CPUs 4-7 from the kernel
# scheduler. This prevents system processes (kswapd, sensors, etc.) from
# running on these cores and skewing profiling results.
#
# WARNING: This script modifies /etc/default/grub and updates grub.
# A system reboot is REQUIRED after running this script.

CORES_TO_ISOLATE="4-9"
GRUB_FILE="/etc/default/grub"

echo "=========================================="
echo "Configuring Core Isolation for CPUs $CORES_TO_ISOLATE"
echo "=========================================="

# Check if script is run as root
if [ "$EUID" -ne 0 ]; then
  echo "Please run as root (sudo)"
  exit 1
fi

# The kernel parameters to add
PARAMS="isolcpus=$CORES_TO_ISOLATE nohz_full=$CORES_TO_ISOLATE rcu_nocbs=$CORES_TO_ISOLATE"

# Check if already applied
if grep -q "isolcpus=$CORES_TO_ISOLATE" "$GRUB_FILE"; then
    echo "Core isolation parameters already exist in $GRUB_FILE"
else
    echo "Backing up $GRUB_FILE to ${GRUB_FILE}.bak..."
    cp $GRUB_FILE ${GRUB_FILE}.bak

    # Append to GRUB_CMDLINE_LINUX_DEFAULT
    # This uses sed to safely append the params inside the quotes
    echo "Adding parameters: $PARAMS"
    sed -i "s/^GRUB_CMDLINE_LINUX_DEFAULT=\"\(.*\)\"/GRUB_CMDLINE_LINUX_DEFAULT=\"\1 $PARAMS\"/" $GRUB_FILE
    
    echo "Updating GRUB..."
    if command -v update-grub &> /dev/null; then
        update-grub
    elif command -v grub2-mkconfig &> /dev/null; then
        grub2-mkconfig -o /boot/grub2/grub.cfg
    else
        echo "Error: Could not find update-grub or grub2-mkconfig. Please update grub manually."
        exit 1
    fi
    
    echo "Success! You MUST REBOOT the system for changes to take effect."
    echo "After reboot, verify with: cat /proc/cmdline"
fi

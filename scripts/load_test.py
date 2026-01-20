#!/usr/bin/env python3
"""
Load Testing Script - MOVED

This script has been moved to tests/load_test.py with improved functionality:
- Direct database population for historical data
- Better load testing with concurrent users
- Realistic traffic pattern simulation

Usage:
    python tests/load_test.py --all              # Populate + load test
    python tests/load_test.py --populate         # Just populate historical data
    python tests/load_test.py --load             # Just run load test

Run 'python tests/load_test.py --help' for all options.
"""
import sys
import os

print("=" * 60)
print("NOTE: This script has been moved to tests/load_test.py")
print("=" * 60)
print()
print("The new script has improved functionality:")
print("  - Direct database population (no API calls needed)")
print("  - Realistic traffic pattern simulation")
print("  - Better concurrent load testing")
print()
print("Usage:")
print("  python tests/load_test.py --all        # Populate + load test")
print("  python tests/load_test.py --populate   # Just populate data")
print("  python tests/load_test.py --load       # Just run load test")
print()
print("Run 'python tests/load_test.py --help' for all options.")
print("=" * 60)

# Try to run the new script with the same arguments
if len(sys.argv) > 1:
    print()
    print("Forwarding to tests/load_test.py...")
    script_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    new_script = os.path.join(script_dir, "tests", "load_test.py")
    os.execv(sys.executable, [sys.executable, new_script] + sys.argv[1:])

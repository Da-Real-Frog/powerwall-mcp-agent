"""Prints ERCOT's current grid condition and how the reserve controller reads it, without starting the server.

Run from the server/ directory:  uv run scripts/check_ercot.py
"""
from server import ercot

condition = ercot.get_condition()
for key, value in condition.items():
    print(f"{key:<12}{value}")

emergency, tight = ercot.classify(condition)
print(f"\nController reads this as: {emergency and 'warning (100%)' or tight and 'watch (80%)' or 'normal'}")

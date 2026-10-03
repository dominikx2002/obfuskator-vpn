"""Atrapa msmtp do testow lokalnych: dopisuje maila (stdin) do pliku z argumentu."""
import sys

with open(sys.argv[1], "ab") as f:
    f.write(sys.stdin.buffer.read() + b"\n=====\n")

"""pytest configuration — adds auto_straddle parent to sys.path so tests can import modules directly."""
import sys
import os

# Add the auto_straddle directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

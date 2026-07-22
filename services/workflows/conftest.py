import os
import sys

# Make the service modules (auth.py, video.py, ...) importable from tests.
sys.path.insert(0, os.path.dirname(__file__))

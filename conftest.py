import os
import sys

# Put the repo root on sys.path so `import services...` / `import server` resolve
# when pytest collects from tests/ (whether run in Docker or bare in CI).
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import sys
from pathlib import Path

# Add repo root to path so `import core` and `import adapters` work
# regardless of where pytest is invoked from.
sys.path.insert(0, str(Path(__file__).parent))
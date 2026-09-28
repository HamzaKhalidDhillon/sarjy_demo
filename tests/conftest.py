import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

# Keep tests that import the app off the real dev database
os.environ.setdefault("DATABASE_URL", "sqlite:///" + os.path.join(os.path.dirname(__file__), ".test_data", "test.db"))
os.environ["LLM_PROVIDER"] = "offline"

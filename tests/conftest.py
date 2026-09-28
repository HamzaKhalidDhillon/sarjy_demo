import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

# Always use a throwaway test database, even if DATABASE_URL is set in the shell (it could
# point at the real Supabase database)
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(os.path.dirname(__file__), ".test_data", "test.db")
os.environ["LLM_PROVIDER"] = "offline"
# Tests never call real (paid) APIs, even when the shell has real keys loaded
for key in ("OPENAI_API_KEY", "CALCOM_API_KEY"):
    os.environ.pop(key, None)

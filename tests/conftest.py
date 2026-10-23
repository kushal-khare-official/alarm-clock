import os
import sys

from hypothesis import settings

# CI runs the same examples every time so a red build is reproducible; locally we explore.
settings.register_profile("ci", derandomize=True, max_examples=300)
settings.register_profile("dev", max_examples=200)
settings.load_profile(os.environ.get("HYPOTHESIS_PROFILE", "dev"))

# Real-terminal tests need termios/pty. Skipped at collection so Windows never imports them.
collect_ignore = ["test_keys_posix.py", "e2e"] if sys.platform == "win32" else []

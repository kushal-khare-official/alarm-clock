import os

from hypothesis import settings

# CI runs the same examples every time so a red build is reproducible; locally we explore.
settings.register_profile("ci", derandomize=True, max_examples=300)
settings.register_profile("dev", max_examples=200)
settings.load_profile(os.environ.get("HYPOTHESIS_PROFILE", "dev"))

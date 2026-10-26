# CBBL portal: Passenger (inside Open OnDemand's per-user nginx) loads `application` from this file.
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from cbbl.web import application  # noqa: E402,F401

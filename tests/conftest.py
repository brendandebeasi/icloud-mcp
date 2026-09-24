import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

os.environ.setdefault("ICLOUD_EMAIL", "tester@icloud.com")
os.environ.setdefault("ICLOUD_APP_SPECIFIC_PASSWORD", "aaaa-bbbb-cccc-dddd")
os.environ.setdefault("DEFAULT_TIMEZONE", "Europe/Berlin")

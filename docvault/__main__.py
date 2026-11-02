"""Start the app: python -m docvault [data.db] [--port 8042]"""
import sys

from .fieldkit import main_cli
from .web import create_app

sys.exit(main_cli(create_app, "docvault.db", 8042, sys.argv))

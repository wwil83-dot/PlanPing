"""Called by GitHub Actions to dispatch alert emails."""
import asyncio
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))
from app.alerts import dispatch_alerts

asyncio.run(dispatch_alerts())

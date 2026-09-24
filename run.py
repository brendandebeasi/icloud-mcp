#!/usr/bin/env python3
"""Entry point for the iCloud MCP server (thin wrapper around ``icloud_mcp.server:main``).

    python run.py                 # stdio transport (Claude Desktop / Claude Code)
    python run.py --http          # Streamable HTTP on 0.0.0.0:8000/mcp
    python run.py --help
"""

import os
import sys

# Allow running from a checkout without installing the package.
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))

from icloud_mcp.server import main  # noqa: E402

if __name__ == "__main__":
    main()

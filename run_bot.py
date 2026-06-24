#!/usr/bin/env python3
import os, sys, asyncio
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))
from bot.main import main
if __name__ == "__main__":
    asyncio.run(main())

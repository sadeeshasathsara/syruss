import asyncio
import sys

if len(sys.argv) > 1:
    from mail.cli import main as cli
    sys.exit(cli())
else:
    from mail.worker import main
    asyncio.run(main())

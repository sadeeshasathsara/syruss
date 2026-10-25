import sys
from pathlib import Path

# Ensure project root and server root are in sys.path
server_root = Path(__file__).resolve().parent
project_root = server_root.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))
if str(server_root) not in sys.path:
    sys.path.insert(0, str(server_root))

import uvicorn

if __name__ == "__main__":
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)

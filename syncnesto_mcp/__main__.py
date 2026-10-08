"""python -m syncnesto_mcpでloopbackだけに公開する。"""

import argparse
from urllib.parse import urlsplit

import uvicorn

from syncnesto_mcp.server import create_server


def main() -> None:
    """URLだけを受け取り、資格情報はCodexのOAuth保存に任せる。"""
    parser = argparse.ArgumentParser(description="Syncnesto local OAuth MCP")
    parser.add_argument("--api-url", default="http://127.0.0.1:8000")
    parser.add_argument("--resource-url", default="http://127.0.0.1:8765/mcp")
    args = parser.parse_args()
    server = create_server(args.api_url, args.resource_url)
    port = urlsplit(args.resource_url).port
    uvicorn.run(server, host="127.0.0.1", port=port or 8765, access_log=False)


if __name__ == "__main__":
    main()

import argparse
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description="QuantStone 回测工作台")
    parser.add_argument(
        "--host",
        default="0.0.0.0",
        help="监听地址；默认 0.0.0.0，允许局域网访问",
    )
    parser.add_argument("--port", type=int, default=8010)
    parser.add_argument("--runs-dir", type=Path, default=Path("var/runs"))
    parser.add_argument(
        "--allowed-host",
        action="append",
        default=[],
        help="额外允许的访问主机名或 IP；可以重复指定",
    )
    args = parser.parse_args()
    import uvicorn
    from .api import create_app
    uvicorn.run(
        create_app(args.runs_dir, allowed_hosts=args.allowed_host),
        host=args.host,
        port=args.port,
    )


if __name__ == "__main__":
    main()

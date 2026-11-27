import argparse
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description="QuantStone 本地回测工作台")
    parser.add_argument("--port", type=int, default=8010)
    parser.add_argument("--runs-dir", type=Path, default=Path("var/runs"))
    args = parser.parse_args()
    import uvicorn
    from .api import create_app
    uvicorn.run(create_app(args.runs_dir), host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()

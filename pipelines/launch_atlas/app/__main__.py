"""Run locally with uv run --group app python -m launch_atlas.app."""

import argparse

from launch_atlas.app.main import create_app


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the local Printed Atlas Dash app")
    parser.add_argument("--port", type=int, default=8050)
    args = parser.parse_args()
    create_app().run(host="127.0.0.1", port=args.port, debug=False)


if __name__ == "__main__":
    main()

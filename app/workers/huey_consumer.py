"""Separate Huey consumer entry point."""

from huey.bin.huey_consumer import consumer_main
import sys


def main() -> None:
    if len(sys.argv) == 1:
        sys.argv.append("app.infrastructure.background.tasks.huey")
    consumer_main()


if __name__ == "__main__":
    main()

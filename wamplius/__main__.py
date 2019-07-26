def main() -> None:
    try:
        import wamplius.cli
    except ImportError:
        import pathlib
        import sys
        sys.path.append(str((pathlib.Path(__file__) / "../..").resolve()))

        import wamplius.cli

    wamplius.cli.main()


if __name__ == "__main__":
    main()

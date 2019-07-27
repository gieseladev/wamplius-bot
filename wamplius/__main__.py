def main() -> None:
    try:
        import wamplius.cli
    except ImportError:
        import sys
        from os import path

        sys.path.append(path.abspath(path.join(__file__, "../..")))

        import wamplius.cli

    wamplius.cli.main()


if __name__ == "__main__":
    main()

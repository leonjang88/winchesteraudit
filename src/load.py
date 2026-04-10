import argparse
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pdfplumber


def cmd_profile(args) -> None:
    """Profile a PDF: iterate every page with pdfplumber.
    For each page:
      - If tables detected: print page number + column headers found
      - If text content (no tables or mixed): print page number + raw text excerpt
    Uses pdfplumber.open(args.pdf) to iterate pages."""
    with pdfplumber.open(args.pdf) as pdf:
        for page in pdf.pages:
            try:
                tables = page.extract_tables()
            except (KeyError, TypeError, ValueError):
                tables = []
            if tables:
                for table in tables:
                    headers = table[0] if table else []
                    print(f"Page {page.page_number} [table]: {headers}")
            else:
                text = page.extract_text() or ""
                excerpt = text[:200].replace("\n", " ")
                print(f"Page {page.page_number} [text]: {excerpt}")


def cmd_extract(args) -> None:
    """Extract budget data from PDF into DB and output files."""
    from extract import run_extract
    run_extract(args.town, args.pdf)


def main() -> int:
    """argparse with subparsers.
    profile subcommand requires: --town (str), --pdf (str, path to PDF file)
    Validates PDF exists before processing (AC3: FileNotFoundError → print error with path, exit 1).
    Missing args → argparse prints usage automatically (AC4)."""
    parser = argparse.ArgumentParser(
        description="Winchester budget audit pipeline"
    )
    subparsers = parser.add_subparsers(dest="command")

    profile_parser = subparsers.add_parser(
        "profile", help="Profile a budget PDF to inspect its structure"
    )
    profile_parser.add_argument("--town", required=True, help="Town name")
    profile_parser.add_argument("--pdf", required=True, help="Path to PDF file")

    extract_parser = subparsers.add_parser(
        "extract", help="Extract budget data from PDF into database"
    )
    extract_parser.add_argument("--town", required=True)
    extract_parser.add_argument("--pdf", required=True)
    extract_parser.set_defaults(func=cmd_extract)

    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        return 1

    if args.command == "profile":
        if not os.path.exists(args.pdf):
            print(f"Error: PDF not found: {args.pdf}")
            return 1
        cmd_profile(args)

    elif args.command == "extract":
        if not os.path.exists(args.pdf):
            print(f"Error: PDF not found: {args.pdf}")
            return 1
        cmd_extract(args)

    return 0


if __name__ == "__main__":
    sys.exit(main())

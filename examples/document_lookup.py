"""Run document lookup with Cloudflare using synthetic/de-identified files only."""

import argparse
import json

from icdjev import ICDLookup


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalogue", required=True, help="User-supplied WHO ClaML XML")
    parser.add_argument("--model", choices=["clef", "clef-flash"], default="clef")
    parser.add_argument("--edge", type=int, default=2048)
    parser.add_argument("--no-compress", action="store_true")
    parser.add_argument("files", nargs="+", help="Local images, PDFs or Word documents")
    args = parser.parse_args()
    with ICDLookup(catalogue_path=args.catalogue, backend=args.model) as lookup:
        report = lookup.get_icd_from_documents(
            args.files, target_longest_edge=args.edge, compress=not args.no_compress
        )
        print(json.dumps(report.to_dict(), indent=2))


if __name__ == "__main__":
    main()

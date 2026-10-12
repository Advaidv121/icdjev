"""Inspect a catalogue offline: python examples/local_catalogue.py --file who.xml E11.9"""

import argparse

from icdjev import Catalogue

parser = argparse.ArgumentParser(description="Inspect a WHO ICD-10 code without hosted inference.")
parser.add_argument("--file", required=True, help="Path to your WHO ICD-10 ClaML file")
parser.add_argument("code", help="ICD-10 code to inspect")
args = parser.parse_args()
catalogue = Catalogue.load(args.file)
node = catalogue.get(args.code)
print(node.code, node.full_label)
print("Validation:", catalogue.validate(args.code))
print("Path:", " > ".join(n.code for n in catalogue.path(args.code)))

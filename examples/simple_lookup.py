"""Run: TYPESAFE_API_KEY=... python examples/simple_lookup.py --file icd102019en.xml "note text" """

import argparse
import json

from icdjev import ICDLookup

p = argparse.ArgumentParser()
p.add_argument("--file", required=True)
p.add_argument("note")
a = p.parse_args()
with ICDLookup(catalogue_path=a.file) as icd:
    print(json.dumps(icd.to_dicts(icd.get_icd(a.note)), indent=2))

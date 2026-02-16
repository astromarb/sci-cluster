import sys
from pathlib import Path

# ensure package import works when running from tools/
repo_root = Path(__file__).resolve().parents[1]
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

import argparse
import pandas as pd
from sci_helpers import wrangle_dataframe

parser = argparse.ArgumentParser(description='Run MC wrangler on CSV/Excel and print summary')
parser.add_argument('--write-out', '-w', help='Optional output path: directory for CSVs or path ending with .xlsx to write one workbook', default=None)
args = parser.parse_args()

csv_path = repo_root / 'sci-data' / 'Aplites_XRF_data' / 'HAL_XRF_WholeRock_andUncertanties.csv'
print('Using CSV:', csv_path)

# read header=None so parser sees all cells
df = pd.read_csv(csv_path, header=None)
print('DataFrame shape:', df.shape)

results = wrangle_dataframe(df, blank_row_tolerance=50, write_out=args.write_out)
print('\nParsed datasets count:', len(results))
for i, ds in enumerate(results, start=1):
    print(f'\n=== Dataset {i} ===')
    print('anchor:', ds.get('anchor'))
    print('oxides_order:', ds.get('oxides_order'))
    print('num samples:', len(ds.get('samples_list', [])))
    if ds.get('samples_list'):
        print('first sample:', ds['samples_list'][0])
    print('notes:')
    for note in ds.get('notes', []):
        print(' -', note)
    print('DataFrame preview:')
    print(ds['df'].head().to_string())

print('\nDone')

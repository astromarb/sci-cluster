# sci_helpers

Lightweight helpers for the sci-cluster project.

Installable or importable directly from the repository in notebooks.

Usage (notebook):

```python
import sys, os
repo_root = os.path.dirname(os.path.dirname(__file__))
package_dir = os.path.join(repo_root, 'sci_helpers')
if package_dir not in sys.path:
    sys.path.insert(0, package_dir)

from sci_helpers import csv_to_samples_list

# then call the function
s_list, ox_order, df_num = csv_to_samples_list('myfile.csv', filepath='/path/to')
```

## Notes for `compare_wrangled`

The comparator supports finding wrangled output files anywhere under your `sci-data` directory. You can pass:

- a basename (e.g. `wrangled_dataset_1.csv`),
- a relative path inside `sci-data` (e.g. `Aplites_XRF_data/wrangled_dataset_1.csv`), or
- an absolute path.

When using Windows backslashes in Python source, prefer raw string literals (r"C:\path\to\sci-data") to avoid escape sequence warnings.

Recommended imports:

```python
# import the simple print-only comparator directly
from sci_helpers.compare_wrangled import compare_wrangled

# or import the programmatic comparator that returns (ok, mismatches)
from sci_helpers.compare_wrangled import compare_wrangled_detailed
```

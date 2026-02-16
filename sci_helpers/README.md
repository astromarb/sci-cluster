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

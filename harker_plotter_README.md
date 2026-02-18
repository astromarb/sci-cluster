# Harker Plotter Quick Usage

```python
from sci_helpers.harker_diagrams import plot_harker_diagrams

fig, axes, clean = plot_harker_diagrams(
    df=my_df,
    sample_id_col="SampleID",
    group_col="Lithology",
    trendline="both",
    save_path="outputs/harker/2026-02-18/harker.png",
)
```

#!/bin/bash
# Auto-load conda environment (base)

if [ -f "$HOME/miniconda3/etc/profile.d/conda.sh" ]; then
    source "$HOME/miniconda3/etc/profile.d/conda.sh"
    conda activate base
else
    echo "Conda initialization script not found" >&2
fi

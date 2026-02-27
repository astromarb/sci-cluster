# Setup

## Base Conda
- Conda executable: `/Users/lopezama/miniconda3/condabin/conda`
- Base interpreter: `/Users/lopezama/miniconda3/bin/python`

## Project Environment (Python 3.10)
```bash
/Users/lopezama/miniconda3/bin/conda create -y -n sci-cluster-py310 python=3.10
/Users/lopezama/miniconda3/bin/conda activate sci-cluster-py310
```

## Recreate From Spec
```bash
/Users/lopezama/miniconda3/bin/conda env create -f sci-cluster-py310.yml
```

## Interpreter For IDE
- Use: `/Users/lopezama/miniconda3/envs/sci-cluster-py310/bin/python`

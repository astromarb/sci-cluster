$Env:CONDA_EXE = "/home/ec2-user/miniconda3/bin/conda"
$Env:_CE_M = $null
$Env:_CE_CONDA = $null
$Env:_CONDA_ROOT = "/home/ec2-user/miniconda3"
$Env:_CONDA_EXE = "/home/ec2-user/miniconda3/bin/conda"
$CondaModuleArgs = @{ChangePs1 = $True}
Import-Module "$Env:_CONDA_ROOT\shell\condabin\Conda.psm1" -ArgumentList $CondaModuleArgs

Remove-Variable CondaModuleArgs
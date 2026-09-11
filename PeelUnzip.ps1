$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$app = Join-Path $root 'shrink_unzip_app.py'
$python = Get-Command python.exe -ErrorAction SilentlyContinue
if (-not $python) { $python = Get-Command py.exe -ErrorAction SilentlyContinue }
if (-not $python) { Add-Type -AssemblyName PresentationFramework; [System.Windows.MessageBox]::Show('Python 3 is required to run PeelUnzip.','PeelUnzip'); exit 1 }
& $python.Source $app

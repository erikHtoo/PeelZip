$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$app = Join-Path $root 'shrink_unzip_app.py'
$launcher = Get-Command py.exe -ErrorAction SilentlyContinue
if ($launcher) { & $launcher.Source -3 $app; exit $LASTEXITCODE }
$launcher = Get-Command python.exe -ErrorAction SilentlyContinue
if (-not $launcher) { Add-Type -AssemblyName PresentationFramework; [System.Windows.MessageBox]::Show('Python 3 is required to run PeelZip.','PeelZip'); exit 1 }
& $launcher.Source $app


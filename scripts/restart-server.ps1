param([int]$Port=8000)
$ErrorActionPreference='Stop'

# Kill any existing uvicorn app workers for this project route first.
$uvicornProcs = Get-CimInstance Win32_Process |
  Where-Object {
    $_.Name -eq 'python.exe' -and
    $_.CommandLine -match 'uvicorn' -and
    $_.CommandLine -match 'api\.main:app'
  }
foreach($proc in $uvicornProcs){
  try { Stop-Process -Id $proc.ProcessId -Force -ErrorAction Stop } catch {}
}

# Also kill any process currently listening on the target port.
$owners = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
  Select-Object -ExpandProperty OwningProcess -Unique
foreach($id in $owners){
  try { Stop-Process -Id $id -Force -ErrorAction Stop } catch {}
}

Start-Sleep -Milliseconds 900

$pythonPath = (Resolve-Path ".\.venv\Scripts\python.exe").Path
$args = "-m uvicorn api.main:app --host 0.0.0.0 --port $Port"
$p = Start-Process -FilePath $pythonPath -ArgumentList $args -WorkingDirectory (Get-Location) -WindowStyle Hidden -PassThru

Start-Sleep -Seconds 2

$live = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
  Select-Object -ExpandProperty OwningProcess -Unique
if(-not $live){
  throw "No listener started on port $Port."
}
if($live.Count -gt 1){
  throw "Multiple listeners detected on port ${Port}: $($live -join ',')."
}

$ownerPid = $live[0]
$owner = Get-CimInstance Win32_Process -Filter "ProcessId = $ownerPid"
$ownerPath = $owner.ExecutablePath
if(-not $ownerPath){
  throw "Could not resolve listener executable path for PID $ownerPid."
}
if($ownerPath -ne $pythonPath){
  throw "Wrong Python bound to port ${Port}. Expected '$pythonPath' but got '$ownerPath'."
}

Write-Output ("started_pid="+$p.Id)
Write-Output ("listener_pid="+$ownerPid)
Write-Output ("listener_path="+$ownerPath)

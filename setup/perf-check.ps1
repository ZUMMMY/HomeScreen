# Measures what Madison Desktop costs your PC: CPU, memory and GPU of its Python scripts
# (and of Lively, if you use it), sampled every few seconds.
#
#   powershell -ExecutionPolicy Bypass -File setup\perf-check.ps1                 2 minutes
#   powershell -ExecutionPolicy Bypass -File setup\perf-check.ps1 -Minutes 10
#
# To see race mode, start a replay first: .venv\Scripts\python live.py --replay latest --from-lap 20

param([double]$Minutes = 2, [int]$Every = 5)

$cores = [Environment]::ProcessorCount

function Get-WallpaperProcesses {
    $all = Get-CimInstance Win32_Process -Property ProcessId, ParentProcessId, Name, CommandLine
    $byParent = $all | Group-Object ParentProcessId -AsHashTable -AsString
    $picked = @{}
    $queue = [System.Collections.Queue]::new()
    foreach ($p in $all) {
        $isLively = $p.Name -like "Lively*"
        $isOurPython = $p.Name -match "^pythonw?\.exe$" -and $p.CommandLine -match "(fetch|live)\.py"
        if ($isLively -or $isOurPython) { $queue.Enqueue($p) }
    }
    # Lively draws web wallpapers in child browser (WebView2) processes; count those too.
    while ($queue.Count) {
        $p = $queue.Dequeue()
        if ($picked.ContainsKey($p.ProcessId)) { continue }
        $picked[$p.ProcessId] = $p
        $kids = $byParent["$($p.ProcessId)"]
        if ($kids) { foreach ($k in $kids) { $queue.Enqueue($k) } }
    }
    $picked.Values
}

function Get-GpuByPid {
    $gpu = @{}
    try {
        $samples = (Get-Counter "\GPU Engine(*engtype_3D)\Utilization Percentage" -ErrorAction Stop).CounterSamples
        foreach ($s in $samples) {
            if ($s.InstanceName -match "pid_(\d+)_") {
                $id = [int]$Matches[1]
                $gpu[$id] = ($gpu[$id] + $s.CookedValue)
            }
        }
    } catch { }  # GPU counters need Windows 10 1709+ and English counter names
    $gpu
}

function Label($p) {
    if ($p.Name -match "^pythonw?\.exe$") {
        if ($p.CommandLine -match "live\.py") { return "live.py (race mode)" }
        if ($p.CommandLine -match "fetch\.py") { return "fetch.py (refresh)" }
    }
    if ($p.Name -like "msedgewebview2*") { return "Lively page (WebView2)" }
    return $p.Name -replace "\.exe$", ""
}

$end = (Get-Date).AddMinutes($Minutes)
$prev = @{}
$prevTime = Get-Date
$rows = @()
Write-Host "Sampling for $Minutes min every $Every s on $cores logical cores. Use the PC normally meanwhile..."

while ((Get-Date) -lt $end) {
    Start-Sleep -Seconds $Every
    $now = Get-Date
    $elapsed = ($now - $prevTime).TotalSeconds
    $prevTime = $now
    $gpu = Get-GpuByPid
    $tick = @{}
    foreach ($w in Get-WallpaperProcesses) {
        $proc = Get-Process -Id $w.ProcessId -ErrorAction SilentlyContinue
        if (-not $proc) { continue }
        $cpu = $proc.TotalProcessorTime.TotalSeconds
        $label = Label $w
        if (-not $tick[$label]) { $tick[$label] = @{ Cpu = 0.0; Mem = 0.0; Gpu = 0.0 } }
        if ($prev.ContainsKey($w.ProcessId)) {
            $tick[$label].Cpu += 100 * ($cpu - $prev[$w.ProcessId]) / ($elapsed * $cores)
        }
        $prev[$w.ProcessId] = $cpu
        $tick[$label].Mem += $proc.WorkingSet64 / 1MB
        $tick[$label].Gpu += [double]($gpu[$w.ProcessId])
    }
    foreach ($k in $tick.Keys) {
        $rows += [pscustomobject]@{ Part = $k; Cpu = $tick[$k].Cpu; Mem = $tick[$k].Mem; Gpu = $tick[$k].Gpu }
    }
    $total = 0.0
    foreach ($t in $tick.Values) { $total += $t.Cpu }
    Write-Host ("{0:HH:mm:ss}  wallpaper CPU {1,6:N2}%  ({2} parts running)" -f $now, $total, $tick.Count)
}

if (-not $rows) {
    Write-Host "`nNothing found. Is race mode or a replay running? (The wallpaper refresh only runs for a few seconds every 15 minutes.)"
    exit 1
}

Write-Host "`nAverages (CPU % of the whole PC, GPU % of the 3D engine):"
$rows | Group-Object Part | ForEach-Object {
    [pscustomobject]@{
        Part      = $_.Name
        "CPU avg" = "{0:N2}%" -f ($_.Group | Measure-Object Cpu -Average).Average
        "CPU max" = "{0:N2}%" -f ($_.Group | Measure-Object Cpu -Maximum).Maximum
        "GPU avg" = "{0:N1}%" -f ($_.Group | Measure-Object Gpu -Average).Average
        "Memory"  = "{0:N0} MB" -f ($_.Group | Measure-Object Mem -Average).Average
    }
} | Format-Table -AutoSize

$cpuAvg = (($rows | Group-Object { $_.Part } | ForEach-Object { ($_.Group | Measure-Object Cpu -Average).Average }) | Measure-Object -Sum).Sum
$verdict = if ($cpuAvg -lt 3) { "Light: you won't notice it." }
           elseif ($cpuAvg -lt 8) { "Moderate: fine on a desktop." }
           else { "Heavy: check cache\live.log for anything looping." }
Write-Host ("Total wallpaper CPU: {0:N2}% average. {1}" -f $cpuAvg, $verdict)

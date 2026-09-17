#Requires -Version 5.1
<#
    launch-guard.ps1 —— 防重复启动器（后端 8001 + 前端 5174）

    做的事：
      1) 先查后端：有没有 python 在跑本项目的 main.py，端口 8001 有没有被占用
      2) 再查前端：有没有 vite 在跑，端口 5174 有没有被占用
      3) 已经在跑的【不再启动第二个】，缺哪个补哪个
      4) 等端口就绪后自动打开浏览器页面（默认 http://localhost:5174）

    为什么需要它：Agent/AI 启动过一次时你这边看不到窗口，容易再手动启动一次，
    结果同一个小助手跑了两三份，每个吃 1.5-2.4GB 内存，最后把整机内存顶爆。
    用这个入口启动，重复点击也只会有一个实例。

    用法：
      powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\launch-guard.ps1
      powershell.exe ... -NoOpen            # 不自动开浏览器
      powershell.exe ... -BackendPort 8001 -FrontendPort 5174
#>
[CmdletBinding()]
param(
    [int]$BackendPort  = 8001,
    [int]$FrontendPort = 5174,
    [string]$OpenUrl   = 'http://localhost:5174',
    [int]$WaitSeconds  = 30,
    [switch]$NoOpen
)

$ErrorActionPreference = 'SilentlyContinue'

$root        = Split-Path -Parent $MyInvocation.MyCommand.Path
$backendDir  = Join-Path $root 'backend'
$frontendDir = Join-Path $root 'frontend'
$venvPython  = Join-Path $root '.venv\Scripts\python.exe'

function Write-Head {
    param([string]$Text)
    Write-Host ''
    Write-Host ('=== ' + $Text + ' ===')
}

function Test-PortBusy {
    param([int]$Port)
    $listen = Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue
    if ($listen) { return $true }
    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $client.Connect('127.0.0.1', $Port)
        $client.Close()
        return $true
    }
    catch {
        $client.Close()
        return $false
    }
}

function Get-ProjectBackendProcs {
    Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='pythonw.exe'" | Where-Object {
        $_.CommandLine -and
        $_.CommandLine -like '*main.py*' -and
        (($_.CommandLine -like ('*' + $root + '*')) -or ($_.ExecutablePath -and $_.ExecutablePath -like ('*' + $root + '*')))
    }
}

function Get-ProjectFrontendProcs {
    Get-CimInstance Win32_Process -Filter "Name='node.exe'" | Where-Object {
        $_.CommandLine -and
        $_.CommandLine -like '*vite*' -and
        (($_.CommandLine -like ('*' + $root + '*')) -or ($_.ExecutablePath -and $_.ExecutablePath -like ('*' + $root + '*')))
    }
}

function Start-Backend {
    if (-not (Test-Path -LiteralPath $venvPython)) {
        Write-Host '[警告] 找不到虚拟环境 python：' $venvPython
        Write-Host '       请先在本项目里建好 .venv（或改用原来的 start.bat）。'
        return $false
    }
    Start-Process -FilePath 'cmd.exe' -ArgumentList '/k', '..\.venv\Scripts\python.exe main.py' -WorkingDirectory $backendDir
    Write-Host '[启动] 后端：python main.py（新窗口，端口 ' $BackendPort '）'
    return $true
}

function Start-Frontend {
    Start-Process -FilePath 'cmd.exe' -ArgumentList '/k', 'npm run dev' -WorkingDirectory $frontendDir
    Write-Host '[启动] 前端：npm run dev（新窗口，端口 ' $FrontendPort '）'
    return $true
}

Write-Host '============================================================'
Write-Host ' 无畏契约智能对话小助手 - 防重复启动器'
Write-Host (' 项目目录：' + $root)
Write-Host '============================================================'

# ---------- 1. 体检 ----------
$backendProcs  = @(Get-ProjectBackendProcs)
$frontendProcs = @(Get-ProjectFrontendProcs)
$backendPortBusy  = Test-PortBusy -Port $BackendPort
$frontendPortBusy = Test-PortBusy -Port $FrontendPort

$backendRunning  = ($backendProcs.Count -gt 0) -or $backendPortBusy
$frontendRunning = ($frontendProcs.Count -gt 0) -or $frontendPortBusy

Write-Head '当前状态'
if ($backendProcs.Count -gt 0) {
    foreach ($bp in $backendProcs) {
        $mb = [math]::Round($bp.PrivatePageCount / 1MB, 0)
        Write-Host ('  后端：已在运行  PID=' + $bp.ProcessId + '  内存≈' + $mb + 'MB')
    }
}
elseif ($backendPortBusy) {
    Write-Host ('  后端：端口 ' + $BackendPort + ' 被占用（没看到本项目的 python，可能是别的程序或残留进程）')
}
else {
    Write-Host '  后端：未运行'
}

if ($frontendProcs.Count -gt 0) {
    Write-Host ('  前端：已在运行  PID=' + ($frontendProcs | ForEach-Object { $_.ProcessId }) -join ',')
}
elseif ($frontendPortBusy) {
    Write-Host ('  前端：端口 ' + $FrontendPort + ' 被占用（可能是别的程序）')
}
else {
    Write-Host '  前端：未运行'
}

# ---------- 2. 只补缺的 ----------
$startedSomething = $false
Write-Head '处理'

if ($backendRunning -and $frontendRunning) {
    Write-Host '  后端、前端都已经在跑了 —— 这次不启动任何东西，免得重复吃内存。'
}
else {
    if ($backendRunning) {
        Write-Host '  后端已在跑，跳过。'
    }
    else {
        if (Start-Backend) { $startedSomething = $true }
    }
    if ($frontendRunning) {
        Write-Host '  前端已在跑，跳过。'
    }
    else {
        if (Start-Frontend) { $startedSomething = $true }
    }
}

# ---------- 3. 等端口就绪 ----------
if ($startedSomething) {
    Write-Head '等待服务就绪'
    $deadline = (Get-Date).AddSeconds($WaitSeconds)
    while ((Get-Date) -lt $deadline) {
        $bOk = Test-PortBusy -Port $BackendPort
        $fOk = Test-PortBusy -Port $FrontendPort
        Write-Host ('  ' + (Get-Date -Format 'HH:mm:ss') + '  后端 ' + $(if ($bOk) { '就绪' } else { '启动中...' }) + ' / 前端 ' + $(if ($fOk) { '就绪' } else { '启动中...' }))
        if ($bOk -and $fOk) { break }
        Start-Sleep -Seconds 2
    }
    $backendPortBusy  = Test-PortBusy -Port $BackendPort
    $frontendPortBusy = Test-PortBusy -Port $FrontendPort
    Write-Host ('  后端端口 ' + $BackendPort + '：' + $(if ($backendPortBusy) { 'OK' } else { '还没起来，去看新开的窗口报错信息' }))
    Write-Host ('  前端端口 ' + $FrontendPort + '：' + $(if ($frontendPortBusy) { 'OK' } else { '还没起来，去看新开的窗口报错信息' }))
}

# ---------- 4. 打开页面 ----------
Write-Head '结果'
if ($frontendPortBusy) {
    if ($NoOpen) {
        Write-Host ('  页面地址（自己点开）：' + $OpenUrl)
    }
    else {
        Write-Host ('  已为你打开页面：' + $OpenUrl)
        Start-Process $OpenUrl
    }
}
else {
    Write-Host '  前端还没就绪，暂时没有打开页面。'
}
Write-Host ''
Write-Host '  （本窗口可以直接关掉，服务在各自的新窗口里继续跑）'

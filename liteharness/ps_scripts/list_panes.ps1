$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
Add-Type -AssemblyName System.Management

function Get-Panes([int]$handle) {
  $window = [System.Windows.Automation.AutomationElement]::FromHandle([IntPtr]$handle)
  if (-not $window) {
    return @()
  }

  $descendants = $window.FindAll(
    [System.Windows.Automation.TreeScope]::Descendants,
    [System.Windows.Automation.Condition]::TrueCondition
  )

  $panes = @()
  $paneId = 0
  foreach ($child in $descendants) {
    try {
      $current = $child.Current
      $className = [string]$current.ClassName
      $controlType = $current.ControlType.ProgrammaticName
      if ($className -like '*TermControl*') {
        $rect = $current.BoundingRectangle
        $panes += [pscustomobject]@{
          id = $paneId
          title = [string]$current.Name
          class_name = $className
          control_type = $controlType
          automation_id = [string]$current.AutomationId
          focused = [bool]$current.HasKeyboardFocus
          rect = if ($rect) {
            [pscustomobject]@{
              left = [int]$rect.Left
              top = [int]$rect.Top
              right = [int]$rect.Right
              bottom = [int]$rect.Bottom
            }
          } else {
            $null
          }
          has_text_pattern = $className -like '*TermControl*'
        }
        $paneId += 1
      }
    } catch {
    }
  }

  return $panes
}

$root = [System.Windows.Automation.AutomationElement]::RootElement
$condition = New-Object System.Windows.Automation.PropertyCondition(
  [System.Windows.Automation.AutomationElement]::ClassNameProperty,
  'CASCADIA_HOSTING_WINDOW_CLASS'
)
$windows = $root.FindAll([System.Windows.Automation.TreeScope]::Children, $condition)

$processRows = Get-CimInstance Win32_Process | Select-Object ProcessId, ParentProcessId, Name, CommandLine, CreationDate
$childrenByParent = @{}
foreach ($row in $processRows) {
  $parentId = [int]$row.ParentProcessId
  if (-not $childrenByParent.ContainsKey($parentId)) {
    $childrenByParent[$parentId] = New-Object System.Collections.ArrayList
  }
  [void]$childrenByParent[$parentId].Add($row)
}

$runtimeMap = @{}
foreach ($proc in Get-Process -ErrorAction SilentlyContinue) {
  $startTime = $null
  try {
    $startTime = $proc.StartTime.ToUniversalTime().ToString('o')
  } catch {
  }

  $runtimeMap[[int]$proc.Id] = [pscustomobject]@{
    id = [int]$proc.Id
    cpu = if ($null -ne $proc.CPU) { [double]$proc.CPU } else { 0.0 }
    memory = if ($null -ne $proc.WorkingSet64) { [double]$proc.WorkingSet64 } else { 0.0 }
    start = $startTime
  }
}

$shellNames = @(
  'powershell.exe', 'pwsh.exe', 'cmd.exe', 'bash.exe',
  'wsl.exe', 'zsh.exe', 'fish.exe', 'nu.exe', 'claude.exe'
)

function Get-DescendantProcesses([int]$processId) {
  $result = @()
  $queue = New-Object 'System.Collections.Generic.Queue[object]'
  if ($childrenByParent.ContainsKey($processId)) {
    foreach ($child in $childrenByParent[$processId]) {
      $queue.Enqueue($child)
    }
  }

  while ($queue.Count -gt 0) {
    $item = $queue.Dequeue()
    $result += $item
    $itemPid = [int]$item.ProcessId
    if ($childrenByParent.ContainsKey($itemPid)) {
      foreach ($child in $childrenByParent[$itemPid]) {
        $queue.Enqueue($child)
      }
    }
  }

  return $result
}

$output = @{ windows = @() }
foreach ($window in $windows) {
  $current = $window.Current
  $windowHandle = [int]$current.NativeWindowHandle
  $windowPid = [int]$current.ProcessId
  $shells = @()

  foreach ($proc in (Get-DescendantProcesses $windowPid)) {
    $name = [string]$proc.Name
    if ($shellNames -contains $name.ToLowerInvariant()) {
      $runtime = $runtimeMap[[int]$proc.ProcessId]
      $shells += [pscustomobject]@{
        pid = [int]$proc.ProcessId
        name = $name
        cmdline = if ($null -ne $proc.CommandLine) { [string]$proc.CommandLine } else { '' }
        cwd = ''
        cpu_seconds = if ($runtime) { [double]$runtime.cpu } else { 0.0 }
        memory_bytes = if ($runtime) { [double]$runtime.memory } else { 0.0 }
        create_time = if ($runtime -and $runtime.start) {
          $runtime.start
        } elseif ($proc.CreationDate) {
          try {
            [System.Management.ManagementDateTimeConverter]::ToDateTime($proc.CreationDate).ToUniversalTime().ToString('o')
          } catch {
            $null
          }
        } else {
          $null
        }
      }
    }
  }

  $output.windows += [pscustomobject]@{
    handle = $windowHandle
    title = [string]$current.Name
    pid = $windowPid
    panes = Get-Panes $windowHandle
    shells = $shells
  }
}

$output | ConvertTo-Json -Depth 8 -Compress

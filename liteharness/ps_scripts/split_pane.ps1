$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
Add-Type -AssemblyName System.Windows.Forms

$argsData = if ($env:LWTT_ARGS) { $env:LWTT_ARGS | ConvertFrom-Json } else { $null }
$windowHandle = [int]$argsData.windowHandle
$paneId = [int]$argsData.paneId
$direction = if ($argsData.direction) { [string]$argsData.direction } else { 'vertical' }

function Get-PaneCount([int]$handle) {
  $window = [System.Windows.Automation.AutomationElement]::FromHandle([IntPtr]$handle)
  if (-not $window) {
    return 0
  }
  $descendants = $window.FindAll(
    [System.Windows.Automation.TreeScope]::Descendants,
    [System.Windows.Automation.Condition]::TrueCondition
  )
  $count = 0
  foreach ($child in $descendants) {
    try {
      $current = $child.Current
      $className = [string]$current.ClassName
      $controlType = $current.ControlType.ProgrammaticName
      if ($className -like '*TermControl*') {
        $count += 1
      }
    } catch {
    }
  }
  return $count
}

function Get-TargetElement([int]$handle, [int]$targetPaneId) {
  $window = [System.Windows.Automation.AutomationElement]::FromHandle([IntPtr]$handle)
  if (-not $window) {
    return $null
  }
  $descendants = $window.FindAll(
    [System.Windows.Automation.TreeScope]::Descendants,
    [System.Windows.Automation.Condition]::TrueCondition
  )
  $currentPaneId = 0
  foreach ($child in $descendants) {
    try {
      $current = $child.Current
      $className = [string]$current.ClassName
      $controlType = $current.ControlType.ProgrammaticName
      if ($className -like '*TermControl*') {
        if ($currentPaneId -eq $targetPaneId) {
          return $child
        }
        $currentPaneId += 1
      }
    } catch {
    }
  }
  return $null
}

$beforeCount = Get-PaneCount $windowHandle
$target = Get-TargetElement $windowHandle $paneId
if (-not $target) {
  $false | ConvertTo-Json -Compress
  exit 0
}

try {
  $target.SetFocus()
  Start-Sleep -Milliseconds 80
  if ($direction -eq 'horizontal') {
    [System.Windows.Forms.SendKeys]::SendWait('%+{-}')
  } else {
    [System.Windows.Forms.SendKeys]::SendWait('%+{=}')
  }

  Start-Sleep -Milliseconds 250
  $afterCount = Get-PaneCount $windowHandle
  ($afterCount -ge $beforeCount) | ConvertTo-Json -Compress
} catch {
  $false | ConvertTo-Json -Compress
}

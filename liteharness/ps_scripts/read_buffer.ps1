$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes

$argsData = if ($env:LWTT_ARGS) { $env:LWTT_ARGS | ConvertFrom-Json } else { $null }
$windowHandle = [int]$argsData.windowHandle
$paneId = [int]$argsData.paneId

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

$target = Get-TargetElement $windowHandle $paneId
if (-not $target) {
  @{ buffer_b64 = $null } | ConvertTo-Json -Compress
  exit 0
}

$valuePattern = $null
$textPattern = $null
$result = $null
if ($target.TryGetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern, [ref]$valuePattern)) {
  $result = [string]$valuePattern.Current.Value
} elseif ($target.TryGetCurrentPattern([System.Windows.Automation.TextPattern]::Pattern, [ref]$textPattern)) {
  $result = [string]$textPattern.DocumentRange.GetText(-1)
} else {
  try {
    $legacyPattern = $null
    $legacyType = [Type]::GetType('System.Windows.Automation.LegacyIAccessiblePattern, UIAutomationClient')
    if ($legacyType) {
      $patternField = $legacyType.GetField('Pattern', [System.Reflection.BindingFlags]::Public -bor [System.Reflection.BindingFlags]::Static)
      if ($patternField -and $target.TryGetCurrentPattern($patternField.GetValue($null), [ref]$legacyPattern)) {
        $result = [string]$legacyPattern.Current.Name
      }
    }
  } catch {}
  if ($null -eq $result) {
    $result = [string]$target.Current.Name
  }
}

@{
  buffer_b64 = if ($null -ne $result) {
    [System.Convert]::ToBase64String([System.Text.Encoding]::UTF8.GetBytes([string]$result))
  } else {
    $null
  }
} | ConvertTo-Json -Compress

$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
Add-Type -AssemblyName System.Windows.Forms

$argsData = if ($env:LWTT_ARGS) { $env:LWTT_ARGS | ConvertFrom-Json } else { $null }
$windowHandle = [int]$argsData.windowHandle
$paneId = [int]$argsData.paneId
$actionName = [string]$argsData.actionName
$actionArgs = $argsData.args

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
  $false | ConvertTo-Json -Compress
  exit 0
}

try {
  $target.SetFocus()
  Start-Sleep -Milliseconds 80
  [System.Windows.Forms.SendKeys]::SendWait('^+p')
  Start-Sleep -Milliseconds 250
  [System.Windows.Forms.SendKeys]::SendWait($actionName)
  Start-Sleep -Milliseconds 200
  [System.Windows.Forms.SendKeys]::SendWait('{ENTER}')

  if ($actionArgs) {
    Start-Sleep -Milliseconds 300
    foreach ($prop in $actionArgs.PSObject.Properties) {
      if ($prop.Name -eq 'tab_index') {
        continue
      }
      if ($null -ne $prop.Value -and [string]$prop.Value -ne '') {
        [System.Windows.Forms.SendKeys]::SendWait([string]$prop.Value)
        Start-Sleep -Milliseconds 100
        [System.Windows.Forms.SendKeys]::SendWait('{ENTER}')
      }
    }
  }

  $true | ConvertTo-Json -Compress
} catch {
  $false | ConvertTo-Json -Compress
}

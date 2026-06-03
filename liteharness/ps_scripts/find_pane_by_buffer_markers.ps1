$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes

$argsData = if ($env:LWTT_ARGS) { $env:LWTT_ARGS | ConvertFrom-Json } else { $null }
$markers = @()
if ($argsData -and $argsData.markers) {
  foreach ($marker in $argsData.markers) {
    $text = [string]$marker
    if (-not [string]::IsNullOrWhiteSpace($text)) {
      $markers += $text
    }
  }
}
$sampleChars = if ($argsData -and $argsData.sampleChars) { [int]$argsData.sampleChars } else { 8000 }

function Get-TerminalText($element, [int]$maxChars) {
  $valuePattern = $null
  $textPattern = $null
  $result = $null
  if ($element.TryGetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern, [ref]$valuePattern)) {
    $result = [string]$valuePattern.Current.Value
  } elseif ($element.TryGetCurrentPattern([System.Windows.Automation.TextPattern]::Pattern, [ref]$textPattern)) {
    $result = [string]$textPattern.DocumentRange.GetText($maxChars)
  } else {
    try {
      $legacyPattern = $null
      $legacyType = [Type]::GetType('System.Windows.Automation.LegacyIAccessiblePattern, UIAutomationClient')
      if ($legacyType) {
        $patternField = $legacyType.GetField('Pattern', [System.Reflection.BindingFlags]::Public -bor [System.Reflection.BindingFlags]::Static)
        if ($patternField -and $element.TryGetCurrentPattern($patternField.GetValue($null), [ref]$legacyPattern)) {
          $result = [string]$legacyPattern.Current.Name
        }
      }
    } catch {}
    if ($null -eq $result) {
      $result = [string]$element.Current.Name
    }
  }
  if ($null -eq $result) { return '' }
  return [string]$result
}

if ($markers.Count -eq 0) {
  @{ match = $null; candidates = @() } | ConvertTo-Json -Depth 6 -Compress
  exit 0
}

$root = [System.Windows.Automation.AutomationElement]::RootElement
$condition = New-Object System.Windows.Automation.PropertyCondition(
  [System.Windows.Automation.AutomationElement]::ClassNameProperty,
  'CASCADIA_HOSTING_WINDOW_CLASS'
)
$windows = $root.FindAll([System.Windows.Automation.TreeScope]::Children, $condition)

$candidates = @()
$candidateOrder = 0
foreach ($window in $windows) {
  $windowCurrent = $window.Current
  $descendants = $window.FindAll(
    [System.Windows.Automation.TreeScope]::Descendants,
    [System.Windows.Automation.Condition]::TrueCondition
  )

  $paneId = 0
  foreach ($child in $descendants) {
    try {
      $current = $child.Current
      $className = [string]$current.ClassName
      if ($className -notlike '*TermControl*') {
        continue
      }
      $buffer = Get-TerminalText $child $sampleChars
      $score = 0
      $matched = @()
      foreach ($marker in $markers) {
        if ($buffer.IndexOf($marker, [System.StringComparison]::OrdinalIgnoreCase) -ge 0) {
          $score += [Math]::Min(1000, [Math]::Max(10, $marker.Length))
          $matched += $marker
        }
      }
      if ($score -gt 0) {
        $candidates += [pscustomobject]@{
          order = $candidateOrder
          window_handle = [int]$windowCurrent.NativeWindowHandle
          window_title = [string]$windowCurrent.Name
          window_pid = [int]$windowCurrent.ProcessId
          pane_id = $paneId
          pane_title = [string]$current.Name
          class_name = $className
          focused = [bool]$current.HasKeyboardFocus
          score = $score
          matched_markers = $matched
        }
        $candidateOrder += 1
      }
      $paneId += 1
    } catch {
    }
  }
}

$best = $null
if ($candidates.Count -gt 0) {
  $best = $candidates | Sort-Object -Property `
    @{ Expression = 'score'; Descending = $true },
    @{ Expression = 'focused'; Descending = $true },
    @{ Expression = 'order'; Descending = $false } |
    Select-Object -First 1
}

@{
  match = $best
  candidates = $candidates
} | ConvertTo-Json -Depth 8 -Compress

$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
Add-Type -AssemblyName System.Windows.Forms

$argsData = if ($env:LWTT_ARGS) { $env:LWTT_ARGS | ConvertFrom-Json } else { $null }
$windowHandle = [int]$argsData.windowHandle
$paneId = [int]$argsData.paneId
$keys = if ($argsData.keys) { [string]$argsData.keys } else { $null }
$focusOnly = if ($null -ne $argsData.focusOnly) { [bool]$argsData.focusOnly } else { $false }

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
  if (-not $focusOnly -and $keys) {
    # Check if keys contain only SendKeys special sequences (like {ENTER})
    $isSpecialOnly = $keys -match '^\{[A-Z]+\}$'

    if ($isSpecialOnly) {
      # Pure special keys — use SendKeys (e.g. {ENTER}, {TAB}, ^c)
      [System.Windows.Forms.SendKeys]::SendWait($keys)
    } else {
      # Text content — use clipboard paste for speed and atomicity.
      # This prevents race conditions when multiple agents type simultaneously
      # and eliminates the slow keystroke-by-keystroke typewriter effect.

      # Strip trailing {ENTER} — we'll send it separately after paste
      $hasEnter = $keys.EndsWith('{ENTER}')
      $textToPaste = if ($hasEnter) { $keys.Substring(0, $keys.Length - 7) } else { $keys }

      # Save current clipboard, paste text, restore clipboard
      $savedClip = $null
      try { $savedClip = [System.Windows.Forms.Clipboard]::GetText() } catch {}

      [System.Windows.Forms.Clipboard]::SetText($textToPaste)
      Start-Sleep -Milliseconds 30
      [System.Windows.Forms.SendKeys]::SendWait('^v')
      Start-Sleep -Milliseconds 30

      # Restore previous clipboard content
      if ($savedClip) {
        try { [System.Windows.Forms.Clipboard]::SetText($savedClip) } catch {}
      } else {
        try { [System.Windows.Forms.Clipboard]::Clear() } catch {}
      }

      # Send Enter if it was appended
      if ($hasEnter) {
        Start-Sleep -Milliseconds 30
        [System.Windows.Forms.SendKeys]::SendWait('{ENTER}')
      }
    }
  }
  $true | ConvertTo-Json -Compress
} catch {
  $false | ConvertTo-Json -Compress
}

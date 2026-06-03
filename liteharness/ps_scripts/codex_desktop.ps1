$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Windows.Forms

$argsData = if ($env:LWTT_ARGS) { $env:LWTT_ARGS | ConvertFrom-Json } else { [pscustomobject]@{} }
$action = if ($argsData.action) { [string]$argsData.action } else { 'list' }

Add-Type @"
using System;
using System.Runtime.InteropServices;

public class CodexDesktopWin32 {
    [StructLayout(LayoutKind.Sequential)]
    public struct RECT {
        public int Left;
        public int Top;
        public int Right;
        public int Bottom;
    }

    [StructLayout(LayoutKind.Sequential)]
    public struct POINT {
        public int X;
        public int Y;
    }

    [StructLayout(LayoutKind.Sequential)]
    public struct WINDOWPLACEMENT {
        public int length;
        public int flags;
        public int showCmd;
        public POINT ptMinPosition;
        public POINT ptMaxPosition;
        public RECT rcNormalPosition;
    }

    [DllImport("user32.dll")]
    public static extern bool GetWindowRect(IntPtr hWnd, out RECT lpRect);

    [DllImport("user32.dll")]
    public static extern bool IsWindowVisible(IntPtr hWnd);

    [DllImport("user32.dll")]
    public static extern bool IsIconic(IntPtr hWnd);

    [DllImport("user32.dll")]
    public static extern IntPtr GetForegroundWindow();

    [DllImport("user32.dll")]
    public static extern bool GetWindowPlacement(IntPtr hWnd, ref WINDOWPLACEMENT lpwndpl);

    [DllImport("user32.dll")]
    public static extern bool SetWindowPlacement(IntPtr hWnd, ref WINDOWPLACEMENT lpwndpl);

    [DllImport("user32.dll")]
    public static extern bool ShowWindow(IntPtr hWnd, int nCmdShow);

    [DllImport("user32.dll")]
    public static extern bool SetForegroundWindow(IntPtr hWnd);

    [DllImport("user32.dll")]
    public static extern bool SetWindowPos(IntPtr hWnd, IntPtr hWndInsertAfter, int X, int Y, int cx, int cy, uint uFlags);

    [DllImport("user32.dll")]
    public static extern bool GetCursorPos(out POINT lpPoint);

    [DllImport("user32.dll")]
    public static extern bool SetCursorPos(int X, int Y);

    [DllImport("user32.dll")]
    public static extern void mouse_event(int dwFlags, int dx, int dy, int dwData, int dwExtraInfo);

    public const int SW_RESTORE = 9;
    public const uint SWP_NOZORDER = 0x0004;
    public const uint SWP_NOACTIVATE = 0x0010;
    public const int MOUSEEVENTF_LEFTDOWN = 0x02;
    public const int MOUSEEVENTF_LEFTUP = 0x04;
}
"@

function Get-WindowRectObject([IntPtr]$handle) {
    $rect = New-Object CodexDesktopWin32+RECT
    if (-not [CodexDesktopWin32]::GetWindowRect($handle, [ref]$rect)) {
        return $null
    }
    return [pscustomobject]@{
        left = $rect.Left
        top = $rect.Top
        right = $rect.Right
        bottom = $rect.Bottom
        width = $rect.Right - $rect.Left
        height = $rect.Bottom - $rect.Top
    }
}

function Get-WindowPlacementObject([IntPtr]$handle) {
    $placement = New-Object CodexDesktopWin32+WINDOWPLACEMENT
    $placement.length = [System.Runtime.InteropServices.Marshal]::SizeOf([type][CodexDesktopWin32+WINDOWPLACEMENT])
    if (-not [CodexDesktopWin32]::GetWindowPlacement($handle, [ref]$placement)) {
        return $null
    }
    return $placement
}

function Convert-WindowPlacementObject($placement) {
    if (-not $placement) {
        return $null
    }
    return [pscustomobject]@{
        flags = $placement.flags
        showCmd = $placement.showCmd
        normal = @{
            left = $placement.rcNormalPosition.Left
            top = $placement.rcNormalPosition.Top
            right = $placement.rcNormalPosition.Right
            bottom = $placement.rcNormalPosition.Bottom
        }
    }
}

function Get-CodexWindows {
    $windows = @()
    foreach ($proc in Get-Process -Name Codex -ErrorAction SilentlyContinue) {
        if ($proc.MainWindowHandle -eq 0) {
            continue
        }
        $handle = [IntPtr]$proc.MainWindowHandle
        if (-not [CodexDesktopWin32]::IsWindowVisible($handle)) {
            continue
        }
        $rect = Get-WindowRectObject $handle
        if (-not $rect) {
            continue
        }
        $windows += [pscustomobject]@{
            pid = $proc.Id
            handle = $handle.ToInt64()
            title = $proc.MainWindowTitle
            rect = $rect
        }
    }
    return $windows | Sort-Object @{ Expression = { if ($_.title -eq 'Codex') { 0 } else { 1 } } }, pid
}

function Resolve-CodexWindow($requestedHandle) {
    $windows = @(Get-CodexWindows)
    if ($windows.Count -eq 0) {
        throw 'No visible Codex Desktop window found.'
    }
    if ($requestedHandle) {
        $requested = [int64]$requestedHandle
        foreach ($window in $windows) {
            if ([int64]$window.handle -eq $requested) {
                return $window
            }
        }
        throw "Codex Desktop window handle not found: $requested"
    }
    return $windows[0]
}

function Get-ClipboardSnapshot {
    try {
        return [System.Windows.Forms.Clipboard]::GetDataObject()
    } catch {
        return $null
    }
}

function Restore-ClipboardSnapshot($snapshot) {
    try {
        if ($snapshot) {
            [System.Windows.Forms.Clipboard]::SetDataObject($snapshot, $true)
        } else {
            [System.Windows.Forms.Clipboard]::Clear()
        }
    } catch {
    }
}

function Send-CodexDesktopText {
    $window = Resolve-CodexWindow $argsData.windowHandle
    $rect = $window.rect

    $clickX = if ($null -ne $argsData.clickX) { [int]$argsData.clickX } else { [int]($rect.left + ($rect.width / 2)) }
    $clickY = if ($null -ne $argsData.clickY) { [int]$argsData.clickY } else { [int]($rect.bottom - 96) }
    $submit = if ($null -ne $argsData.submit) { [bool]$argsData.submit } else { $true }
    $restoreMouse = if ($null -ne $argsData.restoreMouse) { [bool]$argsData.restoreMouse } else { $true }
    $dryRun = if ($null -ne $argsData.dryRun) { [bool]$argsData.dryRun } else { $false }
    $probeOnly = if ($null -ne $argsData.probeOnly) { [bool]$argsData.probeOnly } else { $false }
    $text = if ($null -ne $argsData.text) { [string]$argsData.text } else { '' }

    $handle = [IntPtr]([int64]$window.handle)
    $originalForeground = [CodexDesktopWin32]::GetForegroundWindow()
    $originalPlacement = Get-WindowPlacementObject $handle
    $originalRect = Get-WindowRectObject $handle
    $cursor = New-Object CodexDesktopWin32+POINT
    [void][CodexDesktopWin32]::GetCursorPos([ref]$cursor)
    $savedClipboard = $null

    if ($dryRun) {
        return [pscustomobject]@{
            ok = $true
            dry_run = $true
            window = $window
            click = @{ x = $clickX; y = $clickY }
            original_cursor = @{ x = $cursor.X; y = $cursor.Y }
            original_foreground = $originalForeground.ToInt64()
            original_placement = Convert-WindowPlacementObject $originalPlacement
            submit = $submit
            restore_mouse = $restoreMouse
            probe_only = $probeOnly
        }
    }

    try {
        $savedClipboard = Get-ClipboardSnapshot

        if ([CodexDesktopWin32]::IsIconic($handle)) {
            [void][CodexDesktopWin32]::ShowWindow($handle, [CodexDesktopWin32]::SW_RESTORE)
        }
        [void][CodexDesktopWin32]::SetForegroundWindow($handle)
        Start-Sleep -Milliseconds 120

        [void][CodexDesktopWin32]::SetCursorPos($clickX, $clickY)
        Start-Sleep -Milliseconds 40
        [CodexDesktopWin32]::mouse_event([CodexDesktopWin32]::MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
        [CodexDesktopWin32]::mouse_event([CodexDesktopWin32]::MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
        Start-Sleep -Milliseconds 80

        if (-not $probeOnly) {
            [System.Windows.Forms.Clipboard]::SetText($text)
            Start-Sleep -Milliseconds 40
            [System.Windows.Forms.SendKeys]::SendWait('^v')
            Start-Sleep -Milliseconds 80
            if ($submit) {
                [System.Windows.Forms.SendKeys]::SendWait('{ENTER}')
            }
        }

        return [pscustomobject]@{
            ok = $true
            dry_run = $false
            window = $window
            click = @{ x = $clickX; y = $clickY }
            original_cursor = @{ x = $cursor.X; y = $cursor.Y }
            submitted = $submit
            probe_only = $probeOnly
            restored_mouse = $restoreMouse
            restored_window = $true
            restored_foreground = $true
        }
    } finally {
        Restore-ClipboardSnapshot $savedClipboard
        $currentRect = $null
        $windowMoved = $false
        if ($originalRect) {
            try {
                $currentRect = Get-WindowRectObject $handle
                $windowMoved = -not (
                    $currentRect -and
                    $currentRect.left -eq $originalRect.left -and
                    $currentRect.top -eq $originalRect.top -and
                    $currentRect.right -eq $originalRect.right -and
                    $currentRect.bottom -eq $originalRect.bottom
                )
            } catch {
                $windowMoved = $true
            }
        }
        if ($windowMoved -and $originalPlacement) {
            try {
                $originalPlacement.length = [System.Runtime.InteropServices.Marshal]::SizeOf([type][CodexDesktopWin32+WINDOWPLACEMENT])
                [void][CodexDesktopWin32]::SetWindowPlacement($handle, [ref]$originalPlacement)
            } catch {
                $windowMoved = $true
            }
        }
        if ($windowMoved -and $originalRect) {
            try {
                $restoredRect = Get-WindowRectObject $handle
                $rectMatches = $restoredRect -and
                    $restoredRect.left -eq $originalRect.left -and
                    $restoredRect.top -eq $originalRect.top -and
                    $restoredRect.right -eq $originalRect.right -and
                    $restoredRect.bottom -eq $originalRect.bottom
                if (-not $rectMatches) {
                    $flags = [CodexDesktopWin32]::SWP_NOZORDER -bor [CodexDesktopWin32]::SWP_NOACTIVATE
                    [void][CodexDesktopWin32]::SetWindowPos(
                        $handle,
                        [IntPtr]::Zero,
                        [int]$originalRect.left,
                        [int]$originalRect.top,
                        [int]$originalRect.width,
                        [int]$originalRect.height,
                        [uint32]$flags
                    )
                }
            } catch {
            }
        }
        if ($originalForeground -and $originalForeground -ne [IntPtr]::Zero) {
            try {
                [void][CodexDesktopWin32]::SetForegroundWindow($originalForeground)
            } catch {
            }
        }
        if ($restoreMouse) {
            [void][CodexDesktopWin32]::SetCursorPos($cursor.X, $cursor.Y)
        }
    }
}

try {
    if ($action -eq 'list') {
        [pscustomobject]@{ ok = $true; windows = @(Get-CodexWindows) } | ConvertTo-Json -Depth 8 -Compress
    } elseif ($action -eq 'send') {
        Send-CodexDesktopText | ConvertTo-Json -Depth 8 -Compress
    } else {
        [pscustomobject]@{ ok = $false; error = "Unknown action: $action" } | ConvertTo-Json -Depth 8 -Compress
    }
} catch {
    [pscustomobject]@{ ok = $false; error = $_.Exception.Message } | ConvertTo-Json -Depth 8 -Compress
}

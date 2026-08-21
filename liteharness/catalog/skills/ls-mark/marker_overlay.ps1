# marker_overlay.ps1 — click-target overlay, two modes.
#
# TIMED (default): a topmost, click-THROUGH ring + crosshair that auto-closes
# after -Ms. WS_EX_TRANSPARENT so it never eats the click, NOACTIVATE so it
# never steals focus. This is the agent path (pccontrol.py marker) — unchanged.
#
# INTERACTIVE (-Interactive -HandoffFile <json>): the HUMAN path (/mark).
# The ring is draggable; two buttons ride under it. [send] hides the buttons
# (the RING STAYS — it is the highlight), waits a beat for the compositor,
# captures the marker's monitor, and writes <handoff>.png plus the handoff
# JSON: {x, y, mon, mon_x, mon_y, png}. [cancel] or Esc writes
# {"cancelled": true}. LiteTUI polls for the JSON and hands the screenshot to
# the model with the coordinates — a manual human screen-marker channel.
param(
    [int]$Mon = -1,
    [int]$X = 0,
    [int]$Y = 0,
    [int]$Ms = 2500,
    [string]$Label = '',
    [int]$Size = 110,
    [string]$Color = 'red',
    [switch]$Interactive,
    [string]$HandoffFile = ''
)
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
Add-Type @"
using System; using System.Runtime.InteropServices;
public class NW {
  [DllImport("user32.dll")] public static extern int GetWindowLong(IntPtr h, int i);
  [DllImport("user32.dll")] public static extern int SetWindowLong(IntPtr h, int i, int v);
}
"@

$screens = [System.Windows.Forms.Screen]::AllScreens
$gx = $X; $gy = $Y
if ($Mon -ge 0 -and $Mon -lt $screens.Count) {
    $b = $screens[$Mon].Bounds
    $gx = $b.X + $X; $gy = $b.Y + $Y
}
if ($Interactive -and $X -eq 0 -and $Y -eq 0) {
    # No target given: start centred on the primary monitor, ready to drag.
    $pb = [System.Windows.Forms.Screen]::PrimaryScreen.Bounds
    $gx = $pb.X + [int]($pb.Width / 2); $gy = $pb.Y + [int]($pb.Height / 2)
}

function Resolve-MarkerColor([string]$c) {
    if ($c -match '^#?[0-9A-Fa-f]{6}$') {
        $h = $c.TrimStart('#')
        return [System.Drawing.Color]::FromArgb(255,
            [Convert]::ToInt32($h.Substring(0, 2), 16),
            [Convert]::ToInt32($h.Substring(2, 2), 16),
            [Convert]::ToInt32($h.Substring(4, 2), 16))
    }
    $named = [System.Drawing.Color]::FromName($c)
    if (-not $named.IsKnownColor) {
        return [System.Drawing.Color]::FromArgb(255, 255, 45, 45)   # fallback red
    }
    return [System.Drawing.Color]::FromArgb(255, $named.R, $named.G, $named.B)
}

$size = if ($Size -ge 20) { $Size } else { 110 }
$labelH = if ($Label -ne '') { 20 } else { 0 }
$btnH = if ($Interactive) { 26 } else { 0 }
$key = [System.Drawing.Color]::FromArgb(255, 0, 254)   # transparency key
$mc = Resolve-MarkerColor $Color

$f = New-Object System.Windows.Forms.Form
$f.FormBorderStyle = 'None'
$f.StartPosition = 'Manual'
$f.TopMost = $true
$f.ShowInTaskbar = $false
$f.BackColor = $key
$f.TransparencyKey = $key
$f.KeyPreview = $true
$f.Size = New-Object System.Drawing.Size($size, ($size + $labelH + $btnH))
$f.Location = New-Object System.Drawing.Point(([int]($gx - $size / 2)), ([int]($gy - $size / 2)))

$f.Add_Paint({
    param($s, $e)
    $g = $e.Graphics
    $g.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
    $penW = [Math]::Max(3, [int]($size / 28))
    $pen = New-Object System.Drawing.Pen($mc, $penW)
    $brush = New-Object System.Drawing.SolidBrush($mc)
    $cx = [int]($size / 2); $cy = [int]($size / 2); $r = [int]($size / 2 - 10)
    $g.DrawEllipse($pen, ($cx - $r), ($cy - $r), (2 * $r), (2 * $r))
    $g.FillEllipse($brush, ($cx - 4), ($cy - 4), 8, 8)
    $g.DrawLine($pen, $cx, ($cy - $r - 7), $cx, ($cy - $r + 7))
    $g.DrawLine($pen, $cx, ($cy + $r - 7), $cx, ($cy + $r + 7))
    $g.DrawLine($pen, ($cx - $r - 7), $cy, ($cx - $r + 7), $cy)
    $g.DrawLine($pen, ($cx + $r - 7), $cy, ($cx + $r + 7), $cy)
    if ($Label -ne '') {
        $font = New-Object System.Drawing.Font('Consolas', 9, [System.Drawing.FontStyle]::Bold)
        $g.DrawString($Label, $font, $brush, 2, ($size - 2))
    }
})

if (-not $Interactive) {
    # ── timed, click-through: the agent path, exactly as before ──────────────
    $f.Add_Shown({
        $h = $f.Handle
        $GWL_EXSTYLE = -20
        $WS_EX_LAYERED = 0x80000
        $WS_EX_TRANSPARENT = 0x20
        $WS_EX_NOACTIVATE = 0x08000000
        $WS_EX_TOOLWINDOW = 0x80
        $ex = [NW]::GetWindowLong($h, $GWL_EXSTYLE)
        $ex = $ex -bor $WS_EX_LAYERED -bor $WS_EX_TRANSPARENT -bor $WS_EX_NOACTIVATE -bor $WS_EX_TOOLWINDOW
        [void][NW]::SetWindowLong($h, $GWL_EXSTYLE, $ex)
    })
    $timer = New-Object System.Windows.Forms.Timer
    $timer.Interval = $Ms
    $timer.Add_Tick({ $timer.Stop(); $f.Close() })
    $timer.Start()
    [System.Windows.Forms.Application]::Run($f)
    exit 0
}

# ── interactive: draggable ring + send/cancel, the human path ────────────────
if ($HandoffFile -eq '') { Write-Error 'Interactive mode needs -HandoffFile'; exit 1 }

$script:drag = $null
$f.Add_MouseDown({ param($s, $e)
    if ($e.Button -eq 'Left') { $script:drag = $e.Location } })
$f.Add_MouseMove({ param($s, $e)
    if ($script:drag) {
        $f.Location = New-Object System.Drawing.Point(
            ($f.Location.X + $e.X - $script:drag.X),
            ($f.Location.Y + $e.Y - $script:drag.Y))
    } })
$f.Add_MouseUp({ $script:drag = $null })

function Write-Handoff([hashtable]$obj) {
    # Write-then-rename so the poller never reads a torn file.
    $tmp = "$HandoffFile.tmp"
    ($obj | ConvertTo-Json -Compress) | Set-Content -Path $tmp -Encoding UTF8
    Move-Item -Force $tmp $HandoffFile
}

$btnSend = New-Object System.Windows.Forms.Button
$btnSend.Text = 'send'
$btnSend.Size = New-Object System.Drawing.Size(([int]($size / 2) - 2), 22)
$btnSend.Location = New-Object System.Drawing.Point(0, ($size + $labelH + 2))
$btnSend.BackColor = [System.Drawing.Color]::FromArgb(255, 20, 120, 60)
$btnSend.ForeColor = [System.Drawing.Color]::White
$btnSend.FlatStyle = 'Flat'

$btnCancel = New-Object System.Windows.Forms.Button
$btnCancel.Text = 'x'
$btnCancel.Size = New-Object System.Drawing.Size(([int]($size / 2) - 2), 22)
$btnCancel.Location = New-Object System.Drawing.Point(([int]($size / 2) + 2), ($size + $labelH + 2))
$btnCancel.BackColor = [System.Drawing.Color]::FromArgb(255, 90, 30, 30)
$btnCancel.ForeColor = [System.Drawing.Color]::White
$btnCancel.FlatStyle = 'Flat'

$f.Controls.Add($btnSend)
$f.Controls.Add($btnCancel)

$btnCancel.Add_Click({ Write-Handoff @{ cancelled = $true }; $f.Close() })
$f.Add_KeyDown({ param($s, $e)
    if ($e.KeyCode -eq 'Escape') { Write-Handoff @{ cancelled = $true }; $f.Close() } })

$btnSend.Add_Click({
    # Marker centre in virtual-desktop coords (ring centre, not form corner).
    $mx = $f.Location.X + [int]($size / 2)
    $my = $f.Location.Y + [int]($size / 2)

    # Which monitor holds the centre? Fall back to primary.
    $monIdx = 0
    for ($i = 0; $i -lt $screens.Count; $i++) {
        if ($screens[$i].Bounds.Contains($mx, $my)) { $monIdx = $i; break }
    }
    $mb = $screens[$monIdx].Bounds

    # THE RING STAYS IN THE SHOT — it IS the highlight. Only the buttons hide.
    $btnSend.Visible = $false
    $btnCancel.Visible = $false
    $f.Refresh()
    Start-Sleep -Milliseconds 220   # let the compositor catch up

    $png = [System.IO.Path]::ChangeExtension($HandoffFile, '.png')
    $bmp = New-Object System.Drawing.Bitmap($mb.Width, $mb.Height)
    $g = [System.Drawing.Graphics]::FromImage($bmp)
    $g.CopyFromScreen($mb.X, $mb.Y, 0, 0, $bmp.Size)
    $g.Dispose()
    $bmp.Save($png, [System.Drawing.Imaging.ImageFormat]::Png)
    $bmp.Dispose()

    Write-Handoff @{
        x = $mx; y = $my
        mon = $monIdx
        mon_x = ($mx - $mb.X); mon_y = ($my - $mb.Y)
        png = $png
    }
    $f.Close()
})

[System.Windows.Forms.Application]::Run($f)

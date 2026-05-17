# PROMPT_HISTORY auto-logger hook (UserPromptSubmit) - competition AI-usage evidence
# On every user prompt submission, append one row to PROMPT_HISTORY.md (pre-execution).
# The result summary is filled in afterwards by Claude on the same row.
$ErrorActionPreference = 'SilentlyContinue'

# Read stdin as UTF-8 explicitly (console OEM codepage would mangle Korean)
$stdin = [Console]::OpenStandardInput()
$sr = New-Object System.IO.StreamReader($stdin, (New-Object System.Text.UTF8Encoding($false)))
$raw = $sr.ReadToEnd()
$sr.Close()
try { $j = $raw | ConvertFrom-Json } catch { $j = $null }
$prompt = if ($j -and $j.prompt) { [string]$j.prompt } else { '' }
if ([string]::IsNullOrWhiteSpace($prompt)) { exit 0 }

# Prevent markdown-table breakage: newline -> space, pipe -> slash, collapse spaces, cap length
$clean = ($prompt -replace '\r?\n', ' ') -replace '\|', '/'
$clean = ($clean -replace '\s+', ' ').Trim()
if ($clean.Length -gt 300) { $clean = $clean.Substring(0, 300) + '...' }

$ts  = Get-Date -Format 'yyyy-MM-dd HH:mm'
$row = "| $ts | (자동기록) | $clean | (실행 전 자동기록 - 결과 요약 추후 갱신) |"

$histPath = Join-Path $PSScriptRoot '..\PROMPT_HISTORY.md'
if (-not (Test-Path -LiteralPath $histPath)) { exit 0 }

# Keep the table contiguous: trim trailing blank lines, append one row (UTF-8 no BOM)
$content = [System.IO.File]::ReadAllText($histPath)
$content = $content.TrimEnd("`r", "`n", " ", "`t")
$enc = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllText($histPath, $content + "`r`n" + $row + "`r`n", $enc)
exit 0

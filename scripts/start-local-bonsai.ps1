<#
.SYNOPSIS
    Starts a dedicated local Ollama server for the bonsai-27b forecast model.

.DESCRIPTION
    The Ollama desktop app serves its own model store, so a model that exists on
    disk under %USERPROFILE%\.ollama\models can still answer
    "model 'bonsai-27b' not found" on the app's port 11434. This script starts a
    separate server that reads that store explicitly, on a port that cannot
    collide with the WORED webui (webui publishes host port 8080).

    Measured on RTX 2070 8 GB / 16 GB RAM, bonsai-27b (1-bit, 26.9B):
      - model fully GPU resident, ~4.1 GB VRAM at 8192-token context
      - ~22-23 tokens/second in both modes

    Two supported operating points, both verified against the WORED forecast
    parser, and they are NOT interchangeable:

      FAST  "think": false, num_predict 700   -> ~17 s per role, 4/4 valid.
            Fits every WORED role timeout (worker 20s, analyst 60s, arbiter 60s,
            premium 90s); a bull+bear+arbiter bundle takes ~51 s. Direction
            follows the context, but confidence is emitted as a template ramp and
            the price path is a mechanical extrapolation.

      DEEP  "think": true,  num_predict 5000  -> ~143-187 s per call, valid and
            genuinely reasoned (it weighs overbought RSI against trend and lands
            near flat instead of extrapolating). Completed reasoning was observed
            at 3160, 3787 and 3991 tokens, and 2400 never completed, so budget
            5000 to keep headroom rather than chasing the minimum.
            This does NOT fit any role timeout: 90 s buys ~1980 tokens, which
            produces empty content. It only fits as a single call inside the
            300 s queue attempt budget, never as a 3-role bundle.

    Budget ladder measured with thinking ON: 800/1200/2400 tokens all returned
    empty content at done_reason=length; 4000+ completed with done_reason=stop.
    The thinking LEVEL is not honoured - think:"low" at 2400 produced the exact
    same 7650 reasoning characters as think:true, so only num_predict matters.

    Two requirements are measured, not assumed, and every caller must honour them:
      1. "think" must be sent as a TOP-LEVEL request field. It is ignored inside
         "options", and a Modelfile cannot bake it ("unknown parameter 'think'").
         Left unset, the model reasons by default and burns the whole budget
         before writing any content.
      2. The output schema must be restated AFTER the market context. Declared
         before the context, this 1-bit model echoes the input JSON instead of
         forecasting. Few-shot examples must not be used: the model copies the
         example prices verbatim, which passes format checks but stores fabricated
         numbers in forecast_points.

.PARAMETER Port
    Listen port. Default 8088, chosen to stay clear of the webui port 8080.

.PARAMETER ModelsDir
    Model store to read. Defaults to the store the desktop app pulls into.

.PARAMETER Model
    Model name that must be present in ModelsDir.

.PARAMETER ContextLength
    OLLAMA_CONTEXT_LENGTH for the server. 8192 covers the WORED forecast prompt.

.PARAMETER KeepAlive
    OLLAMA_KEEP_ALIVE, so the next role call does not pay model reload time.

.PARAMETER Smoke
    After the server is up, send one real forecast request and verify the answer
    satisfies the WORED forecast contract (4 steps, price, confidence, band).

.PARAMETER Deep
    With -Smoke, use the DEEP operating point instead of the FAST one: thinking
    enabled and a 5000-token budget. Takes roughly 3-4 minutes on this hardware,
    so only reach for it when you specifically want the reasoned answer.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\start-local-bonsai.ps1 -Smoke

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\start-local-bonsai.ps1 -Smoke -Deep

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\start-local-bonsai.ps1 -Port 8088

.NOTES
    Stop the server with:
      Get-NetTCPConnection -LocalPort 8088 -State Listen |
        ForEach-Object { Stop-Process -Id $_.OwningProcess }
#>

[CmdletBinding()]
param(
    [int]$Port = 8088,
    [string]$ModelsDir = "$env:USERPROFILE\.ollama\models",
    [string]$Model = "bonsai-27b:lmstudio-q1",
    [ValidateRange(2048, 262144)]
    [int]$ContextLength = 8192,
    [string]$KeepAlive = "30m",
    [switch]$Smoke,
    [switch]$Deep
)

$ErrorActionPreference = 'Stop'
$BaseUrl = "http://127.0.0.1:$Port"

function Test-ServerUp {
    param([string]$Url)
    try {
        $null = Invoke-RestMethod -Uri "$Url/api/tags" -TimeoutSec 5
        return $true
    } catch {
        return $false
    }
}

# --- preflight ---------------------------------------------------------------

if (-not (Get-Command ollama -ErrorAction SilentlyContinue)) {
    throw "ollama executable not found in PATH"
}
if (-not (Test-Path $ModelsDir)) {
    throw "model store not found: $ModelsDir"
}

# Schema restatement for the smoke test. Placeholders are filled per request so
# the model always sees the number it must stay near.
$SchemaTail = @(
    'Output ONLY one JSON object and nothing else. It must have exactly two keys:'
    '"summary" (a short string) and "points" (an array of 4 objects, one per step).'
    'Each point object must have keys "step" (1..4), "price" (number near {0}),'
    '"change_pct" (number), "confidence" (0-100), "low" (number) and "high" (number).'
    'Do not repeat the input context.'
) -join ' '

# --- start or adopt ----------------------------------------------------------

if (Test-ServerUp -Url $BaseUrl) {
    Write-Host "server already listening on $BaseUrl - adopting it" -ForegroundColor Yellow
    Write-Host "  (it may use a different model store than $ModelsDir)"
} else {
    if (-not (Test-Path (Join-Path $ModelsDir 'manifests'))) {
        throw "no manifests under $ModelsDir - the store looks empty, pull the model first"
    }

    $saved = @{}
    foreach ($name in 'OLLAMA_HOST', 'OLLAMA_MODELS', 'OLLAMA_CONTEXT_LENGTH', 'OLLAMA_KEEP_ALIVE') {
        $saved[$name] = (Get-Item "Env:$name" -ErrorAction SilentlyContinue)
    }
    try {
        $env:OLLAMA_HOST = "127.0.0.1:$Port"
        $env:OLLAMA_MODELS = $ModelsDir
        $env:OLLAMA_CONTEXT_LENGTH = "$ContextLength"
        $env:OLLAMA_KEEP_ALIVE = $KeepAlive

        Write-Host "starting ollama serve on $BaseUrl (store: $ModelsDir)" -ForegroundColor Cyan
        $proc = Start-Process -FilePath 'ollama' -ArgumentList 'serve' -PassThru -WindowStyle Hidden
    } finally {
        # Do not leak these into the caller's shell; the child already inherited them.
        foreach ($name in $saved.Keys) {
            $old = $saved[$name]
            if ($null -eq $old) {
                Remove-Item "Env:$name" -ErrorAction SilentlyContinue
            } else {
                Set-Item "Env:$name" $old.Value
            }
        }
    }

    $ready = $false
    for ($i = 0; $i -lt 30; $i++) {
        Start-Sleep -Seconds 2
        if (Test-ServerUp -Url $BaseUrl) { $ready = $true; break }
    }
    if (-not $ready) {
        throw "server did not come up on $BaseUrl within 60s (pid $($proc.Id)); inspect: ollama serve logs"
    }
    Write-Host "server up on $BaseUrl (pid $($proc.Id))" -ForegroundColor Green
}

# --- verify the model is visible --------------------------------------------

$tags = Invoke-RestMethod -Uri "$BaseUrl/api/tags" -TimeoutSec 20
$names = @($tags.models | ForEach-Object { $_.name })
if (-not ($names | Where-Object { $_ -like "$Model*" })) {
    Write-Host "models visible on $BaseUrl : $(if ($names.Count) { $names -join ', ' } else { '(none)' })" -ForegroundColor Red
    throw "model '$Model' is not in the store this server reads ($ModelsDir)"
}
Write-Host "model '$Model' is available on $BaseUrl" -ForegroundColor Green

# --- optional contract smoke test -------------------------------------------

if (-not $Smoke) {
    Write-Host "ready. add -Smoke to verify the forecast contract end to end." -ForegroundColor Cyan
    return
}

$basePrice = 81739.6
$context = [ordered]@{
    symbol        = 'btcusdt'
    market        = 'spot'
    base_price    = $basePrice
    horizon_steps = 4
    horizon_hours = 4
    step_minutes  = 60
    indicators    = @{ rsi14 = 66.2; macd_hist = 94.5; ema20 = 81567.7; ema50 = 81325.0 }
    recent_closes = @(80900, 81050, 81260, 81410, 81600, 81739.6)
    trend         = 'strong uptrend, higher highs, volume expanding'
}
$userPrompt = @(
    'Build a crypto forecast using this exact context.'
    'Return strict JSON matching the required schema.'
    ''
    ($context | ConvertTo-Json -Depth 5 -Compress)
    ($SchemaTail -f $basePrice)
) -join "`n"

# Operating point. FAST is the only one that fits WORED role timeouts; DEEP is a
# single reasoned call and needs minutes, not seconds.
if ($Deep) {
    $thinkValue = $true
    $numPredict = 5000
    $reqTimeout = 420
    $modeLabel = 'DEEP (thinking on, 5000 tokens, expect 3-4 minutes)'
} else {
    $thinkValue = $false
    $numPredict = 700
    $reqTimeout = 240
    $modeLabel = 'FAST (thinking off, 700 tokens, expect ~20 seconds)'
}

$payload = @{
    model    = $Model
    messages = @(
        @{ role = 'system'; content = 'You are a crypto risk arbiter. Answer with strict JSON only.' },
        @{ role = 'user'; content = $userPrompt }
    )
    stream   = $false
    think    = $thinkValue
    options  = @{ num_predict = $numPredict; temperature = 0; top_k = 5; top_p = 0.8 }
} | ConvertTo-Json -Depth 6

Write-Host "smoke [$modeLabel]: one forecast call (first run also pays model load)..." -ForegroundColor Cyan
$sw = [System.Diagnostics.Stopwatch]::StartNew()
$reply = Invoke-RestMethod -Uri "$BaseUrl/api/chat" -Method Post -ContentType 'application/json' `
    -Body $payload -TimeoutSec $reqTimeout
$sw.Stop()

$content = $reply.message.content
if ([string]::IsNullOrWhiteSpace($content)) {
    $thinkChars = 0
    if ($reply.message.thinking) { $thinkChars = $reply.message.thinking.Length }
    throw "smoke FAILED: empty content after $([int]$sw.Elapsed.TotalSeconds)s (reasoning took $thinkChars chars). think=$thinkValue must be a top-level field and num_predict=$numPredict may be too small to finish reasoning"
}

$parsed = $null
try {
    $parsed = $content | ConvertFrom-Json
} catch {
    throw "smoke FAILED: answer is not a JSON object ($($_.Exception.Message)). head=$($content.Substring(0, [Math]::Min(120, $content.Length)))"
}
if ($null -eq $parsed.points -or @($parsed.points).Count -ne 4) {
    throw "smoke FAILED: no 4-step points array. head=$($content.Substring(0, [Math]::Min(120, $content.Length)))"
}
foreach ($point in @($parsed.points)) {
    if ($null -eq $point.step -or $null -eq $point.price) {
        throw "smoke FAILED: point without step/price: $($point | ConvertTo-Json -Compress)"
    }
}

$prices = @($parsed.points | ForEach-Object { [double]$_.price })
$net = (($prices[-1] - $basePrice) / $basePrice) * 100.0
Write-Host ("smoke PASSED in {0:N1}s  eval={1} tokens  net={2:+0.00;-0.00;0.00}%" -f `
    $sw.Elapsed.TotalSeconds, $reply.eval_count, $net) -ForegroundColor Green
Write-Host "  prices : $($prices -join ' -> ')"
Write-Host "  summary: $($parsed.summary)"

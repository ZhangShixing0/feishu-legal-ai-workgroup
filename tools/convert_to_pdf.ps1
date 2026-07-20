param(
    [Parameter(Mandatory = $true)][string]$InputDocx,
    [Parameter(Mandatory = $true)][string]$OutputPdf
)

$ErrorActionPreference = 'Stop'
$inputPath = (Resolve-Path -LiteralPath $InputDocx).Path
$outputDirectory = Split-Path -Parent $OutputPdf
New-Item -ItemType Directory -Force -Path $outputDirectory | Out-Null

$token = [guid]::NewGuid().ToString('N')
$tempDocx = Join-Path $env:TEMP ("legal_doc_" + $token + ".docx")
$tempPdf = [System.IO.Path]::ChangeExtension($tempDocx, '.pdf')
$word = $null
$document = $null

try {
    Copy-Item -LiteralPath $inputPath -Destination $tempDocx -Force
    (Get-Item -LiteralPath $tempDocx).IsReadOnly = $false

    $word = New-Object -ComObject Word.Application
    $word.Visible = $false
    $word.DisplayAlerts = 0
    $word.AutomationSecurity = 3
    $word.Options.SaveNormalPrompt = $false

    $document = $word.Documents.Open($tempDocx, $false, $true)
    $document.ExportAsFixedFormat($tempPdf, 17)
    $document.Saved = $true
    $document.Close()
    $document = $null

    $word.Quit()
    $word = $null

    Copy-Item -LiteralPath $tempPdf -Destination $OutputPdf -Force
    Get-Item -LiteralPath $OutputPdf | Select-Object FullName, Length | ConvertTo-Json -Compress
}
finally {
    if ($document -ne $null) {
        $document.Saved = $true
        $document.Close()
    }
    if ($word -ne $null) {
        $word.Quit()
    }
    Remove-Item -LiteralPath $tempDocx, $tempPdf -Force -ErrorAction SilentlyContinue
}

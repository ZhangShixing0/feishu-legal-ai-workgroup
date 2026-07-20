param(
    [Parameter(Mandatory = $true)][string]$InputPath,
    [string]$Language = "zh-Hans",
    [switch]$Thorough
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$OutputEncoding = [Console]::OutputEncoding
Add-Type -AssemblyName System.Runtime.WindowsRuntime

$null = [Windows.Storage.StorageFile, Windows.Storage, ContentType = WindowsRuntime]
$null = [Windows.Storage.FileAccessMode, Windows.Storage, ContentType = WindowsRuntime]
$null = [Windows.Data.Pdf.PdfDocument, Windows.Data.Pdf, ContentType = WindowsRuntime]
$null = [Windows.Storage.Streams.InMemoryRandomAccessStream, Windows.Storage.Streams, ContentType = WindowsRuntime]
$null = [Windows.Graphics.Imaging.BitmapDecoder, Windows.Graphics.Imaging, ContentType = WindowsRuntime]
$null = [Windows.Graphics.Imaging.SoftwareBitmap, Windows.Graphics.Imaging, ContentType = WindowsRuntime]
$null = [Windows.Graphics.Imaging.BitmapPixelFormat, Windows.Graphics.Imaging, ContentType = WindowsRuntime]
$null = [Windows.Graphics.Imaging.BitmapAlphaMode, Windows.Graphics.Imaging, ContentType = WindowsRuntime]
$null = [Windows.Media.Ocr.OcrEngine, Windows.Media.Ocr, ContentType = WindowsRuntime]
$null = [Windows.Globalization.Language, Windows.Globalization, ContentType = WindowsRuntime]

$asTaskMethods = [System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object { $_.Name -eq "AsTask" -and $_.GetParameters().Count -eq 1 }
$asTaskGeneric = $asTaskMethods | Where-Object {
    $_.IsGenericMethod -and $_.GetParameters()[0].ParameterType.Name -eq "IAsyncOperation``1"
} | Select-Object -First 1
$asTaskAction = $asTaskMethods | Where-Object { -not $_.IsGenericMethod -and $_.GetParameters()[0].ParameterType.Name -eq "IAsyncAction" } | Select-Object -First 1

function Await-Result($Operation, [Type]$ResultType) {
    $task = $asTaskGeneric.MakeGenericMethod($ResultType).Invoke($null, @($Operation))
    $task.GetAwaiter().GetResult()
}

function Await-Action($Operation) {
    $task = $asTaskAction.Invoke($null, @($Operation))
    [void]$task.GetAwaiter().GetResult()
}

function Recognize-Stream($Stream, $Engine) {
    $Stream.Seek(0)
    $decoder = Await-Result ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($Stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
    $bitmap = Await-Result ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
    if ($bitmap.BitmapPixelFormat -ne [Windows.Graphics.Imaging.BitmapPixelFormat]::Bgra8 -or
        $bitmap.BitmapAlphaMode -eq [Windows.Graphics.Imaging.BitmapAlphaMode]::Straight) {
        $converted = [Windows.Graphics.Imaging.SoftwareBitmap]::Convert(
            $bitmap,
            [Windows.Graphics.Imaging.BitmapPixelFormat]::Bgra8,
            [Windows.Graphics.Imaging.BitmapAlphaMode]::Premultiplied
        )
        $bitmap.Dispose()
        $bitmap = $converted
    }
    try {
        $result = Await-Result ($Engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])
        return $result.Text
    }
    finally {
        $bitmap.Dispose()
    }
}

function Recognize-ImageFile([string]$Path, $Engine) {
    $storageFile = Await-Result ([Windows.Storage.StorageFile]::GetFileFromPathAsync($Path)) ([Windows.Storage.StorageFile])
    $randomStream = Await-Result ($storageFile.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
    try {
        return Recognize-Stream $randomStream $Engine
    }
    finally {
        $randomStream.Dispose()
    }
}

function New-OcrTiles([string]$Path) {
    Add-Type -AssemblyName System.Drawing
    $source = [System.Drawing.Bitmap]::FromFile($Path)
    $created = New-Object System.Collections.Generic.List[string]
    try {
        # Three overlapping horizontal slices make small banking-system table
        # cells substantially larger than the former 2x2 grid.
        $xStarts = @(0.0, 0.28, 0.56)
        $yStarts = @(0.0, 0.45)
        $tileWidth = [Math]::Min($source.Width, [Math]::Ceiling($source.Width * 0.44))
        $tileHeight = [Math]::Min($source.Height, [Math]::Ceiling($source.Height * 0.55))
        $part = 0
        foreach ($yFactor in $yStarts) {
            foreach ($xFactor in $xStarts) {
                $x = [Math]::Min([Math]::Floor($source.Width * $xFactor), $source.Width - $tileWidth)
                $y = [Math]::Min([Math]::Floor($source.Height * $yFactor), $source.Height - $tileHeight)
                $scale = [Math]::Min(4.0, 2400.0 / [Math]::Max($tileWidth, $tileHeight))
                $outWidth = [Math]::Max(1, [Math]::Floor($tileWidth * $scale))
                $outHeight = [Math]::Max(1, [Math]::Floor($tileHeight * $scale))
                $tile = New-Object System.Drawing.Bitmap($outWidth, $outHeight, [System.Drawing.Imaging.PixelFormat]::Format24bppRgb)
                $graphics = [System.Drawing.Graphics]::FromImage($tile)
                try {
                    $graphics.Clear([System.Drawing.Color]::White)
                    $graphics.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
                    $graphics.PixelOffsetMode = [System.Drawing.Drawing2D.PixelOffsetMode]::HighQuality
                    $graphics.DrawImage(
                        $source,
                        (New-Object System.Drawing.Rectangle(0, 0, $outWidth, $outHeight)),
                        (New-Object System.Drawing.Rectangle($x, $y, $tileWidth, $tileHeight)),
                        [System.Drawing.GraphicsUnit]::Pixel
                    )
                    $tempPath = Join-Path ([IO.Path]::GetTempPath()) ("case-ocr-" + [Guid]::NewGuid().ToString("N") + "-" + $part + ".png")
                    $tile.Save($tempPath, [System.Drawing.Imaging.ImageFormat]::Png)
                    $created.Add($tempPath)
                }
                finally {
                    $graphics.Dispose()
                    $tile.Dispose()
                }
                $part++
            }
        }
    }
    finally {
        $source.Dispose()
    }
    return $created
}

$resolved = (Resolve-Path -LiteralPath $InputPath).Path
$languageObject = New-Object Windows.Globalization.Language($Language)
$engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage($languageObject)
if ($null -eq $engine) {
    throw "Windows OCR language is unavailable: $Language"
}

$file = Await-Result ([Windows.Storage.StorageFile]::GetFileFromPathAsync($resolved)) ([Windows.Storage.StorageFile])
$extension = [IO.Path]::GetExtension($resolved).ToLowerInvariant()
$pages = New-Object System.Collections.Generic.List[object]

if ($extension -eq ".pdf") {
    $pdf = Await-Result ([Windows.Data.Pdf.PdfDocument]::LoadFromFileAsync($file)) ([Windows.Data.Pdf.PdfDocument])
    for ($index = 0; $index -lt $pdf.PageCount; $index++) {
        $page = $pdf.GetPage($index)
        $stream = New-Object Windows.Storage.Streams.InMemoryRandomAccessStream
        try {
            Await-Action ($page.RenderToStreamAsync($stream))
            $text = Recognize-Stream $stream $engine
            $pages.Add([ordered]@{ page = $index + 1; text = $text })
        }
        finally {
            $stream.Dispose()
            $page.Dispose()
        }
    }
}
else {
    $text = Recognize-ImageFile $resolved $engine
    $pages.Add([ordered]@{ page = 1; part = "full"; text = $text })
    $tiles = @()
    try {
        if ($Thorough) {
            $tiles = @(New-OcrTiles $resolved)
            for ($index = 0; $index -lt $tiles.Count; $index++) {
                $tileText = Recognize-ImageFile $tiles[$index] $engine
                $pages.Add([ordered]@{ page = 1; part = "tile-$($index + 1)"; text = $tileText })
            }
        }
    }
    finally {
        foreach ($tilePath in $tiles) {
            Remove-Item -LiteralPath $tilePath -Force -ErrorAction SilentlyContinue
        }
    }
}

[ordered]@{
    file = $resolved
    language = $Language
    pages = $pages
} | ConvertTo-Json -Depth 5 -Compress

$urls = @(
    'https://salaries-rights-briefs-equality.trycloudflare.com/snapshot.jpg',
    'https://absolutely-ordered-automation-satisfactory.trycloudflare.com/snapshot.jpg'
)
foreach ($url in $urls) {
    $r = Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 15
    $hash1 = (Get-FileHash -InputStream ([System.IO.MemoryStream]::new($r.Content)) -Algorithm MD5).Hash
    Start-Sleep -Seconds 3
    $r2 = Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 15
    $hash2 = (Get-FileHash -InputStream ([System.IO.MemoryStream]::new($r2.Content)) -Algorithm MD5).Hash
    $status = if ($hash1 -ne $hash2) { "LIVE ✓" } else { "FROZEN ✗" }
    Write-Host "$url"
    Write-Host "  size=$($r.Content.Length) hash1=$($hash1.Substring(0,10)) hash2=$($hash2.Substring(0,10)) -> $status"
}

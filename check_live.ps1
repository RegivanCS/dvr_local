$u1 = 'https://salaries-rights-briefs-equality.trycloudflare.com/snapshot.jpg'
$u2 = 'https://absolutely-ordered-automation-satisfactory.trycloudflare.com/snapshot.jpg'
foreach ($u in @($u1, $u2)) {
    $a = (Invoke-WebRequest $u -UseBasicParsing -TimeoutSec 15).Content.Length
    Start-Sleep 3
    $b = (Invoke-WebRequest $u -UseBasicParsing -TimeoutSec 15).Content.Length
    $state = if ($a -ne $b) { 'LIVE' } else { 'FROZEN-or-same-size' }
    Write-Host "$u => sz1=$a sz2=$b => $state"
}

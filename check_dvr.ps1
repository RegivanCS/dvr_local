$body = 'user=admin&password=!Rede!123&next=/'
$s = New-Object Microsoft.PowerShell.Commands.WebRequestSession
Invoke-WebRequest -Uri 'https://dvr.regivan.tec.br/login' -Method POST -Body $body -ContentType 'application/x-www-form-urlencoded' -WebSession $s -MaximumRedirection 5 -UseBasicParsing | Out-Null
$r = Invoke-RestMethod -Uri 'https://dvr.regivan.tec.br/api/cameras' -WebSession $s
$r | ConvertTo-Json -Depth 5

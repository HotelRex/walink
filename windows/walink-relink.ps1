<# Re-link this workstation. Needs the built-in Windows OpenSSH client.
   Usage:  .\walink-relink.ps1 -Name "Hotel Rex" -Code "ABCD-EFGH"
   Edit $Server and $Key below. The server runs it as: link CODE --replace NAME --yes (no extra confirmation).
   For everything else, open the TUI:  ssh -i <key> walink@controller #>
param(
  [Parameter(Mandatory=$true)][string]$Name,
  [Parameter(Mandatory=$true)][string]$Code
)
$Server = "walink@controller.example.net"          # <- edit
$Key    = "$env:USERPROFILE\.ssh\walink_ed25519"   # <- edit
$clean = ($Code -replace '[\s-]', '').ToUpper()
if ($clean -notmatch '^[A-Z0-9]{8}$') { Write-Error "Code must be 8 letters/digits"; exit 1 }
if ($Name -notmatch '^[A-Za-z0-9 _().-]{1,50}$') { Write-Error "Bad device name"; exit 1 }
# Keep the 'Link with phone number' code on screen; the phone needs ~1 minute.
ssh -i $Key -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes -o BatchMode=yes $Server "relink '$Name' $clean"
exit $LASTEXITCODE

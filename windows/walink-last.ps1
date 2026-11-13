<# Print the newest message of an allowed chat (e.g. a verification code). Needs the built-in Windows OpenSSH client.
   Usage:  .\walink-last.ps1 -Chat "PayPal"
   Edit $Server and $Key below. Any chat can be read (exact name, as in the chat list).
   Note: the phone opens the chat, so it is marked as read. #>
param([Parameter(Mandatory=$true)][string]$Chat)
$Server = "walink@controller.example.net"          # <- edit
$Key    = "$env:USERPROFILE\.ssh\walink_ed25519"   # <- edit
if ($Chat -match "['`"\r\n]" -or $Chat.Length -gt 60) { Write-Error "Bad chat name"; exit 1 }
ssh -i $Key -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes -o BatchMode=yes $Server "last '$Chat'"
exit $LASTEXITCODE

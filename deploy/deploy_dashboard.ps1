# Builds the dashboard website and uploads it to Cloudflare, then checks the live site serves the same data.
# Needs: Python, Node.js, and a Cloudflare login (`npx wrangler login` once). Run the "Dashboard data"
# workflow on GitHub first, so the dashboard-data branch has the files.
$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)
python deploy/build_site.py
if ($LASTEXITCODE) { throw "building the site failed" }
npx --yes wrangler@latest deploy --config deploy/wrangler.jsonc
if ($LASTEXITCODE) { throw "deploy failed" }
$local = (Get-Content dist\dashboard-site\data\meta.json | ConvertFrom-Json).generated_at
$live = (Invoke-RestMethod "https://labor-demo.managedanalytics.co/data/meta.json").generated_at
if ($local -ne $live) { throw "live site serves data from $live, expected $local" }
Write-Host "live site matches the build ($local)"

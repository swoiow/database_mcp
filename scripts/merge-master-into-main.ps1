# Merge local master into main without rewriting either branch's commits.
# Run from any directory:
#   & "M:\CodeHub\database_mcp\scripts\merge-master-into-main.ps1"
# For the initial merge of independent histories:
#   .\scripts\merge-master-into-main.ps1 -AllowUnrelatedHistories -PreferMasterOnConflict
# No fetch or push is performed. Commit or stash tracked changes before running.
[CmdletBinding()]
param(
    [switch]$AllowUnrelatedHistories,
    [switch]$PreferMasterOnConflict
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$repositoryPath = Split-Path -Parent $PSScriptRoot

function Invoke-RepositoryGit {
    param([string[]]$Arguments)

    # Avoid PowerShell 7's optional native-error promotion; check Git's exit code.
    $PSNativeCommandUseErrorActionPreference = $false
    $output = @(& git -C $repositoryPath @Arguments 2>&1)
    if ($LASTEXITCODE -ne 0) {
        throw ("git {0} failed:{1}{2}" -f ($Arguments -join " "), [Environment]::NewLine, ($output -join [Environment]::NewLine))
    }
    $output
}

$null = Invoke-RepositoryGit -Arguments @("rev-parse", "--show-toplevel")
foreach ($branch in @("main", "master")) {
    $null = Invoke-RepositoryGit -Arguments @("show-ref", "--verify", "refs/heads/$branch")
}

foreach ($operation in @("MERGE_HEAD", "CHERRY_PICK_HEAD", "REVERT_HEAD", "rebase-merge", "rebase-apply")) {
    $operationPath = [string](Invoke-RepositoryGit -Arguments @("rev-parse", "--git-path", $operation))
    if (-not [IO.Path]::IsPathRooted($operationPath)) {
        $operationPath = Join-Path $repositoryPath $operationPath
    }
    if (Test-Path -LiteralPath $operationPath) {
        throw "Finish or abort the existing Git operation ($operation) before merging."
    }
}

$trackedChanges = @(Invoke-RepositoryGit -Arguments @("status", "--porcelain", "--untracked-files=no"))
if ($trackedChanges.Count -gt 0) {
    throw "Tracked files have uncommitted changes. Commit or stash them before running this script."
}

# Untracked files are left in place; Git refuses to overwrite any that collide.
$currentBranch = [string](Invoke-RepositoryGit -Arguments @("branch", "--show-current"))
if ($currentBranch -ne "main") {
    Invoke-RepositoryGit -Arguments @("switch", "main")
}

# Exit code 1 means master has commits not yet included in main.
$PSNativeCommandUseErrorActionPreference = $false
& git -C $repositoryPath merge-base --is-ancestor master main
$ancestorExitCode = $LASTEXITCODE
if ($ancestorExitCode -eq 0) {
    Write-Host "main already contains every commit from master."
    return
}
if ($ancestorExitCode -ne 1) {
    throw "Unable to check the relationship between main and master."
}

$mergeArguments = @(
    "-c", "user.name=Codex",
    "-c", "user.email=codex@openai.com",
    "merge", "--no-edit"
)
if ($AllowUnrelatedHistories) {
    $mergeArguments += "--allow-unrelated-histories"
}
if ($PreferMasterOnConflict) {
    # Explicit opt-in: use master's conflicting hunks, retaining nonconflicting main changes.
    $mergeArguments += @("-X", "theirs")
}
$mergeArguments += @("--", "master")

try {
    Invoke-RepositoryGit -Arguments $mergeArguments
}
catch {
    Write-Warning "Merge did not complete. Check git status; resolve conflicts and commit, or run git merge --abort if a merge is in progress."
    throw
}

$null = Invoke-RepositoryGit -Arguments @("merge-base", "--is-ancestor", "master", "main")
Write-Host "master is merged into main. Existing commit IDs are preserved."

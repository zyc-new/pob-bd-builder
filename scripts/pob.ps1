param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]] $Args
)

& "$PSScriptRoot\start-cn.cmd" | Out-Null
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}
& python "$PSScriptRoot\run_pob.py" @Args
exit $LASTEXITCODE

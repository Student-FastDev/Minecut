$ErrorActionPreference='Stop'
$source=(Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$extensions=Join-Path $env:APPDATA 'Adobe\CEP\extensions'
$destination=[IO.Path]::GetFullPath((Join-Path $extensions 'com.minecut.premiere'))
if(-not $destination.StartsWith([IO.Path]::GetFullPath($extensions)+[IO.Path]::DirectorySeparatorChar,[StringComparison]::OrdinalIgnoreCase)){throw 'Invalid installation target'}
if($source -ne $destination){
    if(Test-Path -LiteralPath $destination){throw "Minecut is already installed at $destination. Close Premiere and rename that folder before installing this build."}
    New-Item -ItemType Directory -Path $destination -Force | Out-Null
    foreach($name in @('CSXS','client','host','backend','model','bin','runtime','licenses','README.md','TESTING.md')){
        $item=Join-Path $source $name
        if(Test-Path -LiteralPath $item){Copy-Item -LiteralPath $item -Destination $destination -Recurse}
    }
}
foreach($v in 9..15){$key="HKCU:\Software\Adobe\CSXS.$v";New-Item -Path $key -Force|Out-Null;New-ItemProperty -Path $key -Name PlayerDebugMode -PropertyType String -Value '1' -Force|Out-Null}
Write-Host "Minecut installed at $destination"
Write-Host 'Restart Premiere, then open Window > Extensions > Minecut.'
Write-Host 'Unsigned developer extension: PlayerDebugMode enabled for your user. No administrator rights needed.'

# 构建 Windows 可执行文件（dist/KonaBessPC.exe）
#
# 用法:  powershell -ExecutionPolicy Bypass -File build_exe.ps1
#
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

function Find-Python {
    foreach ($candidate in @("python", "py")) {
        $cmd = Get-Command $candidate -ErrorAction SilentlyContinue
        if ($cmd) { return $cmd.Source }
    }
    return $null
}

$python = Find-Python
if (-not $python) {
    Write-Host "未找到 Python，请先安装 Python 3.9+ 并加入 PATH。" -ForegroundColor Red
    exit 1
}

Write-Host "== 1/4 生成图标 ==" -ForegroundColor Cyan
& $python -m pip install --quiet --disable-pip-version-check pillow | Out-Null
& $python assets/make_icon.py

Write-Host "== 2/4 安装构建依赖 ==" -ForegroundColor Cyan
& $python -m pip install --quiet --disable-pip-version-check pillow lz4 pyinstaller

Write-Host "== 3/4 运行引擎自检 ==" -ForegroundColor Cyan
& $python main.py --check
if ($LASTEXITCODE -ne 0) { Write-Host "自检失败，中止打包。" -ForegroundColor Red; exit 1 }

Write-Host "== 4/4 打包可执行文件 ==" -ForegroundColor Cyan
& $python -m PyInstaller --noconfirm --clean --windowed --onefile `
    --name KonaBessPC `
    --icon assets/icon.ico `
    --hidden-import lz4.frame `
    --exclude-module numpy `
    --exclude-module unittest `
    --exclude-module pydoc `
    main.py

if ($LASTEXITCODE -ne 0) { Write-Host "打包失败。" -ForegroundColor Red; exit 1 }

Write-Host ""
Write-Host "打包完成: dist\KonaBessPC.exe" -ForegroundColor Green
Write-Host "可以分发该 exe 文件（无需安装 Python）。"

"""通过 GitHub API 创建 Release 并上传 exe 附件。

凭据来自 Git Credential Manager（与 git push 使用相同的存储凭据），
token 仅在内存中使用，不会打印或保存。
"""

from __future__ import annotations

import json
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPO = "THERentropy/KonaBess-PC"
TAG = "v1.0.1"
EXE = ROOT / "dist" / "KonaBessPC.exe"

NOTES = """## KonaBess PC 1.0.1 — 关键修复版本

骁龙 GPU 频率 / 电压表桌面编辑器（Windows），参考 [KonaBess Next](https://github.com/KonaBess-Next/KonaBess-Next) 实现。

> **重要提示：请勿使用 v1.0.0 导出的镜像刷机。** v1.0.0 在处理部分厂商（如小米）的 vendor_boot v4 布局时，会丢失 bootconfig、vbmeta 与 AVB 尾部数据，导出的镜像无法正确启动。请使用本版本重新导出。

### 本版本修复

- **vendor_boot v4 布局兼容性**：兼容表（vendor_ramdisk_table）位于 DTB 之后的厂商布局（小米等），表项不再被误判为独立 ramdisk 段
- **尾部数据完整保留**：vbmeta、0 填充与 AVB footer 原样保留在输出镜像中，文件大小与结构与原镜像一致
- **未修改内容字节级不变**：未编辑的设备树保持原始字节输出，无编辑保存 = 与原文件完全一致
- **新增 AVB 提示**：保存带 AVB 签名的镜像时提醒签名校验失效风险

### 功能

- 打开 `boot.img` / `vendor_boot.img` / `dtbo.img` / `.dtb` / `.dts`，自动识别芯片与 GPU 频率表（骁龙 855 → 8 Elite Gen 5）
- 图形化编辑频率 / 电压档位 / 总线档位，添加、复制、删除等级（自动维护指针）
- 独立电压表（OPP table）编辑、调频曲线可视化（双击数据点编辑）
- 撤销 / 重做（100 步）、调参方案 JSON 导入导出、导出 DTS / DTB
- 深色主题，支持高 DPI 显示

### 使用

1. 提取镜像：新机型（骁龙 8 Gen 2 之后）一般为 `vendor_boot.img`，旧机型为 `boot.img`
2. 打开文件 → 编辑频率 / 电压 → 「保存镜像」
3. 刷入：`fastboot flash boot boot_new.img`（或 `fastboot flash vendor_boot ...`）

详细说明见 [README](https://github.com/THERentropy/KonaBess-PC#readme)。

### 注意

- 修改前请**务必备份原镜像**，并确认设备可正常进入 fastboot 模式
- 刷入修改镜像需要已解锁 Bootloader
- 超频 / 降压存在风险，请一次只做小幅调整并充分测试

### 免责声明

修改系统文件与超频 / 降压存在固有风险，开发者不对因使用本工具导致的设备损坏、数据丢失或系统不稳定承担责任。
"""


def get_token() -> str:
    """从 Git Credential Manager 获取 GitHub 访问令牌。"""
    result = subprocess.run(
        ["git", "credential", "fill"],
        input="protocol=https\nhost=github.com\n\n",
        capture_output=True, text=True, check=True,
    )
    token = ""
    for line in result.stdout.splitlines():
        if line.startswith("password="):
            token = line.split("=", 1)[1]
    if not token:
        raise SystemExit("未找到 GitHub 凭据，请先执行一次 git push 完成登录。")
    return token


def api_request(url: str, token: str, data: bytes | None = None,
                headers: dict | None = None, method: str = "GET"):
    request = urllib.request.Request(url, data=data, method=method)
    request.add_header("Authorization", "Bearer %s" % token)
    request.add_header("Accept", "application/vnd.github+json")
    request.add_header("User-Agent", "konabess-pc")
    for key, value in (headers or {}).items():
        request.add_header(key, value)
    with urllib.request.urlopen(request) as response:
        body = response.read()
        return response.status, json.loads(body) if body else {}


def main() -> int:
    if not EXE.exists():
        raise SystemExit("未找到 %s，请先运行 build_exe.ps1 打包。" % EXE)
    size_mb = EXE.stat().st_size / 1048576
    print("本地附件: %s (%.2f MB, %s)"
          % (EXE.name, size_mb, EXE.stat().st_mtime))

    token = get_token()
    print("已获取 GitHub 凭据")

    payload = {
        "tag_name": TAG,
        "target_commitish": "main",
        "name": "KonaBess PC %s" % TAG.lstrip("v"),
        "body": NOTES,
        "draft": False,
        "prerelease": False,
    }

    # 1. 若该 tag 的 Release 已存在则更新说明，否则创建（幂等）
    existing = None
    try:
        _status, existing = api_request(
            "https://api.github.com/repos/%s/releases/tags/%s" % (REPO, TAG), token)
    except urllib.error.HTTPError as exc:
        if exc.code != 404:
            body = exc.read().decode("utf-8", errors="replace")
            raise SystemExit("查询 Release 失败 (HTTP %d): %s" % (exc.code, body))

    if existing is not None:
        try:
            _status, release = api_request(
                "https://api.github.com/repos/%s/releases/%s" % (REPO, existing["id"]), token,
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="PATCH")
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise SystemExit("更新 Release 失败 (HTTP %d): %s" % (exc.code, body))
        print("Release 已存在，已更新说明: %s" % release["html_url"])
    else:
        try:
            _status, release = api_request(
                "https://api.github.com/repos/%s/releases" % REPO, token,
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST")
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise SystemExit("创建 Release 失败 (HTTP %d): %s" % (exc.code, body))
        print("Release 已创建: %s (id=%s), tag=%s" % (release["html_url"], release["id"], TAG))

    # 2. 删除同名旧附件（避免下载到过期文件）
    for asset in release.get("assets", []):
        if asset["name"] == EXE.name:
            try:
                api_request(
                    "https://api.github.com/repos/%s/releases/assets/%s" % (REPO, asset["id"]),
                    token, method="DELETE")
                print("已删除旧附件: %s (%.2f MB, 上传于 %s)"
                      % (asset["name"], asset["size"] / 1048576, asset["created_at"]))
            except urllib.error.HTTPError as exc:
                body = exc.read().decode("utf-8", errors="replace")
                raise SystemExit("删除旧附件失败 (HTTP %d): %s" % (exc.code, body))

    # 3. 上传新附件
    upload_url = release["upload_url"].split("{")[0]
    try:
        _status, asset = api_request(
            "%s?name=%s" % (upload_url, EXE.name), token,
            data=EXE.read_bytes(),
            headers={"Content-Type": "application/octet-stream"},
            method="POST")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise SystemExit("上传附件失败 (HTTP %d): %s" % (exc.code, body))

    print("附件已上传: %s (%.2f MB)" % (asset["browser_download_url"],
                                        asset["size"] / 1048576))
    print()
    print("完成: %s" % release["html_url"])
    return 0


if __name__ == "__main__":
    sys.exit(main())

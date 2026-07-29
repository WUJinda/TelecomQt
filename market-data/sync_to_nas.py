#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""sync_to_nas.py — 把本地 market-data/exports 推送到 NAS 面板。

配合 batch_fetch_all.py + batch_export_all.py 使用，完成「本地采集 → 推送 NAS」全链路。

用法：
    # 推送全部 exports（默认推送 exports/D1/ 下所有 JSON）
    python sync_to_nas.py

    # 只推送指定品种
    python sync_to_nas.py --files rb0 ag0 cu0

    # 推送到内网地址（默认走公网域名）
    python sync_to_nas.py --host http://192.168.5.8:8000

    # 推送实验数据（experiment.json）
    python sync_to_nas.py --experiment ../backend/data/experiments/xxx/experiment.json

配置：
    首次使用前，复制 .env.example 为 .env 并填入 NAS 地址和令牌：
        cp .env.example .env
        # 编辑 .env 填入 PANEL_HOST 和 DEPLOY_TOKEN

    或通过命令行参数 / 环境变量指定。

完整工作流：
    cd market-data
    .venv/Scripts/python.exe batch_fetch_all.py     # 1. AKShare 拉日K
    .venv/Scripts/python.exe batch_export_all.py    # 2. 导出 JSON
    python sync_to_nas.py                           # 3. 推送到 NAS

    # 或者三步合一：
    .venv/Scripts/python.exe batch_fetch_all.py && \\
    .venv/Scripts/python.exe batch_export_all.py && \\
    python sync_to_nas.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

try:
    import requests
except ImportError:
    print("错误：需要 requests 库。pip install requests")
    sys.exit(1)

# ── 配置 ──

# .env 文件支持（不依赖 python-dotenv，手动解析）
_env_file = Path(__file__).parent / ".env"
if _env_file.exists():
    for line in _env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, val = line.partition("=")
            os.environ.setdefault(key.strip(), val.strip())

PANEL_HOST = os.environ.get("PANEL_HOST", "https://panel.darewin.icu")
DEPLOY_TOKEN = os.environ.get("DEPLOY_TOKEN", "")

# exports 目录（相对于本文件）
EXPORT_DIR = Path(__file__).resolve().parent / "exports"


def sync_market_data(host: str, token: str, symbols: list[str] | None = None,
                     subdir: str = "D1") -> None:
    """推送 exports/<subdir>/ 下的 JSON 文件到 NAS。

    Args:
        host: 面板地址（如 https://panel.darewin.icu）
        token: DEPLOY_TOKEN
        symbols: 只推送指定品种（如 ['rb0', 'ag0']）；None = 全部
        subdir: exports 子目录（D1 / H2 / H4）
    """
    src_dir = EXPORT_DIR / subdir
    if not src_dir.exists():
        print(f"错误：目录不存在 {src_dir}")
        sys.exit(1)

    # 收集要推送的文件
    if symbols:
        files_to_send = []
        for sym in symbols:
            # 尝试小写和原始大小写
            for pattern in [f"{sym.lower()}_kline.json", f"{sym}_kline.json", f"{sym}_kline.json"]:
                p = src_dir / pattern
                if p.exists():
                    files_to_send.append(p)
                    break
            else:
                print(f"  警告：未找到 {sym} 的导出文件")
    else:
        files_to_send = sorted(src_dir.glob("*.json"))

    if not files_to_send:
        print("没有文件可推送")
        return

    print(f"准备推送 {len(files_to_send)} 个文件到 {host}")
    print(f"  源目录：{src_dir}")
    print(f"  目标：  {host}/api/data/sync")
    print()

    # 构建 multipart 表单
    url = f"{host.rstrip('/')}/api/data/sync"
    headers = {"Authorization": f"Bearer {token}"}

    opened_files = []
    multipart_files = []
    try:
        for f in files_to_send:
            fh = open(f, "rb")
            opened_files.append(fh)
            # 文件名带上子目录前缀，让服务端知道放哪个目录
            multipart_files.append(("files", (f"{subdir}/{f.name}", fh)))

        print("上传中...")
        resp = requests.post(url, headers=headers, files=multipart_files, timeout=120)

        if resp.status_code == 200:
            data = resp.json()
            print(f"\n✅ 推送成功！")
            print(f"   上传文件：{len(data.get('uploaded', []))} 个")
            for name in data.get("uploaded", []):
                print(f"   ✓ {name}")
            if data.get("errors"):
                print(f"   失败文件：{len(data['errors'])} 个")
                for err in data["errors"]:
                    print(f"   ✗ {err['file']}: {err['error']}")
            print(f"\n   数据已写入 NAS 的 exports 目录，面板实时生效。")
        elif resp.status_code == 403:
            print(f"\n❌ 认证失败（{resp.status_code}）")
            if "DEPLOY_TOKEN" in resp.text:
                print("   NAS 端未配置 DEPLOY_TOKEN 环境变量。")
                print("   请在 docker-compose.yml 的 panel 服务中添加：")
                print('     environment:')
                print('       - DEPLOY_TOKEN=你的密钥')
            else:
                print("   Token 不正确，请检查 .env 中的 DEPLOY_TOKEN。")
            sys.exit(1)
        else:
            print(f"\n❌ 推送失败（HTTP {resp.status_code}）")
            print(f"   {resp.text[:500]}")
            sys.exit(1)
    finally:
        for fh in opened_files:
            fh.close()


def sync_experiment(host: str, token: str, exp_file: str) -> None:
    """推送单个 experiment.json 到 NAS。"""
    fpath = Path(exp_file)
    if not fpath.exists():
        print(f"错误：文件不存在 {fpath}")
        sys.exit(1)

    exp_id = fpath.parent.name  # 目录名作为 experiment_id
    url = f"{host.rstrip('/')}/api/data/sync-experiment"
    headers = {"Authorization": f"Bearer {token}"}

    print(f"推送实验数据：{fpath.name} (id={exp_id})")
    print(f"  目标：{url}")

    with open(fpath, "rb") as f:
        resp = requests.post(
            url,
            headers=headers,
            files={"file": (fpath.name, f)},
            data={"experiment_id": exp_id},
            timeout=60,
        )

    if resp.status_code == 200:
        data = resp.json()
        print(f"\n✅ 实验数据推送成功！")
        print(f"   experiment_id: {data['experiment_id']}")
        print(f"   写入路径: {data['path']}")
    else:
        print(f"\n❌ 推送失败（HTTP {resp.status_code}）")
        print(f"   {resp.text[:500]}")
        sys.exit(1)


def main():
    import argparse

    p = argparse.ArgumentParser(
        description="推送 market-data exports 到 NAS 面板",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例：
  %(prog)s                              # 推送全部 D1 数据
  %(prog)s --files rb0 ag0 cu0          # 只推送指定品种
  %(prog)s --host http://192.168.5.8:8000  # 推送到内网
  %(prog)s --experiment path/to/experiment.json  # 推送实验数据
        """,
    )
    p.add_argument("--files", nargs="*", help="只推送指定品种（如 rb0 ag0 cu0）")
    p.add_argument("--host", default=PANEL_HOST, help=f"面板地址（默认：{PANEL_HOST}）")
    p.add_argument("--token", default=DEPLOY_TOKEN, help="DEPLOY_TOKEN（默认从 .env 读取）")
    p.add_argument("--subdir", default="D1", help="exports 子目录（D1/H2/H4，默认 D1）")
    p.add_argument("--experiment", help="推送单个 experiment.json 文件")
    args = p.parse_args()

    if not args.token:
        print("错误：未设置 DEPLOY_TOKEN")
        print("请创建 market-data/.env 文件：")
        print("  PANEL_HOST=https://panel.darewin.icu")
        print("  DEPLOY_TOKEN=你的密钥")
        print("\n或在命令行指定：--token YOUR_TOKEN")
        sys.exit(1)

    if args.experiment:
        sync_experiment(args.host, args.token, args.experiment)
    else:
        sync_market_data(args.host, args.token, args.files, args.subdir)


if __name__ == "__main__":
    main()

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
        cp .env.example ..env
        # 编辑 .env 填入 PANEL_HOST 和 DEPLOY_TOKEN

    或通过命令行参数 / 环境变量指定。

完整工作流：
    cd market-data
    .venv/Scripts/python.exe batch_fetch_all.py     # 1. AKShare 拉日K
    .venv/Scripts/python.exe batch_export_all.py    # 2. 导出 JSON
    python sync_to_nas.py                           # 3. 推送到 NAS

    # 或者三步合一：
    .venv/Scripts/python.exe batch_fetch_all.py && \
    .venv/Scripts/python.exe batch_export_all.py && \
    python sync_to_nas.py
"""
from __future__ import annotations

import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

# 强制禁用系统代理（Clash 等代理工具会大幅拖慢 Cloudflare Tunnel 请求）
os.environ["NO_PROXY"] = "*"
os.environ["no_proxy"] = "*"

try:
    import requests
    from requests.adapters import HTTPAdapter
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

# 并发上传线程数（Cloudflare Tunnel 延迟高，并发能显著提速）
_MAX_WORKERS = 5

# requests session（禁用代理，复用连接池）
_session = requests.Session()
_session.trust_env = False  # 关键：不读取系统代理设置
_session.proxies = {"http": None, "https": None}


def _upload_one(url: str, headers: dict, subdir: str, filepath: Path) -> tuple[str, bool, str]:
    """上传单个文件，返回 (文件名, 是否成功, 消息)。"""
    try:
        with open(filepath, "rb") as f:
            resp = _session.post(
                url,
                headers=headers,
                files=[("files", (f"{subdir}/{filepath.name}", f))],
                timeout=60,
            )
        if resp.status_code == 200:
            return (filepath.name, True, "OK")
        else:
            return (filepath.name, False, f"HTTP {resp.status_code}")
    except Exception as e:
        return (filepath.name, False, str(e))


def _upload_zip(url: str, headers: dict, subdir: str, files: list[Path]) -> tuple[int, int, str]:
    """打包 ZIP 上传，返回 (成功数, 失败数, 消息)。"""
    import zipfile
    import io

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in files:
            zf.write(f, f"{subdir}/{f.name}")

    raw_size = sum(f.stat().st_size for f in files)
    zip_size = buf.tell()
    buf.seek(0)

    resp = _session.post(
        url,
        headers=headers,
        files={"file": ("exports.zip", buf, "application/zip")},
        timeout=300,
    )

    if resp.status_code == 200:
        data = resp.json()
        ok = len(data.get("uploaded", []))
        err = len(data.get("errors", []))
        msg = f"raw={raw_size//1024}KB zip={zip_size//1024}KB ratio={zip_size*100//raw_size}%"
        return (ok, err, msg)
    else:
        return (0, len(files), f"HTTP {resp.status_code}: {resp.text[:200]}")


def sync_market_data(host: str, token: str, symbols: list[str] | None = None,
                     subdir: str = "D1") -> None:
    """推送 exports/<subdir>/ 下的 JSON 文件到 NAS（并发上传）。

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
            for pattern in [f"{sym.lower()}_kline.json", f"{sym}_kline.json"]:
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

    url = f"{host.rstrip('/')}/api/data/sync-zip"
    headers = {"Authorization": f"Bearer {token}"}

    total = len(files_to_send)
    total_size = sum(f.stat().st_size for f in files_to_send)
    print(f"准备推送 {total} 个文件（{total_size / 1024 / 1024:.1f}MB）到 {host}")
    print(f"  方式：ZIP 压缩单次上传")
    print()

    t0 = time.time()
    ok_count, err_count, detail = _upload_zip(url, headers, subdir, files_to_send)
    elapsed = time.time() - t0
    print(f"  压缩: {detail}")
    print()
    if err_count == 0:
        print(f"[OK] 全部推送成功! {ok_count} 个文件, 耗时 {elapsed:.1f}s")
    else:
        print(f"[!] 推送完成: {ok_count} 成功, {err_count} 失败, 耗时 {elapsed:.1f}s")
    print(f"\n   数据已写入 NAS 的 exports 目录，面板实时生效。")


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
        resp = _session.post(
            url,
            headers=headers,
            files={"file": (fpath.name, f)},
            data={"experiment_id": exp_id},
            timeout=120,
        )

    if resp.status_code == 200:
        data = resp.json()
        print(f"\n[OK] 实验数据推送成功!")
        print(f"   experiment_id: {data['experiment_id']}")
        print(f"   写入路径: {data['path']}")
    else:
        print(f"\n[FAIL] 推送失败 (HTTP {resp.status_code})")
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
    p.add_argument("--workers", type=int, default=_MAX_WORKERS,
                   help=f"并发上传线程数（默认 {_MAX_WORKERS}）")
    args = p.parse_args()

    if not args.token:
        print("错误：未设置 DEPLOY_TOKEN")
        print("请创建 market-data/.env 文件：")
        print("  PANEL_HOST=https://panel.darewin.icu")
        print("  DEPLOY_TOKEN=你的密钥")
        print("\n或在命令行指定：--token YOUR_TOKEN")
        sys.exit(1)

    # 运行时调整并发数（通过模块级变量传给 sync_market_data）
    import sync_to_nas as _self
    _self._MAX_WORKERS = args.workers

    if args.experiment:
        sync_experiment(args.host, args.token, args.experiment)
    else:
        sync_market_data(args.host, args.token, args.files, args.subdir)


if __name__ == "__main__":
    main()

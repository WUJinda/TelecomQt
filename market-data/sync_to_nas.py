#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""sync_to_nas.py — 把本地 market-data 推送到 NAS 面板。

完整工作流：
    cd market-data
    .venv/Scripts/python.exe batch_fetch_all.py     # 1. AKShare 拉日K
    .venv/Scripts/python.exe batch_export_all.py    # 2. 导出 JSON
    python sync_to_nas.py --all                     # 3. 推送全部数据到 NAS

用法：
    python sync_to_nas.py --all                     # 推送 exports + store（推荐）
    python sync_to_nas.py                           # 只推送 exports/D1/
    python sync_to_nas.py --store                   # 只推送 store/daily/（品种目录）
    python sync_to_nas.py --files rb0 ag0 cu0       # 只推送指定品种
    python sync_to_nas.py --experiment path/to/experiment.json  # 推送回测结果
    python sync_to_nas.py --host http://192.168.5.8:8000        # 推送到内网

配置：
    首次使用前，复制 .env.example 为 .env 并填入 NAS 地址和令牌：
        cp .env.example .env
        # 编辑 .env 填入 PANEL_HOST 和 DEPLOY_TOKEN
"""
from __future__ import annotations

import io
import os
import sys
import time
import zipfile
from pathlib import Path

# 强制禁用系统代理（Clash 等代理工具会大幅拖慢 Cloudflare Tunnel 请求）
os.environ["NO_PROXY"] = "*"
os.environ["no_proxy"] = "*"

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

# market-data 根目录（本文件所在目录）
_MARKET_DATA_DIR = Path(__file__).resolve().parent
EXPORT_DIR = _MARKET_DATA_DIR / "exports"
STORE_DAILY_DIR = _MARKET_DATA_DIR / "store" / "daily"

# requests session（禁用代理，复用连接池）
_session = requests.Session()
_session.trust_env = False  # 关键：不读取系统代理设置
_session.proxies = {"http": None, "https": None}


# ── 核心上传函数 ──

def _upload_zip(url: str, headers: dict, files: list[Path],
                zip_internal_prefix: str, target_dir: str = "exports") -> tuple[int, int, str]:
    """打包 ZIP 上传到 sync-zip 端点。

    Args:
        url: 完整的 /api/data/sync-zip URL
        headers: 包含 Authorization 的 headers
        files: 要打包的本地文件列表
        zip_internal_prefix: ZIP 内文件路径前缀（如 "D1" 或 "daily"）
        target_dir: 服务端目标目录（exports 或 store）

    Returns:
        (成功数, 失败数, 压缩信息描述)
    """
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in files:
            # 保留相对目录结构
            arcname = f.relative_to(_MARKET_DATA_DIR)
            zf.write(f, str(arcname))

    raw_size = sum(f.stat().st_size for f in files)
    zip_size = buf.tell()
    buf.seek(0)

    resp = _session.post(
        url,
        headers=headers,
        files={"file": ("data.zip", buf, "application/zip")},
        data={"target_dir": target_dir},
        timeout=300,
    )

    if resp.status_code == 200:
        data = resp.json()
        ok = len(data.get("uploaded", []))
        err = len(data.get("errors", []))
        ratio = zip_size * 100 // raw_size if raw_size else 0
        msg = f"raw={raw_size//1024}KB zip={zip_size//1024}KB ratio={ratio}%"
        return (ok, err, msg)
    else:
        return (0, len(files), f"HTTP {resp.status_code}: {resp.text[:200]}")


def sync_exports(host: str, token: str, symbols: list[str] | None = None,
                 subdir: str = "D1") -> None:
    """推送 exports/<subdir>/ 下的 JSON 文件到 NAS。"""
    src_dir = EXPORT_DIR / subdir
    if not src_dir.exists():
        print(f"[SKIP] 目录不存在: {src_dir}")
        return

    if symbols:
        files_to_send = []
        for sym in symbols:
            for pattern in [f"{sym.lower()}_kline.json", f"{sym}_kline.json"]:
                p = src_dir / pattern
                if p.exists():
                    files_to_send.append(p)
                    break
            else:
                print(f"  警告: 未找到 {sym} 的导出文件")
    else:
        files_to_send = sorted(src_dir.glob("*.json"))

    if not files_to_send:
        print("[SKIP] 没有文件可推送")
        return

    url = f"{host.rstrip('/')}/api/data/sync-zip"
    headers = {"Authorization": f"Bearer {token}"}

    total_size = sum(f.stat().st_size for f in files_to_send)
    print(f"[1] exports/{subdir}/: {len(files_to_send)} files ({total_size//1024}KB)")

    t0 = time.time()
    ok, err, detail = _upload_zip(url, headers, files_to_send, subdir, "exports")
    elapsed = time.time() - t0
    print(f"    {detail}")
    if err == 0:
        print(f"    [OK] {ok} files in {elapsed:.1f}s")
    else:
        print(f"    [!] {ok} ok, {err} failed in {elapsed:.1f}s")


def sync_store(host: str, token: str) -> None:
    """推送 store/daily/ 下的 parquet 文件到 NAS（品种目录数据）。"""
    if not STORE_DAILY_DIR.exists():
        print(f"[SKIP] 目录不存在: {STORE_DAILY_DIR}")
        return

    files_to_send = sorted(STORE_DAILY_DIR.rglob("*.parquet"))
    if not files_to_send:
        print("[SKIP] 没有文件可推送")
        return

    url = f"{host.rstrip('/')}/api/data/sync-zip"
    headers = {"Authorization": f"Bearer {token}"}

    total_size = sum(f.stat().st_size for f in files_to_send)
    print(f"[2] store/daily/: {len(files_to_send)} files ({total_size//1024}KB)")

    t0 = time.time()
    ok, err, detail = _upload_zip(url, headers, files_to_send, "daily", "store")
    elapsed = time.time() - t0
    print(f"    {detail}")
    if err == 0:
        print(f"    [OK] {ok} files in {elapsed:.1f}s")
    else:
        print(f"    [!] {ok} ok, {err} failed in {elapsed:.1f}s")


def sync_experiment(host: str, token: str, exp_file: str) -> None:
    """推送单个 experiment.json 到 NAS。"""
    fpath = Path(exp_file)
    if not fpath.exists():
        print(f"错误: 文件不存在 {fpath}")
        sys.exit(1)

    exp_id = fpath.parent.name
    url = f"{host.rstrip('/')}/api/data/sync-experiment"
    headers = {"Authorization": f"Bearer {token}"}

    print(f"推送实验数据: {fpath.name} (id={exp_id})")

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
        print(f"[OK] experiment_id: {data['experiment_id']}")
        print(f"     写入路径: {data['path']}")
    else:
        print(f"[FAIL] HTTP {resp.status_code}")
        print(f"       {resp.text[:500]}")
        sys.exit(1)


def main():
    import argparse

    p = argparse.ArgumentParser(
        description="推送 market-data 到 NAS 面板",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例：
  %(prog)s --all                      # 推送全部数据（exports + store）
  %(prog)s                            # 只推送 exports/D1/
  %(prog)s --store                    # 只推送 store/daily/（品种目录）
  %(prog)s --files rb0 ag0 cu0        # 只推送指定品种
  %(prog)s --experiment path/to/exp.json  # 推送回测结果
        """,
    )
    p.add_argument("--all", action="store_true", help="推送全部数据（exports + store）")
    p.add_argument("--store", action="store_true", help="只推送 store/daily/（品种目录 parquet）")
    p.add_argument("--files", nargs="*", help="只推送指定品种（如 rb0 ag0 cu0）")
    p.add_argument("--host", default=PANEL_HOST, help=f"面板地址（默认：{PANEL_HOST}）")
    p.add_argument("--token", default=DEPLOY_TOKEN, help="DEPLOY_TOKEN（默认从 .env 读取）")
    p.add_argument("--subdir", default="D1", help="exports 子目录（D1/H2/H4，默认 D1）")
    p.add_argument("--experiment", help="推送单个 experiment.json 文件")
    args = p.parse_args()

    if not args.token:
        print("错误: 未设置 DEPLOY_TOKEN")
        print("请创建 market-data/.env 文件:")
        print("  PANEL_HOST=https://panel.darewin.icu")
        print("  DEPLOY_TOKEN=你的密钥")
        print("\n或在命令行指定: --token YOUR_TOKEN")
        sys.exit(1)

    if args.experiment:
        sync_experiment(args.host, args.token, args.experiment)
        return

    t0 = time.time()
    if args.all:
        sync_exports(args.host, args.token, None, args.subdir)
        sync_store(args.host, args.token)
    elif args.store:
        sync_store(args.host, args.token)
    else:
        sync_exports(args.host, args.token, args.files, args.subdir)

    elapsed = time.time() - t0
    print(f"\n总耗时: {elapsed:.1f}s")


if __name__ == "__main__":
    main()

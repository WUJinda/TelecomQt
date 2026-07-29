"""数据同步 API — 从开发电脑推送 market-data exports 到 NAS。

POST /api/data/sync
    接收一个或多个 JSON 文件（multipart/form-data），写入 market-data/exports/ 目录。
    用于「本地电脑跑 AKShare → 推送行情数据到 NAS」的自动化链路。

POST /api/data/sync-experiment
    接收单个 experiment.json 文件，写入 backend/data/experiments/ 目录。

安全：
    通过 DEPLOY_TOKEN 环境变量做 Bearer Token 验证。
    未设置 DEPLOY_TOKEN 时拒绝所有写入（默认安全）。
    公网已有 Cloudflare Access 邮箱鉴权作为第一道防线。
"""
from __future__ import annotations

import io
import json
import os
import shutil
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Header, HTTPException, UploadFile, File

# market-data exports 目录（与 symbols.py 同口径）
_MARKET_DATA_DIR = Path(os.environ.get(
    "MARKET_DATA_DIR",
    Path(__file__).resolve().parents[3] / "market-data",
))
_EXPORT_DIR = _MARKET_DATA_DIR / "exports"

# 实验数据目录
_EXPERIMENTS_DIR = Path(os.environ.get(
    "EXPERIMENTS_DIR",
    Path(__file__).resolve().parents[2] / "data" / "experiments",
))

# 安全：部署令牌（环境变量传入，未设置则拒绝写入）
_DEPLOY_TOKEN = os.environ.get("DEPLOY_TOKEN", "")

router = APIRouter()


def _verify_token(authorization: str | None):
    """验证 Bearer Token。DEPLOY_TOKEN 未设置时直接拒绝（默认安全）。"""
    if not _DEPLOY_TOKEN:
        raise HTTPException(
            status_code=403,
            detail="服务器未配置 DEPLOY_TOKEN，拒绝数据写入。"
                   "请在 docker-compose.yml 中设置 DEPLOY_TOKEN 环境变量。",
        )
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="缺少 Authorization 头")
    token = authorization.removeprefix("Bearer ").strip()
    if token != _DEPLOY_TOKEN:
        raise HTTPException(status_code=403, detail="Token 无效")


@router.post("/data/sync")
async def sync_market_data(
    files: list[UploadFile] = File(...),
    authorization: str | None = Header(None),
):
    """批量上传行情数据文件（JSON），写入 exports/ 目录。

    用法（curl 示例）：
        curl -X POST https://panel.darewin.icu/api/data/sync \\
          -H "Authorization: Bearer YOUR_TOKEN" \\
          -F "files=@exports/D1/rb0_kline.json" \\
          -F "files=@exports/D1/ag0_kline.json"

    文件名中包含子目录时会自动创建（如 D1/rb0_kline.json → exports/D1/rb0_kline.json）。
    """
    _verify_token(authorization)

    _EXPORT_DIR.mkdir(parents=True, exist_ok=True)

    uploaded = []
    errors = []

    for f in files:
        # 安全：只允许 .json 文件，防止路径穿越
        name = f.filename or ""
        if not name.endswith(".json"):
            errors.append({"file": name, "error": "只支持 .json 文件"})
            continue

        # 去掉路径前缀，只保留相对路径（防止 ../../etc/passwd 等穿越）
        safe_name = Path(name).name
        # 如果文件名本身包含子目录（如 D1/rb0_kline.json），保留最后一层目录
        parts = Path(name).parts
        if len(parts) > 1:
            # 最多保留一层子目录（D1/、H2/ 等）
            safe_name = str(Path(parts[-2]) / parts[-1])

        target = _EXPORT_DIR / safe_name
        target.parent.mkdir(parents=True, exist_ok=True)

        try:
            # 先写到临时文件，验证 JSON 合法后再移动到目标位置
            with tempfile.NamedTemporaryFile(
                mode="wb", suffix=".json", dir=str(_EXPORT_DIR), delete=False
            ) as tmp:
                content = await f.read()
                tmp.write(content)
                tmp_path = Path(tmp.name)

            # 验证 JSON 合法性
            with open(tmp_path, "r", encoding="utf-8") as jf:
                json.load(jf)  # 会抛异常如果不是合法 JSON

            # 验证通过，移动到目标位置
            shutil.move(str(tmp_path), str(target))
            uploaded.append(safe_name)

        except json.JSONDecodeError:
            errors.append({"file": name, "error": "不是合法的 JSON 文件"})
            tmp_path.unlink(missing_ok=True)
        except Exception as e:
            errors.append({"file": name, "error": str(e)})
            if 'tmp_path' in dir():
                tmp_path.unlink(missing_ok=True)

    return {
        "status": "ok" if not errors else "partial",
        "uploaded": uploaded,
        "errors": errors,
        "export_dir": str(_EXPORT_DIR),
        "timestamp": datetime.now().isoformat(),
    }


@router.post("/data/sync-zip")
async def sync_market_data_zip(
    file: UploadFile = File(...),
    authorization: str | None = Header(None),
):
    """上传一个 ZIP 文件，批量解压写入 exports/ 目录。

    适用于网络带宽有限的场景：客户端先将所有 JSON 压缩成 ZIP
    （8.9MB JSON ≈ 1MB ZIP），单次请求传完。

    用法（curl 示例）：
        # 客户端打包
        cd market-data/exports && zip -r /tmp/d1.zip D1/
        # 上传
        curl -X POST https://panel.darewin.icu/api/data/sync-zip \\
          -H "Authorization: Bearer YOUR_TOKEN" \\
          -F "file=@/tmp/d1.zip"
    """
    _verify_token(authorization)

    _EXPORT_DIR.mkdir(parents=True, exist_ok=True)

    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="空文件")

    uploaded = []
    errors = []

    try:
        zf = zipfile.ZipFile(io.BytesIO(content))
    except zipfile.BadZipFile:
        raise HTTPException(status_code=400, detail="不是合法的 ZIP 文件")

    for info in zf.infolist():
        if info.is_dir():
            continue
        name = info.filename
        if not name.endswith(".json"):
            errors.append({"file": name, "error": "只支持 .json 文件"})
            continue

        # 安全：去掉路径前缀，最多保留一层子目录
        parts = Path(name).parts
        safe_name = parts[-1]
        if len(parts) > 1:
            safe_name = str(Path(parts[-2]) / parts[-1])

        # 验证路径不会逃出 exports 目录
        target = (_EXPORT_DIR / safe_name).resolve()
        if not str(target).startswith(str(_EXPORT_DIR.resolve())):
            errors.append({"file": name, "error": "路径不合法"})
            continue

        try:
            raw = zf.read(info)
            data = json.loads(raw)  # 验证 JSON 合法性
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(raw)
            uploaded.append(safe_name)
        except json.JSONDecodeError:
            errors.append({"file": name, "error": "不是合法的 JSON"})
        except Exception as e:
            errors.append({"file": name, "error": str(e)})

    zf.close()

    return {
        "status": "ok" if not errors else "partial",
        "uploaded": uploaded,
        "errors": errors,
        "export_dir": str(_EXPORT_DIR),
        "timestamp": datetime.now().isoformat(),
    }


@router.post("/data/sync-experiment")
async def sync_experiment(
    file: UploadFile = File(...),
    experiment_id: str | None = None,
    authorization: str | None = Header(None),
):
    """上传单个 experiment.json 文件到实验数据目录。

    用法：
        curl -X POST https://panel.darewin.icu/api/data/sync-experiment \\
          -H "Authorization: Bearer YOUR_TOKEN" \\
          -F "file=@20260712_210518_test.json" \\
          -F "experiment_id=20260712_210518_test"

    如果不传 experiment_id，则使用文件名（去掉 .json 后缀）作为目录名。
    """
    _verify_token(authorization)

    name = file.filename or ""
    if not name.endswith(".json"):
        raise HTTPException(status_code=400, detail="只支持 .json 文件")

    # 确定 experiment_id（目录名）
    exp_id = experiment_id or Path(name).stem
    safe_id = Path(exp_id).name  # 防止路径穿越

    target_dir = _EXPERIMENTS_DIR / safe_id
    target_dir.mkdir(parents=True, exist_ok=True)
    target_file = target_dir / "experiment.json"

    content = await file.read()

    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="不是合法的 JSON 文件")

    with open(target_file, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    return {
        "status": "ok",
        "experiment_id": safe_id,
        "path": str(target_file),
        "timestamp": datetime.now().isoformat(),
    }


@router.get("/data/status")
def data_status(authorization: str | None = Header(None)):
    """查看当前 exports 目录下的文件清单（只读，不需要 token）。"""
    if not _EXPORT_DIR.exists():
        return {"exports": [], "total": 0, "note": "exports 目录不存在"}

    result = {}
    for sub in sorted(_EXPORT_DIR.iterdir()):
        if sub.is_dir():
            files = sorted(f.name for f in sub.glob("*.json"))
            result[sub.name] = files

    # exports 根目录下的散文件
    root_files = sorted(f.name for f in _EXPORT_DIR.glob("*.json"))
    if root_files:
        result["_root"] = root_files

    total = sum(len(v) for v in result.values())
    return {"exports": result, "total": total}

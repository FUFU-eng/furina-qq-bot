"""扫码绑定：群里发二维码 → 扫码后撤回 → 绑定通行证下全部游戏账号 → 开启自动签到 → 发账号卡片。

登录、取 Cookie、写库全部用 GsCore 自带的实现（mys_api / deal_ck / GsBind / gs_subscribe），
这里只是把它们串成一个对群友友好的流程。
"""
from __future__ import annotations

import copy
import json
import time
import uuid
import random
import asyncio
import hashlib
from copy import deepcopy
from typing import Dict, List, Tuple, Optional
from http.cookies import SimpleCookie

from gsuid_core.bot import Bot
from gsuid_core.logger import logger
from gsuid_core.models import Event
from gsuid_core.segment import MessageSegment
from gsuid_core.subscribe import gs_subscribe
from gsuid_core.utils.api.mys_api import mys_api
from gsuid_core.utils.api.mys.tools import get_web_ds_token
from gsuid_core.utils.database.models import GsBind, GsUser, Subscribe
from gsuid_core.utils.cookie_manager.add_ck import deal_ck
from gsuid_core.utils.cookie_manager.add_fp import deal_fp

from .cards import GAME_ORDER, Role, AccountCard, make_qr_png, render_qr_card, render_account_card
from .config import cfg, bot_name, CONFIG_PATH

ROLE_API = "https://api-takumi.mihoyo.com/binding/api/getUserGameRolesByCookie"
BIZ_TO_GAME = {"hk4e_cn": "gs", "hkrpg_cn": "sr", "nap_cn": "zzz", "bh3_cn": "bbb"}
RECORD_ID_TO_GAME = {2: "gs", 6: "sr", 8: "zzz", 1: "bbb"}
SIGN_TASK = {"gs": "[原神] 自动签到", "sr": "[星铁] 自动签到", "zzz": "[绝区零] 自动签到", "bbb": "[崩坏3] 自动签到"}

QR_TEXT = "诺，二维码发你，后续账号如果出现问题，跟本芙可没关系哦"
UNBOUND_TEXT = "芙芙这里没有你的数据哦，要先绑定账号"

QR_LIFETIME = 120  # 米游社扫码登录二维码本身约 2 分钟失效（之后查询返回 -3501）
QR_EXPIRED_CODE = -3501

_running: Dict[str, float] = {}
_healed: Dict[str, float] = {}

# 绑定成功后要在后台做的事（由 mys/__init__.py 注入：静默刷新面板）
ON_BOUND = None


def game_col(game: str) -> Optional[str]:
    """GsBind/GsUser 的 game_name 入参：原神用 None（与上游一致）。"""
    return None if game == "gs" else game


def sender_name(ev: Event) -> str:
    sender = getattr(ev, "sender", None) or {}
    return str(sender.get("card") or sender.get("nickname") or ev.user_id)


async def bound_uid(ev: Event, game: str) -> Optional[str]:
    uid = await GsBind.get_uid_by_game(ev.user_id, ev.bot_id, game_col(game))
    return str(uid) if uid else None


async def has_cookie(uid: str, game: str) -> bool:
    try:
        return bool(await mys_api.get_ck(uid, "OWNER", game_col(game)))
    except Exception:
        return False


def needs_stoken(uid: str, game: str) -> bool:
    """国服星铁 / 绝区零的实时便笺走米游社小组件接口，需要 SToken。"""
    if game == "sr":
        return uid[:1].isdigit() and int(uid[0]) <= 5
    if game == "zzz":
        return len(uid) < 10
    return False


async def has_stoken(uid: str, game: str) -> bool:
    try:
        return bool(await mys_api.get_stoken(uid, game_col(game)))
    except Exception:
        return False


async def any_binding(ev: Event) -> bool:
    for game in GAME_ORDER:
        if await bound_uid(ev, game):
            return True
    return False


# ---------------------------------------------------------------- 设备（防风控）
# 上游 generate_fake_fp 对所有账号都用同一台假手机（同型号、同 oaid、同指纹）去换设备指纹，
# 米游社就会给所有账号返回同一个 fp —— 在它看来是「一台手机挂了一堆账号」，于是频繁弹验证码。
# 这里沿用上游的 generate_fp / device_login_and_save，只是给每个米游社通行证分配一台
# 独立、固定不变的常见安卓机型（独立 device_id / oaid / 指纹），完全免费。
DEVICE_POOL = [
    # (型号 model, product, device, board, 指纹)
    ("23127PN0CC", "houji", "houji", "pineapple", "Xiaomi/houji/houji:14/UKQ1.230804.001/V816.0.{b}.0.UNCCNXM:user/release-keys"),
    ("2211133C", "fuxi", "fuxi", "kalama", "Xiaomi/fuxi/fuxi:14/UKQ1.230804.001/V816.0.{b}.0.UMCCNXM:user/release-keys"),
    ("23113RKC6C", "vermeer", "vermeer", "pineapple", "Redmi/vermeer/vermeer:14/UKQ1.230804.001/V816.0.{b}.0.UNKCNXM:user/release-keys"),
    ("23013RK75C", "mondrian", "mondrian", "taro", "Redmi/mondrian/mondrian:14/UKQ1.230804.001/V816.0.{b}.0.UMNCNXM:user/release-keys"),
    ("V2307A", "PD2307", "PD2307", "pineapple", "vivo/PD2307/PD2307:14/UP1A.231005.007/compiler1{b}1535:user/release-keys"),
    ("PJD110", "PJD110", "OP5929L1", "pineapple", "OnePlus/PJD110/OP5929L1:14/UKQ1.230924.001/T.R4T2.1{b}c8b3e:user/release-keys"),
    ("PJE110", "PJE110", "OP5D06L1", "kalama", "OnePlus/PJE110/OP5D06L1:14/UKQ1.230924.001/T.R4T3.1{b}a9f20:user/release-keys"),
]
# 上游 generate_fp 上报的硬件固定是高通（hardware=qcom）。天玑芯片的机型（vivo PD2309 / OPPO PHZ110）
# 和上报信息自相矛盾，实测分到这两台的通行证全部被米游社打上 10035 人机验证；荣耀那台主板名也不像真机。
# 这几台已从机型池去掉，之前分到它们的通行证会自动换一台高通机型重新登记。
BAD_PROFILES = ("vivo/PD2309/", "OPPO/PHZ110/", "HONOR/BVL-AN00/")


def _is_bad(rec: Optional[Dict]) -> bool:
    return bool(rec) and str(rec.get("device_info", "")).startswith(BAD_PROFILES)


# ---------------------------------------------------------------- 设备指纹 v2
# 上游 generate_fp 上报给米游社的设备环境里写死了 isRoot=1（已 root）、debugStatus=1（开着调试）、
# 米游社版本 2.20.2（几年前的旧版）、主机名固定是一加的编译服务器（小米 / vivo 机型也这么报），
# 传感器、内存等随机数每次都变——这些都是风控最在意的信号。这里按机型生成一份自洽、固定不变的环境：
# 未 root、未调试、当前米游社版本、对应品牌的编译信息、同一台设备每次上报都一样。
FP_VERSION = 2
MODEL_META = {
    # model: (市场名, 分辨率, 运存GB, 存储GB, 编译主机, 编译用户)
    "23127PN0CC": ("Xiaomi 14", "1200x2670", 16, 512, "pangu-build-component-system-208787-x4fk6-nnc4j-ldx4v", "builder"),
    "2211133C": ("Xiaomi 13", "1080x2400", 12, 256, "pangu-build-component-system-197614-2vxhs-gmz2c-kzf5t", "builder"),
    "23113RKC6C": ("Redmi K70", "1440x3200", 16, 512, "pangu-build-component-system-203357-8wq6p-k7r4m-bt2zc", "builder"),
    "23013RK75C": ("Redmi K60", "1440x3200", 12, 256, "pangu-build-component-system-191040-l2m9d-c8vvx-5sd7q", "builder"),
    "V2307A": ("iQOO 12", "1260x2800", 16, 512, "compiler231211152357", "compiler"),
    "PJD110": ("OnePlus 12", "1440x3168", 16, 512, "dg02-pool03-kvm97", "root"),
    "PJE110": ("OnePlus Ace 3", "1264x2780", 16, 512, "dg02-pool06-kvm82", "root"),
}


def _stable(seed: str, lo: int, hi: int) -> int:
    return lo + int(hashlib.sha256(seed.encode()).hexdigest()[:8], 16) % (hi - lo + 1)


def _ext_fields(prof: Dict[str, str]) -> str:
    model = prof["model"]
    name, screen, ram_gb, rom_gb, host, user = MODEL_META.get(
        model, (model, "1080x2400", 12, 256, "localhost", "builder"))
    seed = f"furina-fp:{prof['oaid']}"
    brand = prof["device_info"].split("/")[0]
    build_id = prof["device_info"].split("/")[3] if prof["device_info"].count("/") >= 3 else "UKQ1.230804.001"
    install_ms = int(time.time() * 1000) - _stable(seed + "inst", 20, 400) * 86400000
    ram_total = ram_gb * 1024 * 1024 // 64  # 与真机抓包同量级的单位
    data = {
        "proxyStatus": 0, "isRoot": 0, "romCapacity": str(rom_gb), "deviceName": name,
        "productName": prof["product"], "romRemain": str(_stable(seed + "rom", rom_gb // 4, rom_gb * 3 // 4)),
        "hostname": host, "screenSize": screen, "isTablet": 0,
        "aaid": str(uuid.UUID(hashlib.md5((seed + "aaid").encode()).hexdigest())),
        "model": model, "brand": brand, "hardware": "qcom", "deviceType": prof["device"], "devId": "REL",
        "serialNumber": "unknown", "sdCapacity": ram_total * 2, "buildTime": str(_stable(seed + "bt", 1700000000, 1715000000) * 1000),
        "buildUser": user, "simState": 5, "ramRemain": str(ram_total * _stable(seed + "rr", 30, 55) // 100),
        "appUpdateTimeDiff": install_ms, "deviceInfo": prof["device_info"],
        "vaid": str(uuid.UUID(hashlib.md5((seed + "vaid").encode()).hexdigest())), "buildType": "user",
        "sdkVersion": "34", "ui_mode": "UI_MODE_TYPE_NORMAL", "isMockLocation": 0, "cpuType": "arm64-v8a",
        "isAirMode": 0, "ringMode": 2, "chargeStatus": _stable(seed + "cs", 0, 1),
        "manufacturer": "Xiaomi" if brand in ("Xiaomi", "Redmi") else brand, "emulatorStatus": 0, "appMemory": "512",
        "osVersion": "14", "vendor": ("中国移动", "中国联通", "中国电信")[_stable(seed + "v", 0, 2)],
        "accelerometer": f"{_stable(seed + 'ax', 1, 60) / 100:.6f}x{_stable(seed + 'ay', 1, 90) / 100:.6f}x9.{_stable(seed + 'az', 700000, 819999)}",
        "sdRemain": ram_total, "buildTags": "release-keys", "packageName": "com.mihoyo.hyperion",
        "networkType": "WiFi", "oaid": prof["oaid"], "debugStatus": 0, "ramCapacity": str(ram_total),
        "magnetometer": f"{_stable(seed + 'mx', 100, 400) / 10:.4f}x-{_stable(seed + 'my', 100, 500) / 10:.4f}x-{_stable(seed + 'mz', 100, 400) / 10:.4f}",
        "display": f"{build_id} release-keys", "appInstallTimeDiff": str(install_ms),
        "packageVersion": str(mys_api.mysVersion),
        "gyroscope": f"0.00{_stable(seed + 'gx', 1, 9)}x-0.00{_stable(seed + 'gy', 1, 9)}x0.00{_stable(seed + 'gz', 1, 9)}",
        "batteryStatus": _stable(seed + str(int(time.time() // 3600)), 35, 95), "hasKeyboard": 0, "board": prof["board"],
    }
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"))


async def furina_fp(device_id: str, prof: Dict[str, str]) -> str:
    """用自洽的设备环境向米游社换设备指纹；接口拒收时退回上游实现。"""
    from gsuid_core.utils.api.mys.api import GET_FP

    seed_id, seed_time = mys_api.get_seed()
    body = {
        "device_id": hashlib.sha256(f"furina-aid:{prof['oaid']}".encode()).hexdigest()[:16],
        "seed_id": seed_id, "platform": "2", "seed_time": seed_time, "ext_fields": _ext_fields(prof),
        "app_name": "bbs_cn", "bbs_device_id": device_id, "device_fp": mys_api.generate_random_fp(),
    }
    try:
        res = await mys_api._mys_request(url=GET_FP.get(), method="POST", header=copy.deepcopy(mys_api._HEADER), data=body)
        if isinstance(res, dict) and (res.get("data") or {}).get("code") == 200 and res["data"].get("device_fp"):
            return res["data"]["device_fp"]
        logger.warning(f"[FurinaMys] 设备指纹接口未接受 v2 环境: {str(res)[:120]}")
    except Exception as exc:
        logger.warning(f"[FurinaMys] 设备指纹 v2 获取失败: {exc}")
    return await mys_api.generate_fp(
        device_id, prof["model"], prof["product"], prof["device"], prof["board"],
        prof["oaid"], prof["device_info"], seed_id, seed_time,
    )


def _profile_for(mid: str, device_info: str) -> Dict[str, str]:
    """沿用这台设备原来的机型（同一个 device_id 不能突然变成别的手机）；机型不在池里才重新挑。"""
    prof = _vdev_profile(mid)
    parts = device_info.split("/")
    for model, product, device, board, _fingerprint in DEVICE_POOL:
        if len(parts) > 2 and parts[1] == product:
            prof.update({"model": model, "product": product, "device": device, "board": board, "device_info": device_info})
            break
    return prof


async def _upgrade_fp(mid: str, rec: Dict, vdevs: Dict[str, Dict]) -> Dict:
    """旧版指纹（上游环境）升级到 v2：保留 device_id，只换指纹（真机更新米游社后也会这样换）。"""
    prof = _profile_for(mid, str(rec.get("device_info", "")))
    rec = dict(rec)
    rec.update({"fp": await furina_fp(rec["device_id"], prof), "device_info": prof["device_info"],
                "fpv": FP_VERSION, "at": int(time.time())})
    vdevs[mid] = rec
    _vdev_save(vdevs)
    logger.info(f"[FurinaMys] 通行证 {mid[:2]}***{mid[-2:]} 设备指纹已升级到 v2 ({prof['model']}) fp={rec['fp'][:4]}…")
    return rec
VDEV_PATH = CONFIG_PATH.parent / "virtual_devices.json"


def _vdev_load() -> Dict[str, Dict]:
    try:
        return json.loads(VDEV_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _vdev_save(data: Dict[str, Dict]) -> None:
    try:
        VDEV_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = VDEV_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(VDEV_PATH)
    except Exception as exc:
        logger.warning(f"[FurinaMys] 保存独立设备记录失败: {exc}")


def _vdev_profile(mys_id: str) -> Dict[str, str]:
    """按通行证 ID 固定挑一台机型并生成独立的 oaid / 指纹（同一个人每次都一样）。"""
    h = hashlib.sha256(f"furina-vdev:{mys_id}".encode()).hexdigest()
    model, product, device, board, fingerprint = DEVICE_POOL[int(h[:8], 16) % len(DEVICE_POOL)]
    return {
        "model": model, "product": product, "device": device, "board": board,
        "oaid": h[8:24], "device_info": fingerprint.format(b=int(h[24:28], 16) % 9 + 1),
    }


async def device_bound(uid: str, game: str) -> bool:
    """是否已经用「绑定设备」导入过真实手机设备信息（芙芙分配的独立设备不算）。"""
    try:
        info = await GsUser.get_user_attr_by_uid(uid, "device_info", game_col(game))
        if not info:
            return False
        return not any(v.get("device_info") == info for k, v in _vdev_load().items() if not k.startswith("real:"))
    except Exception:
        return False


def _app_cookie(user) -> Optional[str]:
    if user.stoken:
        return user.stoken
    try:
        simp = SimpleCookie(user.cookie or "")
        sk = simp["stoken_v2"].value if "stoken_v2" in simp else (simp["stoken"].value if "stoken" in simp else "")
        if sk and user.mys_id:
            mid = simp["mid"].value if "mid" in simp else ""
            return f"stuid={user.mys_id};stoken={sk};mid={mid}"
    except Exception:
        pass
    return None


async def _rows_of(mys_id: str):
    try:
        users = await GsUser.get_all_user(without_error=False)
    except Exception:
        return []
    return [u for u in users if str(u.mys_id or "") == str(mys_id)]


async def ensure_own_device(mys_id: str, relogin: bool = False) -> bool:
    """给这个通行证配一台独立设备并向米游社登记（已导入真实设备的不动）。返回是否处理过。"""
    if not mys_id:
        return False
    rows = await _rows_of(mys_id)
    if not rows:
        return False
    vdevs = _vdev_load()
    real = vdevs.get(f"real:{mys_id}")
    if real and any(r.device_info == real.get("device_info") for r in rows):
        # 用户自己绑定的真实设备：上游重新登录（扫码 / 刷新CK）时会换掉 device_id，这里还原回去
        if all(r.device_id == real["device_id"] and r.fp == real["fp"] for r in rows) and not relogin:
            return False
        rec = real
    else:
        rec = vdevs.get(str(mys_id))
        # 用户自己导入过、但没有记录的真实设备：完全不碰
        if any(r.device_info and not (rec and r.device_info == rec.get("device_info")) for r in rows):
            return False
        if _is_bad(rec):
            logger.info(f"[FurinaMys] 通行证 {str(mys_id)[:2]}***{str(mys_id)[-2:]} 的独立设备机型有问题，换一台重新登记")
            vdevs.pop(str(mys_id), None)
            rec, relogin = None, True
        elif rec and rec.get("fpv", 1) < FP_VERSION:
            rec, relogin = await _upgrade_fp(str(mys_id), rec, vdevs), True
    if rec and all(r.device_id == rec["device_id"] and r.fp == rec["fp"] and r.device_info for r in rows) and not relogin:
        return False
    cookie = next((c for c in (_app_cookie(r) for r in rows) if c), None) or rows[0].cookie
    if not cookie:
        return False
    if not rec:
        prof = _vdev_profile(str(mys_id))
        device_id = str(uuid.uuid4()).lower()
        fp = await furina_fp(device_id, prof)
        rec = {"device_id": device_id, "fp": fp, "device_info": prof["device_info"], "fpv": FP_VERSION, "at": int(time.time())}
        vdevs[str(mys_id)] = rec
        _vdev_save(vdevs)
    for r in rows:
        await GsUser.update_data_by_data(
            {"id": r.id}, {"fp": rec["fp"], "device_id": rec["device_id"], "device_info": rec["device_info"]}
        )
    try:
        await mys_api.device_login_and_save(rec["device_id"], rec["fp"], rec["device_info"], cookie)
    except Exception as exc:
        logger.warning(f"[FurinaMys] 独立设备登记失败 {mys_id}: {exc}")
        return False
    logger.info(f"[FurinaMys] 通行证 {str(mys_id)[:2]}***{str(mys_id)[-2:]} 已使用独立设备 {rec['device_info'].split('/')[0]} fp={rec['fp'][:4]}…")
    return True


async def migrate_shared_devices() -> int:
    """启动时：把还在共用上游假设备指纹的老账号，全部换成各自独立的设备。"""
    try:
        users = await GsUser.get_all_user(without_error=False)
    except Exception as exc:
        logger.warning(f"[FurinaMys] 读取账号失败，跳过设备迁移: {exc}")
        return 0
    done, seen = 0, set()
    for u in users:
        mid = str(u.mys_id or "")
        if not mid or mid in seen:
            continue
        seen.add(mid)
        try:
            vdevs = _vdev_load()
            if u.device_info and mid not in vdevs and f"real:{mid}" not in vdevs and u.device_id and u.fp:
                # 之前已经用「绑定设备」导入的真实手机：记下来，以后上游重新登录换掉 device_id 时能还原
                vdevs[f"real:{mid}"] = {"device_id": u.device_id, "fp": u.fp, "device_info": u.device_info, "at": int(time.time())}
                _vdev_save(vdevs)
                continue
            if await ensure_own_device(mid):
                done += 1
                await asyncio.sleep(random.uniform(2, 4))
        except Exception as exc:
            logger.warning(f"[FurinaMys] 设备迁移失败 {mid[:2]}***: {exc}")
    if done:
        logger.info(f"[FurinaMys] 已为 {done} 个通行证换上独立设备（防验证码）")
    return done


def _cookie_value(cookie: str, name: str) -> str:
    for part in (cookie or "").split(";"):
        k, _, v = part.strip().partition("=")
        if k == name:
            return v
    return ""


_orig_new_device = None


async def _same_device_login(self, uid: str, app_cookie: Optional[str] = None, game_name: Optional[str] = None):
    """替换上游 ck_in_new_device。

    上游每次扫码绑定 / 刷新 CK，都会随机生成一个全新的 device_id，再拿旧的指纹去登录。
    结果是：同一个指纹对应一串不同的设备、账号名下的登录设备越堆越多——群友越是「解绑重绑」，
    米游社越觉得这个号可疑（10035 就是这么被加重的）。这里改成始终用这个通行证自己固定的那台设备登录。
    """
    try:
        mid = _cookie_value(app_cookie or "", "stuid")
        if not mid or not app_cookie:
            return await _orig_new_device(self, uid, app_cookie, game_name)
        vdevs = _vdev_load()
        rec = vdevs.get(f"real:{mid}") or vdevs.get(mid)
        if rec is None or (_is_bad(rec) and f"real:{mid}" not in vdevs):
            prof = _vdev_profile(mid)
            device_id = str(uuid.uuid4()).lower()
            fp = await furina_fp(device_id, prof)
            rec = {"device_id": device_id, "fp": fp, "device_info": prof["device_info"], "fpv": FP_VERSION, "at": int(time.time())}
            vdevs[mid] = rec
            _vdev_save(vdevs)
        elif f"real:{mid}" not in vdevs and rec.get("fpv", 1) < FP_VERSION:
            rec = await _upgrade_fp(mid, rec, vdevs)
        seed_id, seed_time = self.get_seed()
        await self.device_login_and_save(rec["device_id"], rec["fp"], rec["device_info"], app_cookie)
        if await GsUser.user_exists(uid, game_name):
            await GsUser.update_data_by_uid_without_bot_id(
                uid, game_name, fp=rec["fp"], device_id=rec["device_id"], device_info=rec["device_info"]
            )
        logger.info(f"[FurinaMys] 通行证 {mid[:2]}***{mid[-2:]} 登录沿用自己的固定设备（不再每次生成新设备）")
        return rec["fp"], rec["device_id"], seed_id, seed_time
    except Exception as exc:
        logger.warning(f"[FurinaMys] 固定设备登录失败，改用上游默认流程: {exc}")
        return await _orig_new_device(self, uid, app_cookie, game_name)


def install_device_patch() -> None:
    global _orig_new_device
    try:
        from gsuid_core.utils.api.mys.base_request import BaseMysApi
    except Exception as exc:
        logger.warning(f"[FurinaMys] 找不到上游设备登录函数，跳过: {exc}")
        return
    current = BaseMysApi.ck_in_new_device
    if getattr(current, "_furina", False):
        return
    _orig_new_device = current
    _same_device_login._furina = True  # type: ignore[attr-defined]
    BaseMysApi.ck_in_new_device = _same_device_login


install_device_patch()


async def heal_device(uid: str, game: str) -> bool:
    """遇到验证码时：没有设备的分配独立设备；已有芙芙分配的设备就用它重新登记一次。

    用户自己绑定的真实设备不动；同一账号 30 分钟内最多一次。
    """
    key = f"{game}:{uid}"
    if _healed.get(key, 0) > time.monotonic():
        return False
    _healed[key] = time.monotonic() + 1800
    try:
        if await device_bound(uid, game):
            return False
        mys_id = await GsUser.get_user_attr_by_uid(uid, "mys_id", game_col(game))
        result = await ensure_own_device(str(mys_id or ""), relogin=True)
        logger.info(f"[FurinaMys] {game} {uid} 遇到验证码，已重新登记独立设备: {result}")
        return result
    except Exception as exc:
        logger.warning(f"[FurinaMys] 设备登录失败 {game} {uid}: {exc}")
        return False


async def refresh_cookie(ev: Event) -> bool:
    """Cookie 失效但 SToken 还在时，用上游「刷新CK」换一个新的 cookie_token，免得让人重新扫码。"""
    try:
        from gsuid_core.utils.cookie_manager.add_ck import get_ck_by_stoken

        msg = await get_ck_by_stoken(ev.bot_id, ev.user_id)
        logger.info(f"[FurinaMys] {ev.user_id} 用 SToken 刷新 Cookie: {str(msg)[:80]}")
        await reapply_devices(ev)
        return True
    except Exception as exc:
        logger.warning(f"[FurinaMys] 刷新 Cookie 失败: {exc}")
        return False


async def bind_device(ev: Event, data: Dict) -> int:
    """与上游「mys设备登录 / 绑定设备」相同：写入设备指纹并向米游社登记该设备。返回处理的账号数。"""
    fp, device_id, device_info = await deal_fp(data)
    users = await GsUser.select_data_list(ev.user_id, ev.bot_id) or []
    count = 0
    for user in users:
        if not user.cookie:
            continue
        await GsUser.update_data_by_data(
            {"uid": user.uid} if user.uid else {"mys_id": user.mys_id},
            {"fp": fp, "device_id": device_id, "device_info": device_info},
        )
        await mys_api.device_login_and_save(device_id, fp, device_info, user.cookie)
        if user.mys_id:
            vdevs = _vdev_load()
            vdevs[f"real:{user.mys_id}"] = {"device_id": device_id, "fp": fp, "device_info": device_info, "at": int(time.time())}
            _vdev_save(vdevs)
        count += 1
    return count


async def reapply_devices(ev: Event) -> None:
    """上游每次重新登录都会换一个随机 device_id；登录后把这个人的设备身份还原/补上。"""
    try:
        users = await GsUser.select_data_list(user_id=ev.user_id, bot_id=ev.bot_id) or []
    except Exception:
        users = []
    for mid in {str(u.mys_id) for u in users if u.mys_id}:
        try:
            await ensure_own_device(mid)
        except Exception as exc:
            logger.warning(f"[FurinaMys] 还原设备失败: {exc}")


# ---------------------------------------------------------------- 解除绑定
async def unbind_all(ev: Event) -> List[Role]:
    """删除该 QQ 名下全部游戏 UID、登录凭证和自动签到（都用上游数据库方法）。返回被解绑的账号。"""
    removed: List[Role] = []
    for game in GAME_ORDER:
        uids = [u for u in (await GsBind.get_uid_list_by_game(ev.user_id, ev.bot_id, game_col(game)) or []) if u]
        for uid in uids:
            try:
                await Subscribe.delete_row(task_name=SIGN_TASK[game], uid=str(uid))
            except Exception as exc:
                logger.warning(f"[FurinaMys] 删除签到订阅失败 {game} {uid}: {exc}")
            await GsBind.delete_uid(ev.user_id, ev.bot_id, str(uid), game_col(game))
            removed.append(Role(game, str(uid)))
    try:
        await GsUser.delete_row(user_id=ev.user_id, bot_id=ev.bot_id)
    except Exception as exc:
        logger.warning(f"[FurinaMys] 删除登录凭证失败: {exc}")
    return removed


# ---------------------------------------------------------------- 角色列表
async def fetch_roles(account_id: str, cookie_token: str) -> List[Role]:
    """取通行证下全部游戏角色。优先「我的角色」接口，失败时退回米游社战绩卡片接口。"""
    roles: List[Role] = []
    header = deepcopy(mys_api._HEADER)
    header["Cookie"] = f"account_id={account_id};cookie_token={cookie_token}"
    header["DS"] = get_web_ds_token(True)
    try:
        data = await mys_api._mys_request(ROLE_API, "GET", header=header)
        if isinstance(data, dict):
            for item in (data.get("data") or {}).get("list") or []:
                game = BIZ_TO_GAME.get(str(item.get("game_biz")))
                if game and item.get("game_uid"):
                    roles.append(Role(game, str(item["game_uid"]), str(item.get("nickname") or ""),
                                      _int(item.get("level")), str(item.get("region_name") or "")))
        else:
            logger.warning(f"[FurinaMys] 角色列表接口返回 {data}")
    except Exception as exc:
        logger.warning(f"[FurinaMys] 角色列表接口异常: {exc}")
    if roles:
        return roles
    try:
        data = await mys_api.get_mihoyo_bbs_info(account_id, f"account_id={account_id};cookie_token={cookie_token}")
        if isinstance(data, list):
            for item in data:
                game = RECORD_ID_TO_GAME.get(_int(item.get("game_id")) or -1)
                if game and item.get("game_role_id"):
                    roles.append(Role(game, str(item["game_role_id"]), str(item.get("nickname") or ""),
                                      _int(item.get("level")), str(item.get("region_name") or "")))
    except Exception as exc:
        logger.warning(f"[FurinaMys] 战绩卡片接口异常: {exc}")
    return roles


def _int(value) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------- 扫码
async def _poll(code: Dict, timeout: int, on_scanned) -> Tuple[str, Optional[Dict]]:
    """返回 (状态, 登录数据)。状态：confirmed / expired / canceled / timeout。"""
    deadline = time.monotonic() + timeout
    scanned = False
    errors = 0
    while time.monotonic() < deadline:
        await asyncio.sleep(2)
        status = await mys_api.check_hyp_qrcode(code["ticket"], code["device_id"])
        if isinstance(status, int):
            if status == QR_EXPIRED_CODE:
                return ("scanned_expired" if scanned else "expired"), None
            # 偶发网络错误不立刻判死；连续 3 次失败才当作过期/取消
            errors += 1
            logger.info(f"[FurinaMys] 二维码状态查询返回 {status}（第 {errors} 次）")
            if errors >= 3:
                return ("scanned_expired" if scanned else "expired"), None
            continue
        errors = 0
        state = status.get("status")
        if state == "Created":
            continue
        if state == "Scanned":
            if not scanned:
                scanned = True
                await on_scanned()
            continue
        if state == "Confirmed":
            if not scanned:
                await on_scanned()
            return "confirmed", dict(status)
        return "canceled", None
    return ("scanned_expired" if scanned else "timeout"), None


async def _login_cookie(login: Dict) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """把扫码结果换成 GsCore 认识的 Cookie 字符串（同 GsCore qrcode_login）。返回 (ck, account_id, cookie_token)。"""
    tokens = login.get("tokens") or []
    info = login.get("user_info") or {}
    account_id = str(info.get("aid") or info.get("uid") or info.get("account_id") or "")
    mid = info.get("mid")
    if not tokens or not mid or not account_id:
        return None, None, None
    stoken = next((t["token"] for t in tokens if t.get("name") in {"stoken", "stoken_v2"}), tokens[0]["token"])
    app_cookie = f"stuid={account_id};stoken={stoken};mid={mid}"
    ck = await mys_api.get_cookie_token_by_stoken(stoken, account_id, app_cookie)
    if isinstance(ck, int):
        logger.warning(f"[FurinaMys] 获取 cookie_token 失败: {ck}")
        return None, account_id, None
    cookie = SimpleCookie(
        {"stoken_v2": stoken, "stuid": account_id, "mid": mid, "cookie_token": ck["cookie_token"]}
    ).output(header="", sep=";")
    return cookie, account_id, ck["cookie_token"]


async def _bind_roles(ev: Event, roles: List[Role]) -> None:
    """按游戏写入 GsBind，并把等级最高的账号设为当前账号。"""
    for game in GAME_ORDER:
        mine = sorted((r for r in roles if r.game == game), key=lambda r: -(r.level or 0))
        if not mine:
            continue
        for r in mine:
            ret = await GsBind.insert_uid(ev.user_id, ev.bot_id, r.uid, ev.group_id, is_digit=True, game_name=game_col(game))
            logger.info(f"[FurinaMys] 绑定 {game} {r.uid}: {ret}")
        await GsBind.switch_uid_by_game(ev.user_id, ev.bot_id, mine[0].uid, game_col(game))
        mine[0].main = True


async def enable_auto_sign(ev: Event, game: str, uid: str) -> bool:
    """与上游「开启自动签到」相同的写法；结果改为私聊用户（不在群里刷屏，群里由 FurinaSign 发战报）。"""
    task = SIGN_TASK[game]
    sub_ev = copy.copy(ev)
    sub_ev.user_type = "direct"
    sub_ev.group_id = None
    try:
        if await gs_subscribe.get_subscribe(task, uid=uid):
            await Subscribe.update_data_by_data(
                {"task_name": task, "uid": uid},
                {"user_id": sub_ev.user_id, "bot_id": sub_ev.bot_id, "group_id": None, "bot_self_id": sub_ev.bot_self_id,
                 "user_type": "direct", "WS_BOT_ID": sub_ev.WS_BOT_ID},
            )
        else:
            await gs_subscribe.add_subscribe("single", task, sub_ev, uid=uid)
        return True
    except Exception as exc:
        logger.exception(f"[FurinaMys] 开启 {task} 失败: {exc}")
        return False


async def sign_enabled(game: str, uid: str) -> bool:
    try:
        return bool(await gs_subscribe.get_subscribe(SIGN_TASK[game], uid=uid))
    except Exception:
        return False


async def bound_roles(ev: Event) -> Tuple[List[Role], str]:
    """当前用户已绑定的全部账号（尽量用 Cookie 补全昵称和等级）。"""
    roles: List[Role] = []
    for game in GAME_ORDER:
        uids = await GsBind.get_uid_list_by_game(ev.user_id, ev.bot_id, game_col(game)) or []
        for i, uid in enumerate(u for u in uids if u):
            roles.append(Role(game, str(uid), main=(i == 0)))
    mys_id = ""
    try:
        users = await GsUser.select_data_list(user_id=ev.user_id, bot_id=ev.bot_id) or []
    except Exception:
        users = []
    info: Dict[Tuple[str, str], Role] = {}
    for user in users:
        if not (user.cookie and user.mys_id):
            continue
        mys_id = str(user.mys_id)
        simp = SimpleCookie(user.cookie)
        token = simp["cookie_token"].value if "cookie_token" in simp else ""
        if token:
            info.update({(r.game, r.uid): r for r in await fetch_roles(mys_id, token)})
    for r in roles:
        live = info.get((r.game, r.uid))
        if live:
            r.nickname, r.level, r.region = live.nickname, live.level, live.region
    for r in roles:
        r.has_ck = await has_cookie(r.uid, r.game)
        r.sign = await sign_enabled(r.game, r.uid)
    return roles, mys_id


async def _run_on_bound(bot: Bot, ev: Event) -> None:
    try:
        await ON_BOUND(bot, ev)
    except Exception as exc:
        logger.warning(f"[FurinaMys] 绑定后刷新面板失败: {exc}")


async def start_bind(bot: Bot, ev: Event, unbound: bool = False, after=None, lead: Optional[str] = None) -> None:
    """完整的扫码绑定流程。after: 绑定成功后要继续执行的协程工厂（例如原本想查的东西）；lead: 二维码前的提示语。"""
    key = f"{ev.bot_id}:{ev.user_id}"
    if _running.get(key, 0) > time.monotonic():
        return await bot.send("二维码已经发你啦，用米游社扫一下就好～")
    timeout = max(60, min(int(cfg("QrTimeout") or QR_LIFETIME), QR_LIFETIME))
    _running[key] = time.monotonic() + timeout + 30
    try:
        code = await mys_api.create_hyp_qrcode_url()
        if isinstance(code, int):
            return await bot.send(f"米游社那边有点忙（{code}），过一会儿再试试吧～")
        card = await render_qr_card(make_qr_png(code["url"]), sender_name(ev), timeout, bot_name())
        lead = lead or (UNBOUND_TEXT if unbound else "")
        text = (lead + "\n" if lead else "") + QR_TEXT
        head = [MessageSegment.at(ev.user_id)] if ev.group_id else []
        ids = await bot.send(head + [MessageSegment.text(f" {text}" if head else text), MessageSegment.image(card)], wait_recall=True)

        recalled = False

        async def recall():
            nonlocal recalled
            if not recalled and ids:
                recalled = True
                try:
                    await bot.unsend(ids)
                except Exception as exc:
                    logger.warning(f"[FurinaMys] 撤回二维码失败: {exc}")

        async def on_scanned():
            await recall()
            try:
                await bot.send("扫到啦～请在米游社里点「确认登录」，芙芙马上帮你绑好")
            except Exception:
                pass

        state, login = await _poll(code, timeout, on_scanned)
        await recall()
        if state != "confirmed" or not login:
            if state == "canceled":
                return await bot.send("登录被取消啦，想绑定的话再 @芙芙 绑定账号 就好～")
            if state == "scanned_expired":
                return await bot.send("二维码被扫了，但一直没在米游社里点「确认登录」，已经失效啦～\n"
                                      "（注意要扫自己那张哦）再 @芙芙 绑定账号 重新拿一张就好")
            return await bot.send(f"二维码 {timeout // 60} 分钟没被扫，已经失效啦～想绑定的话再 @芙芙 绑定账号 重新拿一张")

        cookie, account_id, cookie_token = await _login_cookie(login)
        if not cookie or not account_id or not cookie_token:
            return await bot.send("扫码成功了，但米游社没给登录凭证……稍后再 @芙芙 绑定账号 试一次吧")
        roles = await fetch_roles(account_id, cookie_token)
        if not roles:
            return await bot.send("这个米哈游通行证下没有找到原神 / 星铁 / 绝区零 / 崩坏3 的国服账号哦")

        await _bind_roles(ev, roles)
        msg, ok = await deal_ck(ev.bot_id, cookie, ev.user_id, mode="TEXT")
        logger.info(f"[FurinaMys] deal_ck 状态 {ok}")
        if not ok:
            logger.warning(f"[FurinaMys] deal_ck 失败: {str(msg)[:120]}")
        else:
            await reapply_devices(ev)

        auto_sign = bool(cfg("AutoSignOnBind"))
        for r in roles:
            r.has_ck = await has_cookie(r.uid, r.game)
            if auto_sign and r.has_ck:
                r.sign = await enable_auto_sign(ev, r.game, r.uid)
        image = await render_account_card(
            AccountCard(sender_name(ev), ev.user_id, roles, account_id, "绑定成功",
                        f"{sender_name(ev)} 的 {len(roles)} 个游戏账号已绑定" + ("，每日自动签到已开启" if auto_sign else "")),
            bot_name(),
        )
        await bot.send([MessageSegment.text("绑定好啦～这是你名下的全部游戏账号："), MessageSegment.image(image)])
        if after is not None:
            try:
                await after()
            except Exception as exc:
                logger.exception(f"[FurinaMys] 绑定后续查询失败: {exc}")
        if cfg("RefreshOnBind") and ON_BOUND is not None:
            asyncio.create_task(_run_on_bound(bot, ev))
    finally:
        _running.pop(key, None)

from __future__ import annotations

import datetime as dt
import hashlib
import ipaddress
import os
import stat

import pytest
from cryptography import x509
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.x509.oid import ExtendedKeyUsageOID

from gobot import hardware, netinfo
from gobot.auth import TokenAuth, cookie_value, is_loopback, load_or_create_token
from gobot.bench.benchmark import LINE_RE, select
from gobot.bench.positions import positions
from gobot.certs import ensure_certs
from gobot.engine.config import default_engine_settings, render_config
from gobot.engine.models import NetInfo, download


def _load(p):
    return x509.load_pem_x509_certificate(p.read_bytes())


def test_certs_san_chain_and_ip_change(tmp_path):
    paths = ensure_certs(tmp_path, "192.168.1.23")
    ca, cert = _load(paths["ca_pem"]), _load(paths["cert"])
    san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    assert ipaddress.ip_address("192.168.1.23") in san.get_values_for_type(x509.IPAddress)
    assert "localhost" in san.get_values_for_type(x509.DNSName)
    eku = cert.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value
    assert ExtendedKeyUsageOID.SERVER_AUTH in eku
    assert cert.not_valid_after_utc - cert.not_valid_before_utc <= dt.timedelta(days=398)
    assert ca.extensions.get_extension_for_class(x509.BasicConstraints).value.ca is True
    # chain: server cert signed by the CA
    ca.public_key().verify(
        cert.signature, cert.tbs_certificate_bytes, padding.PKCS1v15(), cert.signature_hash_algorithm
    )
    assert stat.S_IMODE(os.stat(paths["key"]).st_mode) == 0o600
    assert stat.S_IMODE(os.stat(tmp_path / "ca.key").st_mode) == 0o600
    # same IP -> no reissue; new IP -> new server cert from the SAME CA (phone keeps trusting it)
    serial = cert.serial_number
    ensure_certs(tmp_path, "192.168.1.23")
    assert _load(paths["cert"]).serial_number == serial
    ensure_certs(tmp_path, "10.0.0.5")
    cert2 = _load(paths["cert"])
    assert cert2.serial_number != serial
    assert _load(paths["ca_pem"]).serial_number == ca.serial_number
    san2 = cert2.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    assert ipaddress.ip_address("10.0.0.5") in san2.get_values_for_type(x509.IPAddress)
    der = paths["ca_der"].read_bytes()
    assert x509.load_der_x509_certificate(der).serial_number == ca.serial_number


def test_token_persistence_and_auth(tmp_path):
    p = tmp_path / "token"
    t1 = load_or_create_token(p)
    assert len(t1) >= 20 and load_or_create_token(p) == t1
    assert stat.S_IMODE(os.stat(p).st_mode) == 0o600
    t2 = load_or_create_token(p, regenerate=True)
    assert t2 != t1
    auth = TokenAuth(t2, require=True)
    assert auth.check_token(t2) and not auth.check_token(t1) and not auth.check_token(None)
    scope = {
        "client": ("192.168.1.9", 1),
        "headers": [(b"cookie", f"gobot_pair={cookie_value(t2)}".encode())],
    }
    assert auth.check_scope(scope)
    scope_bad = {"client": ("192.168.1.9", 1), "headers": [(b"cookie", b"gobot_pair=x")]}
    assert not auth.check_scope(scope_bad)
    assert auth.check_scope({"client": ("127.0.0.1", 1), "headers": []})
    assert is_loopback("::1") and not is_loopback("192.168.0.2")
    assert TokenAuth(None, require=True).check_scope(scope_bad)  # LAN mode off -> no token needed


def test_lan_ip_override(monkeypatch):
    monkeypatch.setenv("GOBOT_LAN_IP", "192.168.50.7")
    assert netinfo.lan_ip() == "192.168.50.7"
    assert netinfo._is_private("10.1.2.3") and not netinfo._is_private("8.8.8.8")


def test_config_rendering():
    hw8 = {"memory_gb": 8, "logical_cpus": 8}
    s = default_engine_settings(hw8, "metal")
    assert s["nn_cache_pow2"] == 17 and s["metal_mode"] == "gpu"
    cfg = render_config({**s, "metal_mode": "mux", "num_search_threads": 16, "nn_max_batch_size": 8}, "metal")
    assert "reportAnalysisWinratesAs = BLACK" in cfg
    assert "numNNServerThreadsPerModel = 4" in cfg and "metalDeviceToUseThread2 = 100" in cfg
    assert "numSearchThreadsPerAnalysisThread = 16" in cfg
    gpu = render_config(s, "metal")
    assert "metalDeviceToUseThread0 = 0" in gpu and "metalDeviceToUseThread1" not in gpu
    assert default_engine_settings({"memory_gb": 32}, "eigen")["nn_cache_pow2"] == 20
    assert "metal" not in render_config(default_engine_settings({}, "eigen"), "eigen").lower()


def test_benchmark_parsing_and_selection():
    line = "numSearchThreads =  8: 10 / 10 positions, visits/s = 413.22 nnEvals/s = 342.71 nnBatches/s = 87.10 avgBatchSize = 3.93 (19.4 secs)"
    m = LINE_RE.search(line)
    assert m and m.group(1) == "8" and float(m.group(4)) == pytest.approx(413.22)

    def res(mid, elo, vps, visits, rss_mb):
        return {
            "model_id": mid,
            "elo": elo,
            "mode": "gpu",
            "benchmark": {"ok": True, "best_visits_per_s": vps, "best_threads": 8, "peak_rss_mb": rss_mb},
            "analysis": {
                "peak_rss_mb": rss_mb,
                "queries": [
                    {"position": "19x19 middle game (move 120)", "preset": "standard", "visits": visits}
                ],
            },
        }

    gb = 2**30
    rs = [
        res("light", 13700, 400, 2400, 500),
        res("strong", 14167, 200, 1200, 800),
        res("max", 14602, 60, 360, 1500),
    ]
    sel = select(rs, 8 * gb)
    assert sel["model_id"] == "strong" and sel["light_model_id"] == "light"
    # if the strong one is too slow, fall back to the light one
    rs[1]["analysis"]["queries"][0]["visits"] = 500
    assert select(rs, 8 * gb)["model_id"] == "light"
    # memory limit excludes networks
    big = [res("max", 14602, 900, 5000, 4000), res("light", 13700, 400, 2400, 500)]
    assert select(big, 8 * gb)["model_id"] == "light"
    assert select([], 8 * gb)["model_id"] is None


def test_bench_positions_are_legal_games():
    ps = positions()
    assert [p.position.size for p in ps] == [19, 19, 19, 13, 9]
    assert len(ps[2].position.moves) == 220
    colors = [m.color for m in ps[1].position.moves]
    assert all(a != b for a, b in zip(colors, colors[1:], strict=False))


def test_verified_download(tmp_path):
    src = tmp_path / "src.bin.gz"
    src.write_bytes(b"network weights" * 100)
    digest = hashlib.sha256(src.read_bytes()).hexdigest()
    info = NetInfo(id="x", file="net.bin.gz", url=src.as_uri(), size=src.stat().st_size, sha256=digest)
    out = download(info, tmp_path / "models")
    assert out.read_bytes() == src.read_bytes()
    bad = NetInfo(id="y", file="bad.bin.gz", url=src.as_uri(), size=src.stat().st_size, sha256="0" * 64)
    with pytest.raises(RuntimeError, match="verification failed"):
        download(bad, tmp_path / "models")
    assert not (tmp_path / "models" / "bad.bin.gz").exists()
    assert not (tmp_path / "models" / "bad.bin.gz.part").exists()


def test_hardware_detect_keys():
    h = hardware.detect()
    for k in ("system", "machine", "logical_cpus", "memory_gb", "chip"):
        assert k in h
    assert "GB RAM" in hardware.summary(h)
